"""framework.runtime.logger — 统一日志(纯标准库,无全局状态)

替代旧 modules/utils/log.py:不依赖全局 timer,时间戳用墙钟;
提供 get_logger 工厂(控制台 + 可选文件),框架内所有模块统一使用。
级别:debug < info < warning < error。
"""
import logging
import os
import sys
from typing import Optional


class _ColorFormatter(logging.Formatter):
    """控制台彩色输出(可选),文件输出不带色"""

    COLORS = {
        "DEBUG": "\033[90m",     # gray
        "INFO": "\033[92m",      # green
        "WARNING": "\033[93m",   # yellow
        "ERROR": "\033[91m",     # red
    }
    RESET = "\033[00m"

    def __init__(self, fmt: str, use_color: bool = False):
        super().__init__(fmt)
        self.use_color = use_color

    def format(self, record):
        msg = super().format(record)
        if self.use_color:
            color = self.COLORS.get(record.levelname, "")
            return f"{color}{msg}{self.RESET}"
        return msg


def _parse_level(level) -> int:
    if isinstance(level, int):
        return level
    table = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    return table.get(str(level).lower(), logging.INFO)


def get_logger(name: str = "framework", level="info", log_file: str = "",
               use_color: bool = True) -> logging.Logger:
    """获取统一 logger

    - name     : 模块名(如 "agent" / "simulator"),用于区分
    - level    : debug/info/warning/error
    - log_file : 非空则同时写入该文件
    - use_color: 控制台是否彩色
    """
    logger = logging.getLogger(f"framework.{name}")
    logger.setLevel(_parse_level(level))
    logger.propagate = False

    if not logger.handlers:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(
            _ColorFormatter(
                "[%(asctime)s][%(levelname)s][%(name)s] %(message)s",
                use_color=use_color,
            )
        )
        logger.addHandler(console)

        if log_file:
            os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(
                logging.Formatter(
                    "[%(asctime)s][%(levelname)s][%(name)s] %(message)s"
                )
            )
            logger.addHandler(fh)

    return logger
