"""framework.core.agent_core — Agent 完整生命周期(组件注入式,纯逻辑)

从旧实现(modules/agent.py)迁移完整实现:依赖全部注入(timer/llm/记忆/空间/提示词),
不 import modules。Agent 每步(think)编排:
    移动 → 取计划 → (睡/醒) → 感知 → 反应(对话/等待) → 反思 → 输出路径

可插拔组件:
- llm     : framework.runtime.llm.LLMProvider(completion/is_available/get_summary)
- memory  : framework.core.associate.Associate(联想记忆)
- maze    : framework.scene.maze.Maze(空间/寻路)
- prompts : framework.prompt.Scratch(prompt_xxx 方法族)
- timer   : framework.core.timer.Timer(模拟时钟)
"""
import datetime
import math
import os
import random
import threading
from typing import Any, Dict, List, Optional, Tuple

from mavisframework.core.action import Action
from mavisframework.core.associate import Associate, Concept
from mavisframework.core.event import Event
from mavisframework.core.schedule import Schedule
from mavisframework.core.spatial import Spatial
from mavisframework.core.timer import Timer, daily_duration, to_date

# 对话对互斥:同一对角色不同时聊两场,但不同对可并行(支持多线对话)
# 用一个带锁的"活跃对话对"集合实现,线程安全
_chat_pairs_lock = threading.Lock()
_active_chat_pairs = set()  # frozenset({a, b})
# 全局并发对话上限:同一步内同时进行的对话场次不超过该值,
# 防止角色聚集时对话风暴(每场 8-10 次 LLM,并行时互相排队拖垮整步)
_CHAT_MAX_PARALLEL = 2
_chat_slots_lock = threading.Lock()
_active_chat_slots = 0


def _acquire_chat_pair(a: str, b: str) -> bool:
    """尝试占用对话对(a,b);成功返回 True"""
    pair = frozenset([a, b])
    with _chat_pairs_lock:
        if pair in _active_chat_pairs:
            return False
        _active_chat_pairs.add(pair)
        return True


def _release_chat_pair(a: str, b: str) -> None:
    pair = frozenset([a, b])
    with _chat_pairs_lock:
        _active_chat_pairs.discard(pair)

# 对话逐句回调:由外部(如 live 服务)设置,每生成一句话实时推送
chat_callback = None

# 事件重要性打分缓存(进程级):同一事件描述只调一次 LLM,后续复用
_POIGNANCY_CACHE = {}


