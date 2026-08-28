"""framework.core.schedule — 日程(Schedule)(纯逻辑,时间注入)

从旧实现(modules/memory/schedule.py)迁移:去掉对全局 timer 的依赖,
"当前计划/时间戳"等需要时间判断的方法由外部传入 now(datetime 或 Timer)。
"""
from typing import Dict, List, Optional, Tuple

from mavisframework.core.timer import Timer


class Schedule:
    def __init__(
        self,
        create=None,
        daily_schedule: Optional[List[dict]] = None,
        diversity: int = 5,
        max_try: int = 5,
    ):
        if create and isinstance(create, str):
            from mavisframework.core.timer import to_date

            create = to_date(create)
        self.create = create  # datetime(或 None)
        self.daily_schedule = daily_schedule or []
        self.diversity = diversity
        self.max_try = max_try

    def abstract(self, timer: Timer):
        def _to_stamp(plan):
            start, end = self.plan_stamps(plan, timer, time_format="%H:%M")
            return "{}~{}".format(start, end)

        des = {}
        for plan in self.daily_schedule:
            stamp = _to_stamp(plan)
            if plan.get("decompose"):
                s_info = {_to_stamp(p): p["describe"] for p in plan["decompose"]}
                des[stamp + ": " + plan["describe"]] = s_info
            else:
                des[stamp] = plan["describe"]
        return des

    def add_plan(self, describe: str, duration: int, decompose=None, start: Optional[int] = None) -> dict:
        # start 显式传入时以它为准(LLM 生成的 schedule 键是"该时段的真实起点",
        # 不能从 0 累加——否则 9:00 的活动会被平移到凌晨)
        if start is None:
            if self.daily_schedule:
                last_plan = self.daily_schedule[-1]
                start = last_plan["start"] + last_plan["duration"]
            else:
                start = 0
        self.daily_schedule.append(
            {
                "idx": len(self.daily_schedule),
                "describe": describe,
                "start": start,
                "duration": duration,
                "decompose": decompose or {},
            }
        )
        return self.daily_schedule[-1]

    def current_plan(self, timer: Timer) -> Tuple[dict, dict]:
        total_minute = timer.daily_duration()
        for plan in self.daily_schedule:
            if self.plan_stamps(plan, timer)[1] <= total_minute:
                continue
            for de_plan in plan.get("decompose", []):
                if self.plan_stamps(de_plan, timer)[1] <= total_minute:
                    continue
                return plan, de_plan
            return plan, plan
        last_plan = self.daily_schedule[-1]
        return last_plan, last_plan

    def plan_stamps(self, plan: dict, timer: Timer, time_format: str = None):
        def _to_date(minutes):
            return timer.daily_time(minutes).strftime(time_format)

        start, end = plan["start"], plan["start"] + plan["duration"]
        if time_format:
            start, end = _to_date(start), _to_date(end)
        return start, end

    def decompose(self, plan: dict) -> bool:
        d_plan = plan.get("decompose", {})
        if len(d_plan) > 0:
            return False
        describe = plan["describe"]
        if "sleep" not in describe and "bed" not in describe:
            return True
        if "睡" not in describe and "床" not in describe:
            return True
        if "sleeping" in describe or "asleep" in describe or "in bed" in describe:
            return False
        if "睡" in describe or "床" in describe:
            return False
        if "sleep" in describe or "bed" in describe:
            return plan["duration"] <= 60
        if "睡" in describe or "床" in describe:
            return plan["duration"] <= 60
        return True

    def scheduled(self, timer: Timer) -> bool:
        if not self.daily_schedule:
            return False
        if self.create is None:
            return False
        return timer.daily_format() == self.create.strftime("%A %B %d")

    def to_dict(self):
        return {
            "create": (
                self.create.strftime("%Y%m%d-%H:%M:%S") if self.create else None
            ),
            "daily_schedule": self.daily_schedule,
        }
