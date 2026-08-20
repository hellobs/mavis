# -*- coding: utf-8 -*-
"""通用思维导图生成器(PIL,支持中文)

输入:嵌套的 (label, children) 树
布局:根在左侧,逐层向右展开;自动测量文本宽度,计算画布尺寸。
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "C:/Windows/Fonts/msyh.ttc"
FONT_BOLD = "C:/Windows/Fonts/msyhbd.ttc"
OUT_DIR = "docs"

# 配色
COLORS = {
    "root":   ("#1a73e8", "#ffffff"),  # (填充, 文字)
    "level1": ("#e8f0fe", "#174ea6"),
    "level2": ("#f1f3f4", "#202124"),
    "level3": ("#f8f9fa", "#5f6368"),
    "level4": ("#f8f9fa", "#80868b"),
    "line":   "#bdc1c6",
}
PAD_X, PAD_Y = 18, 12
LEVEL_GAP = 60
NODE_GAP = 14


class Node:
    def __init__(self, label, children=None, level=0):
        self.label = label
        self.children = children or []
        self.level = level
        self.w = 0
        self.h = 0
        self.x = 0
        self.y = 0
        self.text = ""


def _split_label(label, font, max_w):
    """把长文本按宽度折行"""
    lines = []
    for seg in str(label).split("\n"):
        cur = ""
        for ch in seg:
            if font.getlength(cur + ch) > max_w and cur:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        lines.append(cur)
    return lines


def build_tree(data, level=0):
    if isinstance(data, str):
        return Node(data, level=level)
    label = data[0]
    children = [build_tree(c, level + 1) for c in data[1:]] if len(data) > 1 else []
    return Node(label, children, level)


def measure(node, font, max_w):
    lines = _split_label(node.label, font, max_w)
    node.text = "\n".join(lines)
    node.w = min(max(font.getlength(l) for l in lines), max_w) + PAD_X * 2
    node.h = len(lines) * (font.size + 6) + PAD_Y * 2
    for c in node.children:
        measure(c, font, max_w)
    node.h = max(node.h, sum(c.h for c in node.children) + NODE_GAP * (len(node.children) - 1))


def layout(node, x, y):
    node.x, node.y = x, y
    if not node.children:
        return
    cy = y + node.h / 2
    total_h = sum(c.h for c in node.children) + NODE_GAP * (len(node.children) - 1)
    top = cy - total_h / 2
    for c in node.children:
        layout(c, x + node.w + LEVEL_GAP, top)
        top += c.h + NODE_GAP


def draw_tree(draw, node, fonts, colors):
    fill, txt = colors.get(node.level, colors["level3"])
    font = fonts[min(node.level, 1)]  # level0/1 用粗体,其余普通
    # 节点背景圆角矩形
    draw.rounded_rectangle(
        [node.x, node.y, node.x + node.w, node.y + node.h],
        radius=8, fill=fill, outline=colors["line"], width=1,
    )
    # 文本(垂直居中)
    lines = node.text.split("\n")
    line_h = font.size + 6
    ty = node.y + (node.h - len(lines) * line_h) / 2
    for l in lines:
        tw = font.getlength(l)
        draw.text((node.x + (node.w - tw) / 2, ty), l, font=font, fill=txt)
        ty += line_h
    # 连线
    for c in node.children:
        draw.line(
            [node.x + node.w, node.y + node.h / 2, c.x, c.y + c.h / 2],
            fill=colors["line"], width=2,
        )
        draw_tree(draw, c, fonts, colors)


def render_mindmap(data, out_name, max_w=520):
    font_normal = ImageFont.truetype(FONT_PATH, 22)
    font_bold = ImageFont.truetype(FONT_BOLD, 24)
    fonts = {0: font_bold, 1: font_bold, 2: font_normal, 3: font_normal, 4: font_normal}

    root = build_tree(data)
    measure(root, font_normal, max_w)
    layout(root, 30, 30)

    # 画布尺寸
    def tree_w(node):
        return node.x + node.w + 30

    def tree_h(node):
        return node.y + node.h + 30

    def max_w_t(node):
        vals = [tree_w(node)] + [max_w_t(c) for c in node.children]
        return max(vals)

    def max_h_t(node):
        vals = [tree_h(node)] + [max_h_t(c) for c in node.children]
        return max(vals)

    W, H = int(max_w_t(root)), int(max_h_t(root))
    img = Image.new("RGB", (W, H), "#ffffff")
    draw = ImageDraw.Draw(img)
    draw_tree(draw, root, fonts, COLORS)

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, out_name)
    img.save(out)
    print(f"saved: {out} ({W}x{H})")


if __name__ == "__main__":
    pass