class Agent:
    """框架版 Agent(与旧 modules.agent.Agent 行为对齐,依赖注入)"""

    def __init__(
        self,
        config: dict,
        maze,
        conversation: dict,
        timer: Optional[Timer] = None,
        llm=None,
        logger=None,
    ):
        self.name = config["name"]
        self.maze = maze
        self.conversation = conversation
        self._llm = llm
        self._timer = timer or Timer()
        if logger is None:
            from mavisframework.runtime.logger import get_logger

            logger = get_logger(f"agent.{self.name}", level="info")
        self.logger = logger

        # agent config
        self.percept_config = config["percept"]
        self.think_config = config["think"]
        self.chat_iter = config["chat_iter"]
        # 对话频率参数(可配置,缺省保持原行为)
        self.chat_cooldown_min = int(config.get("chat_cooldown_min", 20))
        self.chat_retry_prob = float(config.get("chat_retry_prob", 0.5))

        # memory
        self.spatial = Spatial(**config["spatial"])
        self.schedule = Schedule(**config["schedule"])
        self.associate = Associate(
            os.path.join(config["storage_root"], "associate"),
            config["associate"].get("embedding", {}),
            timer=self._timer,
            **{k: v for k, v in config["associate"].items() if k != "embedding"},
        )
        self.concepts, self.chats = [], config.get("chats", [])

        # 业务关系配置(relationships.json 注入):本角色与其他角色的关系列表
        self.relationships: List[dict] = config.get("relationships", [])
        # 角色类型:user(人)/ ai_tool(AI 工具角色)
        self.role_type: str = config.get("role_type", "user") or "user"
        # 是否全天在线不睡觉:ai_tool 天然如此;user 角色可通过配置开启
        # (全球时区场景:投资顾问分布在多个时区,模拟时间下需保持清醒)
        self.no_sleep: bool = bool(config.get("no_sleep", False)) or self.role_type == "ai_tool"
        # 全天在线角色:清洗续跑恢复的旧日程中残留的"睡觉"段
        # (旧存档在 no_sleep 引入前生成,日程里仍有睡觉计划;新日程不会产生。
        #  替换文本必须英文——睡前段(如 22:00)当天会被执行,中文会污染行动流。
        #  兼容:早期版本把 sleep 段替换成了中文"空闲待命",旧存档里已固化,
        #  此处一并清洗为英文,避免 resume 后行动描述仍中文)
        if self.no_sleep:
            for _plan in getattr(self.schedule, "daily_schedule", []) or []:
                _desc = str(_plan.get("describe", "") or "")
                if ("睡" in _desc or "sleep" in _desc.lower()
                        or "空闲待命" in _desc):
                    _plan["describe"] = "Idle standby, staying online, no user inquiries"
                    for _dp in _plan.get("decompose", []) or []:
                        _dp["describe"] = "Idle standby, staying online, no user inquiries"

        # prompt
        from mavisframework.prompt import Scratch

        # IVD 重构:goals 不再是 AI 属性(约束外部化到 governance.json)
        # scratch 配置不含 goals;价值倾向(value_tendency)由体验累积
        _scratch_cfg = dict(config.get("scratch", {}))
        self.scratch = Scratch(
            self.name, config["currently"], _scratch_cfg, timer=self._timer
        )
        self.scratch.agent = self  # 供 _tendency_desc 读取 value_tendency
        # 价值倾向初始化:
        # - 续跑(resume):优先恢复存档的 value_tendency(内化结果)
        # - 新开:initial_tendency(agent.json 人物底色)起步,随体验累积 α 渐降
        self.initial_tendency = dict(config.get("initial_tendency", {}) or {})
        _saved_status = config.get("status") or {}
        _saved_vt = _saved_status.get("value_tendency") or {}
        if _saved_vt:
            self.value_tendency = dict(_saved_vt)
        else:
            self.value_tendency = dict(self.initial_tendency)
        self._tendency_window = []
        # 续跑:恢复滑动窗口内容(渐进内化记忆;旧存档无该字段则从头累积)
        # 条目兼容两种格式:旧存档=裸 feedback dict;新存档={action,alignment,feedback}
        _saved_window = _saved_status.get("tendency_window")
        if isinstance(_saved_window, list) and _saved_window:
            _win_size = int(config.get("think", {}).get("tendency_window", 15))
            _restored = []
            for w in _saved_window:
                if not isinstance(w, dict):
                    continue
                if "feedback" in w and isinstance(w.get("feedback"), dict):
                    _restored.append({
                        "action": str(w.get("action", "")),
                        "alignment": dict(w.get("alignment", {}) or {}),
                        "feedback": dict(w["feedback"]),
                        "time": str(w.get("time", "") or ""),
                    })
                else:
                    # 旧格式:整条目即反馈 dict
                    _restored.append({
                        "action": "",
                        "alignment": {},
                        "feedback": dict(w),
                        "time": "",
                    })
            self._tendency_window = _restored[-_win_size:]
        # 续跑:体验计数近似恢复(α 惯性不从头算,避免 resume 后倾向剧烈波动)
        try:
            self._tendency_obs = int(_saved_status.get("tendency_window_n", 0) or 0)
        except (TypeError, ValueError):
            self._tendency_obs = 0
        self._tendency_steps = 0
        # V1:恢复"最后采样行动"——否则 resume 后第一次 observe_consequence
        # 因 None ≠ action 而误判"行动变化",立即采样一条多余体验,破坏
        # "resume 连续性"(行动未变应等 refresh_interval 才刷新)
        self._last_window_action = (
            self._tendency_window[-1].get("action", "") if self._tendency_window else None
        )
        self._window_size = int(config.get("think", {}).get("tendency_window", 15))
        # 记忆近因性衰减(think.tendency_decay_per_hour,缺省 0.6/模拟小时):
        # 权重 = decay_per_hour^age_hours —— 与 Generative Agents 论文的
        # recency 语义一致(检索打分含 0.99^小时,旧记忆长时间保留),衰减以
        # 模拟时间为尺,与采样频率解耦。早前实现按"条目序号"衰减(0.8^条):
        # 采样密集时(干预后 AI 频繁行动)窗口 1-2 模拟小时即被新反馈翻新,
        # 收敛过快,削弱"内化滞后"叙事(0.5h 跳变)。
        # 0.6/小时 → 半衰 ≈1.4h:干预前旧反馈随真实模拟时间流逝持续衰减,
        # 干预后约 3-5 模拟小时渐进爬升至新约束(小时级收敛,可见内化过程)。
        # 语义:age 小时数 = 当前模拟时间 − 条目记录时间(tendency_window 的
        # time 字段)。范围 (0,1),越接近 1 旧记忆保留越久、收敛越慢。
        try:
            self._tendency_decay_per_hour = float(
                config.get("think", {}).get("tendency_decay_per_hour", 0.6))
        except (TypeError, ValueError):
            self._tendency_decay_per_hour = 0.6
        if not (0.0 < self._tendency_decay_per_hour < 1.0):
            self._tendency_decay_per_hour = 0.6
        self._governance = None
        self._consequence_fn = None
        # status
        status = {"poignancy": 0}
        self.status = self._update_dict(status, _saved_status)
        self.plan = config.get("plan", {})

        # record
        self.last_record = self._timer.daily_duration()

        # action and events
        if "action" in config:
            self.action = Action.from_dict(config["action"])
            # 全天在线角色:恢复的 action 若仍是旧中文"空闲待命"(早期存档固化),
            # 替换为英文——否则 resume 后正执行的段(如凌晨/睡前)仍显示中文
            if self.no_sleep:
                for _ev in ("event", "obj_event"):
                    _d = getattr(self.action, _ev, None)
                    if _d is None:
                        continue
                    # Event 的描述存私有 _describe(describe 是构造参数非属性)
                    for _attr, _f in (("_describe", "describe"), ("object", "object"), ("emoji", "emoji")):
                        _txt = str(getattr(_d, _attr, "") or "")
                        if "空闲待命" in _txt or "无用户咨询" in _txt:
                            try:
                                setattr(_d, _attr, "Idle standby, staying online, no user inquiries")
                            except Exception:
                                pass
            tiles = self.maze.get_address_tiles(self.get_event().address)
            config["coord"] = random.choice(list(tiles))
        else:
            tile = self.maze.tile_at(config["coord"])
            address = tile.get_address("game_object", as_list=True)
            self.action = Action(
                Event(self.name, address=address),
                Event(address[-1], address=address),
            )

        # update maze
        self.coord, self.path = None, None
        self.move(config["coord"], config.get("path"))
        if self.coord is None:
            self.coord = config["coord"]

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    @staticmethod
    def _update_dict(base: dict, extra: dict) -> dict:
        base = {**base}
        for k, v in (extra or {}).items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k] = {**base[k], **v}
            else:
                base[k] = v
        return base

    def abstract(self):
        des = {
            "name": self.name,
            "currently": self.scratch.currently,
            "tile": self.maze.tile_at(self.coord).abstract(),
            "status": self.status,
            "concepts": {c.node_id: c.abstract() for c in self.concepts},
            "chats": self.chats,
            "action": self.action.abstract(self._timer.get_date()),
            "associate": self.associate.abstract(),
        }
        if self.schedule.scheduled(self._timer):
            des["schedule"] = self.schedule.abstract(self._timer)
        if self.llm_available():
            des["llm"] = self._llm.get_summary()
        return des

    def __str__(self):
        import json

        return json.dumps(self.abstract(), ensure_ascii=False, indent=2)

    def reset(self):
        if not self._llm:
            from mavisframework.runtime.llm import create_llm_provider

            self._llm = create_llm_provider(self.think_config["llm"])

    # ------------------------------------------------------------------
    # IVD:治理约束 + 价值倾向(value_tendency)
    # ------------------------------------------------------------------
    def attach_governance(self, governance, consequence_fn=None):
        """挂接制度约束层与后果反馈函数

        governance: Governance 实例(期望目标权重,来自 governance.json)
        consequence_fn: callable(agent, action_desc) -> {goal: feedback}
            返回"行动结果好坏"(客观后果反馈,非约束加权)
        """
        self._governance = governance
        self._consequence_fn = consequence_fn
        # 价值倾向(内化结果):已有值(如 resume 从存档恢复)则保留,
        # 否则起步=人物初始底色(若有),否则中性空
        if not getattr(self, "value_tendency", None):
            self.value_tendency = dict(self.initial_tendency or {})
        # 滑动窗口:按行动变化点记录逐目标对齐(最近 N 次)
        # 续跑(resume):窗口与体验计数从 checkpoint 恢复(__init__ 已还原),
        # 这里不再清空——否则 α 从 1.0 重新衰减,倾向被底色钳制,
        # 干预后曲线长时间"不动"(resume 连续性缺陷)
        self._tendency_window = getattr(self, "_tendency_window", [])
        self._tendency_obs = getattr(self, "_tendency_obs", 0)
        self._tendency_steps = 0
        # V1:保留已恢复的"最后采样行动"(resume 时 __init__ 已从窗口恢复),
        # 不重置为 None——否则 resume 首步误采样,破坏连续性
        self._last_window_action = getattr(self, "_last_window_action", None)
        self._window_size = int(getattr(self, "think_config", {}).get("tendency_window", 15))
        try:
            self._tendency_decay_per_hour = float(
                getattr(self, "think_config", {}).get("tendency_decay_per_hour", 0.6))
        except (TypeError, ValueError):
            self._tendency_decay_per_hour = 0.6
        if not (0.0 < self._tendency_decay_per_hour < 1.0):
            self._tendency_decay_per_hour = 0.6

    def get_constraints(self) -> dict:
        """当前治理约束(期望目标权重)"""
        gov = getattr(self, "_governance", None)
        if gov is not None:
            return gov.get_constraints(self.name)
        return {}

    def get_tendency(self) -> dict:
        """当前价值倾向(内化结果,注入 base_desc 用)"""
        return dict(getattr(self, "value_tendency", {}) or {})

    def goal_alignment(self, action: str) -> dict:
        """逐目标对齐度:行动 vs 约束期望(客观语义相似度,独立于权重)"""
        goals = self.get_constraints()
        if not goals or not action:
            return {}
        try:
            from mavisframework.runtime.goal_scorer import GoalScorer

            scorer = getattr(self, "_goal_scorer", None)
            if scorer is None:
                scorer = GoalScorer()
                self._goal_scorer = scorer
            return scorer.alignment(action, goals)
        except Exception:
            return {}

    def observe_consequence(self, action_desc: str):
        """后果反馈 → 更新倾向(滑动窗口,按行动变化点采样)

        机制:
        1. 客观后果(consequence_fn)给出各目标的"结果好坏"反馈
        2. 若行动变了,将本次逐目标反馈计入滑动窗口
        3. 窗口加权平均 → 与人物初始底色惯性混合 → 归一化 → value_tendency
        4. 惯性:α = max(0.1, 1 - obs/窗口容量),起步=人设底色,随体验累积渐降
           (过渡期自适应绑定记忆窗口大小,下限 0.1 保 10% 性格残余)
        5. 记录倾向变化轨迹(可审计)
        """
        fn = getattr(self, "_consequence_fn", None)
        if fn is None:
            return
        try:
            feedback = fn(self, action_desc)  # {goal: 反馈值(0-1)}
        except Exception:
            return
        if not feedback:
            return
        # 采样策略:行动变化点 + 周期性刷新
        # - 行动变了 → 立即计入(新体验)
        # - 行动未变但已持续 refresh_interval 步 → 也计入(持续做=持续强化)
        #   避免"长时间做同一件事时倾向完全静止"(曲线平的真凶)
        refresh_interval = int(getattr(self, "think_config", {}).get("tendency_refresh", 5) or 5)
        changed = action_desc != getattr(self, "_last_window_action", None)
        if not changed:
            self._tendency_steps = getattr(self, "_tendency_steps", 0) + 1
            if self._tendency_steps < refresh_interval:
                return
        self._tendency_steps = 0
        self._last_window_action = action_desc
        # 累计体验次数(独立于窗口截断,α 由此衰减)
        self._tendency_obs = getattr(self, "_tendency_obs", 0) + 1
        # 入窗口:记录"行动 + 对齐度 + 反馈"三件套(可解释性数据源)
        # - action  : 做了什么(行动描述,解释"这条体验因何而来")
        # - alignment: 行动对各目标的语义对齐度(为什么反馈偏向某目标)
        # - feedback : 客观后果反馈(相对优势 × 约束权重,驱动倾向的内化量)
        _align = {}
        try:
            _align = self.goal_alignment(action_desc) or {}
        except Exception:
            _align = {}
        # 记录模拟时间(窗口明细按时间从早到晚展示;旧存档无 time 字段则留空)
        _w_time = ""
        try:
            _w_time = self._timer.get_date("%Y%m%d-%H:%M")
        except Exception:
            _w_time = ""
        self._tendency_window.append({
            "action": action_desc,
            "alignment": dict(_align),
            "feedback": dict(feedback),
            "time": _w_time,
        })
        if len(self._tendency_window) > self._window_size:
            self._tendency_window.pop(0)
        # 加权平均——记忆近因性按"模拟时间"衰减(论文 recency 语义):
        #   权重 = decay_per_hour ^ age_hours
        #   age_hours = 当前模拟时间 − 条目记录的模拟时间(time 字段)
        # 与采样频率解耦:干预后新反馈再多,只要模拟时间没走够,旧记忆仍
        # 有分量 → 收敛是小时级渐进(而非 1-2h 内被密集新反馈翻新)。
        n = len(self._tendency_window)
        decay_ph = getattr(self, "_tendency_decay_per_hour", 0.6)
        # 当前模拟时间(绝对时间,用于跨天 age 计算)
        _now = None
        try:
            _now = self._timer.get_date()
        except Exception:
            _now = None
        # 各条目的 age 小时数(无 time 的旧条目:按窗口内位置估算年龄,
        # 最旧条目默认窗口跨度(如 15 条 × 平均 10min ≈ 2.5h),不精确但单调)
        ages = []
        for i, w in enumerate(self._tendency_window):
            t_str = str(w.get("time", "") or "")
            if t_str and _now is not None:
                try:
                    import datetime as _dt
                    t_entry = _dt.datetime.strptime(t_str, "%Y%m%d-%H:%M")
                    age_h = max(0.0, (_now - t_entry).total_seconds() / 3600.0)
                    ages.append(age_h)
                    continue
                except (ValueError, TypeError):
                    pass
            # 无 time/解析失败:按位置估算(越旧 age 越大),避免该条目权重异常高
            ages.append(float(n - i) * 0.25)  # 每 15min 一条的粗糙估计
        weights = [decay_ph ** a for a in ages]
        goals = set()
        for w in self._tendency_window:
            goals.update(w.get("feedback", w).keys())
        tendency = {}
        for g in goals:
            vals = [w.get("feedback", w).get(g, 0.0) for w in self._tendency_window]
            wsum = sum(weights) or 1.0
            tendency[g] = sum(v * wt for v, wt in zip(vals, weights)) / wsum
        # 人物底色惯性混合:起步=人设,体验接管,性格有残余(α 随累计体验衰减)
        # 自适应过渡期:α 的衰减分母 = 体验记忆窗口容量(self._window_size,
        # 即 think.tendency_window,默认 15)——"过渡期 ≈ 一趟装满体验记忆
        # 窗口所需的观测数"。窗口越小过渡越快,窗口越大越缓慢,与记忆容量
        # 一致,避免魔法数"8"。下限 0.1 保证性格 10% 残余(随体验收敛但不归零)
        base = self.initial_tendency or {}
        decay_total = max(1, int(getattr(self, "_window_size", 15) or 15))
        alpha = max(0.1, 1.0 - self._tendency_obs / decay_total)
        if base:
            all_goals = set(goals) | set(base.keys())
            for g in all_goals:
                tendency[g] = alpha * (base.get(g, 0.0)) + (1 - alpha) * tendency.get(g, 0.0)
        # 约束外目标剔除:制度删除的目标(专家干预后约束集缩小)应在内化中
        # 消退至 0,而非靠底色残余长期残留(否则倾向曲线"多出一条"不跟随治理)
        constraints = self.get_constraints()
        if constraints:
            tendency = {g: v for g, v in tendency.items() if g in constraints}
            # V2:同步清理窗口内被删目标的 feedback/alignment,避免"影子目标"
            # 残留在窗口里(滑出前仍影响加权平均,倾向曲线删除目标后仍显示尾巴)
            for _w in self._tendency_window:
                _fb = _w.get("feedback", {})
                if _fb:
                    _w["feedback"] = {g: v for g, v in _fb.items() if g in constraints}
                _al = _w.get("alignment", {})
                if _al:
                    _w["alignment"] = {g: v for g, v in _al.items() if g in constraints}
        # 归一化(总和=1)
        total = sum(tendency.values()) or 1.0
        self.value_tendency = {g: v / total for g, v in tendency.items()}
        # 审计:倾向变化轨迹(供 interventions/可审计链)
        self.status["value_tendency"] = dict(self.value_tendency)
        self.status["tendency_window_n"] = n
        # 审计:倾向过渡元信息——α(底色占比)与记忆窗口容量(bound 为衰减分母),
        # 让"倾向此刻过渡到几成"可解释(自适应过渡期替代魔法数后仍可追溯)
        self.status["tendency_meta"] = {
            "alpha": round(alpha, 4),
            "decay_total": decay_total,
            "obs": self._tendency_obs,
        }
        # 窗口内容随 checkpoint 持久化:resume 恢复后反馈逐条替换,
        # 干预后的收敛保持渐进(而非重启清窗 → 一步跳变)
        self.status["tendency_window"] = [
            {
                "action": w.get("action", ""),
                "alignment": dict(w.get("alignment", {}) or {}),
                "feedback": dict(w.get("feedback", w)),
                "time": str(w.get("time", "") or ""),
            }
            for w in self._tendency_window
        ]

    def completion(self, func_hint, *args, **kwargs):
        assert hasattr(
            self.scratch, "prompt_" + func_hint
        ), "Can not find func prompt_{} from scratch".format(func_hint)
        func = getattr(self.scratch, "prompt_" + func_hint)
        res = func(*args, **kwargs)._asdict()
        title, msg = "{}.{}".format(self.name, func_hint), {}
        if not self.llm_available():
            raise RuntimeError(
                "Agent {} 缺少可用 LLM:请先配置大模型"
                "(data/config.json 的 agent.think.llm,支持 Ollama / OpenAI 兼容 API)。"
                "框架运行必须有 LLM,不支持无 LLM 环境。".format(self.name)
            )
        self.logger.info("{} -> {}".format(self.name, func_hint))
        output = self._llm.completion(**res)
        msg = {"<PROMPT>": "\n" + res["prompt"] + "\n"}
        msg.update({"response": output})
        self.logger.debug(_block_msg(title, msg))
        return output

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def think(self, status, agents):
        events = self.move(status["coord"], status.get("path"))
        plan, _ = self.make_schedule()

        if (
            not self.no_sleep  # 全天在线角色(ai_tool / 配置 no_sleep)不睡觉
            and (plan["describe"] == "sleeping" or "睡" in plan["describe"])
            and self.is_awake()
        ):
            self.logger.info("{} is going to sleep...".format(self.name))
            address = self.spatial.find_address("睡觉", as_list=True)
            tiles = self.maze.get_address_tiles(address)
            coord = random.choice(list(tiles))
            events = self.move(coord)
            self.action = Action(
                Event(self.name, "正在", "睡觉", address=address, emoji="😴"),
                Event(
                    address[-1],
                    "被占用",
                    self.name,
                    address=address,
                    emoji="🛌",
                ),
                duration=plan["duration"],
                start=self._timer.daily_time(plan["start"]),
            )
        if self.is_awake():
            self.percept()
            self.make_plan(agents)
            self.reflect()
        else:
            if self.action.finished(self._timer.get_date()):
                self.action = self._determine_action()

        emojis = {}
        if self.action:
            emojis[self.name] = {"emoji": self.get_event().emoji, "coord": self.coord}
        for eve, coord in events.items():
            if eve.subject in agents:
                continue
            emojis[":".join(eve.address)] = {"emoji": eve.emoji, "coord": coord}
        self.plan = {
            "name": self.name,
            "path": self.find_path(agents),
            "emojis": emojis,
        }
        return self.plan

    def move(self, coord, path=None):
        events = {}

        def _update_tile(coord):
            tile = self.maze.tile_at(coord)
            if not self.action:
                return {}
            if not tile.update_events(self.get_event()):
                tile.add_event(self.get_event())
            obj_event = self.get_event(False)
            if obj_event:
                self.maze.update_obj(coord, obj_event)
            return {e: coord for e in tile.get_events()}

        if self.coord and self.coord != coord:
            tile = self.get_tile()
            tile.remove_events(subject=self.name)
            if tile.has_address("game_object"):
                addr = tile.get_address("game_object")
                self.maze.update_obj(
                    self.coord, Event(addr[-1], address=addr)
                )
            events.update({e: self.coord for e in tile.get_events()})
        if not path:
            events.update(_update_tile(coord))
        self.coord = coord
        self.path = path or []

        return events

    def make_schedule(self):
        just_created = False
        if not self.schedule.scheduled(self._timer):
            just_created = True
            self.logger.info("{} is making schedule...".format(self.name))
            # update currently
            if self.associate.index.nodes_num > 0:
                self.associate.cleanup_index()
                focus = [
                    f"{self.name} 在 {self._timer.daily_format_cn()} 的计划。",
                    f"在 {self.name} 的生活中，重要的近期事件。",
                ]
                retrieved = self.associate.retrieve_focus(focus)
                self.logger.info(
                    "{} retrieved {} concepts".format(self.name, len(retrieved))
                )
                if retrieved:
                    plan = self.completion("retrieve_plan", retrieved)
                    thought = self.completion("retrieve_thought", retrieved)
                    self.scratch.currently = self.completion(
                        "retrieve_currently", plan, thought
                    )
            # make init schedule
            self.schedule.create = self._timer.get_date()
            wake_up = self.completion("wake_up")
            init_schedule = self.completion("schedule_init", wake_up)
            # 全天在线角色强制无睡觉时段(wake_up=0):ai_tool 或配置 no_sleep
            if self.no_sleep:
                wake_up = 0
                # 日程来自业务配置(currently),框架中立:无业务描述时用中性默认
                init_schedule = [self.scratch.currently or "随时准备为用户服务"]
            # make daily schedule
            hours = [f"{i}:00" for i in range(24)]
            seed = [(h, "睡觉") for h in hours[:wake_up]]
            seed += [(h, "") for h in hours[wake_up:]]
            schedule = {}
            for _ in range(self.schedule.max_try):
                schedule = {h: s for h, s in seed[:wake_up]}
                schedule.update(
                    self.completion("schedule_daily", wake_up, init_schedule)
                )
                if len(set(schedule.values())) >= self.schedule.diversity:
                    break
            # 全天在线角色:凌晨(服务时段外)强制"空闲待命",防止 LLM 把
            # 主动活动排到无意义时段(模拟从业务时间开始,凌晨段永不被执行,
            # 排在那里的走访/协调会"消失");同时兜底覆盖任何残留"睡觉"段
            if self.no_sleep:
                service_start = int(
                    getattr(self, "think_config", {}).get("ai_service_start", 9) or 9
                )
                _covered = 0
                for hkey in list(schedule.keys()):
                    try:
                        hh = int(str(hkey).split(":")[0])
                    except (ValueError, IndexError):
                        self.logger.warning(
                            "schedule key '{}' 无法解析小时,跳过覆盖".format(hkey)
                        )
                        continue
                    desc = str(schedule.get(hkey) or "")
                    if hh < service_start or "睡" in desc or "sleep" in desc.lower():
                        schedule[hkey] = "空闲待命,保持在线,无用户咨询"
                        _covered += 1
                self.logger.info(
                    "no_sleep 日程覆盖: 覆盖 {} 段, 时段键样例 {} (service_start={})".format(
                        _covered, list(schedule.keys())[:6], service_start
                    )
                )

            def _to_duration(date_str):
                return daily_duration(to_date(date_str, "%H:%M"))

            schedule = {_to_duration(k): v for k, v in schedule.items()}
            starts = list(sorted(schedule.keys()))
            for idx, start in enumerate(starts):
                end = starts[idx + 1] if idx + 1 < len(starts) else 24 * 60
                # start 显式传入(LLM 键的真实起点),add_plan 不再从 0 累加
                self.schedule.add_plan(schedule[start], end - start, start=start)
            schedule_time = self._timer.time_format_cn(self.schedule.create)
            thought = "这是 {} 在 {} 的计划：{}".format(
                self.name, schedule_time, "；".join(init_schedule)
            )
            event = Event(
                self.name,
                "计划",
                schedule_time,
                describe=thought,
                address=self.get_tile().get_address(),
            )
            self._add_concept(
                "thought",
                event,
                expire=self.schedule.create + datetime.timedelta(days=30),
            )
        # decompose current plan(首轮刚建日程时跳过:先跑起来,当天后续再拆)
        plan, _ = self.schedule.current_plan(self._timer)
        if not just_created and self.schedule.decompose(plan):
            decompose_schedule = self.completion(
                "schedule_decompose", plan, self.schedule
            )
            decompose, start = [], plan["start"]
            for describe, duration in decompose_schedule:
                decompose.append(
                    {
                        "idx": len(decompose),
                        "describe": describe,
                        "start": start,
                        "duration": duration,
                    }
                )
                start += duration
            plan["decompose"] = decompose
        return self.schedule.current_plan(self._timer)

    def revise_schedule(self, event, start, duration):
        self.action = Action(event, start=start, duration=duration)
        plan, _ = self.schedule.current_plan(self._timer)
        if len(plan["decompose"]) > 0:
            plan["decompose"] = self.completion(
                "schedule_revise", self.action, self.schedule
            )

    def percept(self):
        scope = self.maze.get_scope(self.coord, self.percept_config)
        # add spatial memory
        for tile in scope:
            if tile.has_address("game_object"):
                self.spatial.add_leaf(tile.address)
        events, arena = {}, self.get_tile().get_address("arena")
        # gather events in scope
        for tile in scope:
            if not tile.events or tile.get_address("arena") != arena:
                continue
            dist = math.dist(tile.coord, self.coord)
            for event in tile.get_events():
                if dist < events.get(event, float("inf")):
                    events[event] = dist
        events = list(sorted(events.keys(), key=lambda k: events[k]))
        # get concepts
        self.concepts, valid_num = [], 0
        for idx, event in enumerate(events[: self.percept_config["att_bandwidth"]]):
            recent_nodes = (
                self.associate.retrieve_events() + self.associate.retrieve_chats()
            )
            recent_nodes = set(n.describe for n in recent_nodes)
            if event.get_describe() not in recent_nodes:
                if event.object == "idle" or event.object == "空闲":
                    node = Concept.from_event(
                        "idle_" + str(idx), "event", event, poignancy=1, timer=self._timer
                    )
                else:
                    valid_num += 1
                    node_type = "chat" if event.fit(self.name, "对话") else "event"
                    node = self._add_concept(node_type, event)
                    self.status["poignancy"] += node.poignancy
                self.concepts.append(node)
        self.concepts = [c for c in self.concepts if c.event.subject != self.name]
        self.logger.info(
            "{} percept {}/{} concepts".format(self.name, valid_num, len(self.concepts))
        )

    def make_plan(self, agents):
        if self._reaction(agents):
            return
        if self.path:
            return
        if self.action.finished(self._timer.get_date()):
            self.action = self._determine_action()

    # create action && object events
    def make_event(self, subject, describe, address):
        e_describe = describe.replace("(", "").replace(")", "").replace("<", "").replace(">", "")
        if e_describe.startswith(subject + "此时"):
            e_describe = e_describe[len(subject + "此时"):]
        if e_describe.startswith(subject):
            e_describe = e_describe[len(subject):]
        event = Event(
            subject, "此时", e_describe, describe=describe, address=address
        )
        return event

    def reflect(self):
        def _add_thought(thought, evidence=None):
            event = self.make_event(self.name, thought, self.get_tile().get_address())
            return self._add_concept("thought", event, filling=evidence)

        if self.status["poignancy"] < self.think_config["poignancy_max"]:
            return
        nodes = self.associate.retrieve_events() + self.associate.retrieve_thoughts()
        if not nodes:
            return
        self.logger.info(
            "{} reflect(P{}/{}) with {} concepts...".format(
                self.name,
                self.status["poignancy"],
                self.think_config["poignancy_max"],
                len(nodes),
            )
        )
        nodes = sorted(nodes, key=lambda n: n.access, reverse=True)[
            : self.associate.max_importance
        ]
        # summary thought
        focus = self.completion("reflect_focus", nodes, 3)
        retrieved = self.associate.retrieve_focus(focus, reduce_all=False)
        for r_nodes in retrieved.values():
            thoughts = self.completion("reflect_insights", r_nodes, 5)
            for thought, evidence in thoughts:
                _add_thought(thought, evidence)
        # summary chats
        if self.chats:
            recorded, evidence = set(), []
            for name, _ in self.chats:
                if name == self.name or name in recorded:
                    continue
                res = self.associate.retrieve_chats(name)
                if res and len(res) > 0:
                    node = res[-1]
                    evidence.append(node.node_id)
            thought = self.completion("reflect_chat_planing", self.chats)
            _add_thought(f"对于 {self.name} 的计划：{thought}", evidence)
            thought = self.completion("reflect_chat_memory", self.chats)
            _add_thought(f"{self.name} {thought}", evidence)
        self.status["poignancy"] = 0
        self.chats = []

    def find_path(self, agents):
        address = self.get_event().address
        if self.path:
            return self.path
        if address == self.get_tile().get_address():
            return []
        if address[0] == "<waiting>":
            return []
        if address[0] == "<persona>":
            target_tiles = self.maze.get_around(agents[address[1]].coord)
        else:
            target_tiles = self.maze.get_address_tiles(address)
        if tuple(self.coord) in target_tiles:
            return []

        # filter tile with self event
        def _ignore_target(t_coord):
            if list(t_coord) == list(self.coord):
                return True
            events = self.maze.tile_at(t_coord).get_events()
            if any(e.subject in agents for e in events):
                return True
            return False

        target_tiles = [t for t in target_tiles if not _ignore_target(t)]
        if not target_tiles:
            return []
        if len(target_tiles) >= 4:
            target_tiles = random.sample(target_tiles, 4)
        pathes = {t: self.maze.find_path(self.coord, t) for t in target_tiles}
        target = min(pathes, key=lambda p: len(pathes[p]))
        return pathes[target][1:]

    def _determine_action(self):
        self.logger.info("{} is determining action...".format(self.name))
        plan, de_plan = self.schedule.current_plan(self._timer)
        describes = [plan["describe"], de_plan["describe"]]

        # 行动定位缓存:同一计划段内目标地址稳定,避免每步重复调 LLM
        # 键 = 计划描述(计划变了自然换键,缓存自动失效)
        cache_key = (plan.get("idx"), de_plan.get("idx"), describes[0], describes[1])
        if not hasattr(self, "_action_cache"):
            self._action_cache = {}
        if cache_key in self._action_cache:
            address = self._action_cache[cache_key]
        else:
            address = self._resolve_action_address(describes)
            # 限长:超过 64 条清空(计划段数量有限,防内存无限增长)
            if len(self._action_cache) >= 64:
                self._action_cache.clear()
            self._action_cache[cache_key] = address

        # 价值反馈观测:计算行动对各目标的语义对齐度(不干预行为)
        # IVD 重构:约束是期望,不直接指挥 AI;这里只"观测+记录",
        # 供后果反馈(consequence)与倾向内化(value_tendency)使用。
        # 计划段缓存:同一行动描述的对齐度不重复算(embedding 也费时)
        action_desc = describes[-1]
        constraints = self.get_constraints()
        _align_key = (tuple(sorted(constraints.items())) if constraints else (), action_desc)
        if constraints and self._goal_scorer_available():
            if not hasattr(self, "_align_cache"):
                self._align_cache = {}
            if _align_key in self._align_cache:
                self.status["goal_alignment"] = self._align_cache[_align_key]
            else:
                self.status["goal_alignment"] = self.goal_alignment(action_desc)
                if len(self._align_cache) >= 64:
                    self._align_cache.clear()
                self._align_cache[_align_key] = self.status["goal_alignment"]
        else:
            self.status["goal_alignment"] = {}

        event = self.make_event(self.name, action_desc, address)
        # describe_object 计划段缓存:同一行动描述只调一次 LLM(行动稳定时复用)
        _desc_key = (address[-1] if address else "", action_desc)
        if not hasattr(self, "_describe_cache"):
            self._describe_cache = {}
        if _desc_key in self._describe_cache:
            obj_describe = self._describe_cache[_desc_key]
        else:
            obj_describe = self.completion("describe_object", address[-1], action_desc)
            self._describe_cache[_desc_key] = obj_describe
        obj_event = self.make_event(address[-1], obj_describe, address)

        event.emoji = f"{de_plan['describe']}"

        return Action(
            event,
            obj_event,
            duration=de_plan["duration"],
            start=self._timer.daily_time(de_plan["start"]),
        )

    def _goal_scorer_available(self) -> bool:
        """GoalScorer 是否可用(embedding 可达)"""
        try:
            from mavisframework.runtime.goal_scorer import GoalScorer

            scorer = getattr(self, "_goal_scorer", None)
            if scorer is None:
                scorer = GoalScorer()
                self._goal_scorer = scorer
            # 探活:embed 一个空串看是否返回 None
            return scorer.embed("probe") is not None
        except Exception:
            return False

    def _resolve_action_address(self, describes):
        """解析行动目标地址(定位链:空间匹配优先,LLM 兜底)"""
        address = self.spatial.find_address(describes[0], as_list=True)
        if address:
            return address
        tile = self.get_tile()
        kwargs = {
            "describes": describes,
            "spatial": self.spatial,
            "address": tile.get_address("world", as_list=True),
        }
        kwargs["address"].append(
            self.completion("determine_sector", **kwargs, tile=tile)
        )
        arenas = self.spatial.get_leaves(kwargs["address"])
        if len(arenas) == 1:
            kwargs["address"].append(arenas[0])
        else:
            kwargs["address"].append(self.completion("determine_arena", **kwargs))
        objs = self.spatial.get_leaves(kwargs["address"])
        if len(objs) == 1:
            kwargs["address"].append(objs[0])
        elif len(objs) > 1:
            kwargs["address"].append(self.completion("determine_object", **kwargs))
        return kwargs["address"]

    def _reaction(self, agents=None, ignore_words=None):
        focus = None
        ignore_words = ignore_words or ["空闲"]

        def _focus(concept):
            return concept.event.subject in agents

        def _ignore(concept):
            return any(i in concept.describe for i in ignore_words)

        if agents:
            priority = [i for i in self.concepts if _focus(i)]
            if priority:
                focus = random.choice(priority)
        if not focus:
            priority = [i for i in self.concepts if not _ignore(i)]
            if priority:
                focus = random.choice(priority)
        if not focus or focus.event.subject not in agents:
            return
        other, focus = agents[focus.event.subject], self.associate.get_relation(focus)

        if self._chat_with(other, focus):
            return True
        if self._wait_other(other, focus):
            return True
        return False

    def _skip_react(self, other):
        def _skip(event):
            if not event.address or "sleeping" in event.get_describe(False) or "睡觉" in event.get_describe(False):
                return True
            if event.predicate == "待开始":
                return True
            return False

        if self._timer.daily_duration(mode="hour") >= 23:
            return True
        if _skip(self.get_event()) or _skip(other.get_event()):
            return True
        return False

    def _chat_with(self, other, focus):
        # 全局并发槽位:限制同时进行的对话场次(防对话风暴)
        global _active_chat_slots
        with _chat_slots_lock:
            if _active_chat_slots >= _CHAT_MAX_PARALLEL:
                return False  # 本步对话已满,跳过(世界继续运转,下步再聊)
            _active_chat_slots += 1
        try:
            # 对话对互斥:同一对不同时聊两场,但不同对可并行(支持多线对话)
            if not _acquire_chat_pair(self.name, other.name):
                return False
            try:
                return self._chat_with_locked(other, focus)
            finally:
                _release_chat_pair(self.name, other.name)
        finally:
            with _chat_slots_lock:
                _active_chat_slots -= 1

    def _chat_with_locked(self, other, focus):
        if len(self.schedule.daily_schedule) < 1 or len(other.schedule.daily_schedule) < 1:
            # initializing
            return False
        if self._skip_react(other):
            return False
        if other.path:
            return False
        if self.get_event().fit(predicate="对话") or other.get_event().fit(predicate="对话"):
            return False

        chats = self.associate.retrieve_chats(other.name)
        if chats:
            delta = self._timer.get_delta(chats[0].create)
            self.logger.info(
                "retrieved chat between {} and {}({} min):\n{}".format(
                    self.name, other.name, delta, chats[0]
                )
            )
            if delta < self.chat_cooldown_min:
                return False

        if not self.completion("decide_chat", self, other, focus, chats):
            # 提高对话频率:即使 LLM 不倾向,也有一定概率继续尝试(可配置)
            # AI 工具角色(自己或对方)概率更高,鼓励与人交流
            retry_prob = self.chat_retry_prob
            if self.role_type == "ai_tool" or getattr(other, "role_type", "") == "ai_tool":
                retry_prob = max(retry_prob, 0.9)
            if random.random() < retry_prob:
                return False

        self.logger.info("{} decides chat with {}".format(self.name, other.name))
        start, chats = self._timer.get_date(), []
        relations = [
            self.completion("summarize_relation", self, other.name),
            other.completion("summarize_relation", other, self.name),
        ]

        for i in range(self.chat_iter):
            text = self.completion(
                "generate_chat", self, other, relations[0], chats
            )
            # 逐句实时推送(不用等整段对话完成)
            if chat_callback:
                chat_callback(self.name, text)

            if i > 0:
                # 对于发起对话的Agent，从第2轮对话开始，检查是否出现"复读"现象
                end = self.completion(
                    "generate_chat_check_repeat", self, chats, text
                )
                if end:
                    break

                # 对于发起对话的Agent，从第2轮对话开始，检查话题是否结束
                chats.append((self.name, text))
                end = self.completion(
                    "decide_chat_terminate", self, other, chats
                )
                if end:
                    break
            else:
                chats.append((self.name, text))

            text = other.completion(
                "generate_chat", other, self, relations[1], chats
            )
            # 逐句实时推送
            if chat_callback:
                chat_callback(other.name, text)
            if i > 0:
                # 对于响应对话的Agent，从第2轮开始，检查是否出现"复读"现象
                end = self.completion(
                    "generate_chat_check_repeat", other, chats, text
                )
                if end:
                    break

            chats.append((other.name, text))

            # 对于响应对话的Agent，从第1轮开始，检查话题是否结束
            end = other.completion(
                "decide_chat_terminate", other, self, chats
            )
            if end:
                break

        key = self._timer.get_date("%Y%m%d-%H:%M")
        if key not in self.conversation.keys():
            self.conversation[key] = []
        self.conversation[key].append({f"{self.name} -> {other.name} @ {'，'.join(self.get_event().address)}": chats})

        self.logger.info(
            "{} and {} has chats\n  {}".format(
                self.name,
                other.name,
                "\n  ".join(["{}: {}".format(n, c) for n, c in chats]),
            )
        )
        chat_summary = self.completion("summarize_chats", chats)
        duration = int(sum([len(c[1]) for c in chats]) / 240)
        self.schedule_chat(
            chats, chat_summary, start, duration, other
        )
        other.schedule_chat(chats, chat_summary, start, duration, self)
        return True

    def _wait_other(self, other, focus):
        if self._skip_react(other):
            return False
        if not self.path:
            return False
        if self.get_event().address != other.get_tile().get_address():
            return False
        if not self.completion("decide_wait", self, other, focus):
            return False
        self.logger.info("{} decides wait to {}".format(self.name, other.name))
        start = self._timer.get_date()
        t = other.action.end - start
        duration = int(t.total_seconds() / 60)
        event = Event(
            self.name,
            "waiting to start",
            self.get_event().get_describe(False),
            address=self.get_event().address,
            emoji="⌛",
        )
        self.revise_schedule(event, start, duration)

    def schedule_chat(self, chats, chats_summary, start, duration, other, address=None):
        self.chats.extend(chats)
        event = Event(
            self.name,
            "对话",
            other.name,
            describe=chats_summary,
            address=address or self.get_tile().get_address(),
            emoji="💬",
        )
        self.revise_schedule(event, start, duration)

    def _add_concept(
        self,
        e_type,
        event,
        create=None,
        expire=None,
        filling=None,
    ):
        if event.fit(None, "is", "idle"):
            poignancy = 1
        elif event.fit(None, "此时", "空闲"):
            poignancy = 1
        elif e_type == "chat":
            poignancy = self.completion("poignancy_chat", event)
        else:
            # 事件级缓存:同一事件描述全局只调一次 LLM 打重要性分,
            # 后续感知到相同事件直接复用(大幅减少 LLM 调用,行为语义不变)
            desc = str(event)
            cached = _POIGNANCY_CACHE.get(desc)
            if cached is not None:
                poignancy = cached
            else:
                poignancy = self.completion("poignancy_event", event)
                _POIGNANCY_CACHE[desc] = poignancy
        self.logger.debug("{} add associate {}".format(self.name, event))
        return self.associate.add_node(
            e_type,
            event,
            poignancy,
            create=create,
            expire=expire,
            filling=filling,
        )

    def get_tile(self):
        return self.maze.tile_at(self.coord)

    def inject_story_event(self, event: dict):
        """注入剧情事件( story.json 的环境危机 )到本角色记忆

        event: {"id","time","event_type","content","targets","expected","importance"}
        事件作为高重要性记忆写入联想记忆,影响后续检索/对话/反思。
        重要性由配置方在 story.json 的 importance 字段指定(缺省 10),
        直接落库不走 LLM 打分(剧情是业务设定的事件,重要性是业务决策)。
        """
        content = event.get("content", "")
        if not content:
            return
        importance = int(event.get("importance", 10) or 10)
        ev = Event(
            "环境",
            "事件",
            event.get("event_type", "突发"),
            describe=f"{content}",
            address=self.get_tile().get_address(),
        )
        try:
            node = self.associate.add_node("event", ev, poignancy=importance)
            self.status["poignancy"] += importance
            self.logger.info(
                "{} injected story {} (P{}): {}".format(
                    self.name, event.get("id"), importance, content[:40]
                )
            )
            return node
        except Exception as e:
            self.logger.warning("story inject failed: {}".format(e))
            return None

    def recent_story_events(self, topk: int = 2) -> List[str]:
        """最近注入的剧情事件描述(供对话/思考检索焦点使用)

        从联想记忆中找 subject="环境" 的事件节点(剧情注入写入的),
        按重要性降序取最近 topk 条,返回描述文本列表。
        """
        try:
            nodes = self.associate.retrieve_events()
            story_nodes = [
                n for n in nodes if getattr(n, "event", None)
                and n.event.subject == "环境"
            ]
            story_nodes.sort(key=lambda n: n.poignancy, reverse=True)
            return [n.describe for n in story_nodes[:topk]]
        except Exception as e:
            self.logger.warning("recent_story_events failed: {}".format(e))
            return []

    def get_event(self, as_act=True):
        return self.action.event if as_act else self.action.obj_event

    def is_awake(self):
        # 全天在线角色(ai_tool / 配置 no_sleep)始终清醒
        if self.no_sleep:
            return True
        if not self.action:
            return True
        if self.get_event().fit(self.name, "is", "sleeping"):
            return False
        if self.get_event().fit(self.name, "正在", "睡觉"):
            return False
        return True

    def llm_available(self):
        if not self._llm:
            return False
        return self._llm.is_available()

    def to_dict(self, with_action=True):
        info = {
            "status": self.status,
            "schedule": self.schedule.to_dict(),
            "associate": self.associate.to_dict(),
            "chats": self.chats,
            "currently": self.scratch.currently,
        }
        if with_action:
            info.update({"action": self.action.to_dict()})
        return info


def _block_msg(title: str, msg: dict) -> str:
    lines = [title]
    for k, v in msg.items():
        lines.append("{}: {}".format(k, v))
    return "\n".join(lines)
