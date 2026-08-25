"""framework.core.action — 行动(Action)(纯逻辑,时间注入)

从旧实现(modules/memory/action.py)迁移:去掉对全局 timer 的依赖,
时间由外部 Timer 传入(每次判断时用传入的 now,不读全局时钟)。
"""
import datetime
from typing import Optional

from mavisframework.core.event import Event


class Action:
    def __init__(
        self,
        event: Event,
        obj_event: Optional[Event] = None,
        start: Optional[datetime.datetime] = None,
        duration: int = 0,
    ):
        self.event = event
        self.obj_event = obj_event
        self.start = start or datetime.datetime.now()
        self.duration = duration
        self.end = self.start + datetime.timedelta(minutes=self.duration)

    def abstract(self, now: Optional[datetime.datetime] = None):
        now = now or datetime.datetime.now()
        status = "{} [{}~{}]".format(
            "已完成" if self.finished(now) else "进行中",
            self.start.strftime("%Y%m%d-%H:%M"),
            self.end.strftime("%Y%m%d-%H:%M"),
        )
        info = {"status": status, "event": str(self.event)}
        if self.obj_event:
            info["object"] = str(self.obj_event)
        return info

    def finished(self, now: Optional[datetime.datetime] = None) -> bool:
        now = now or datetime.datetime.now()
        if not self.duration:
            return True
        if not self.event.address:
            return True
        return now > self.end

    def to_dict(self):
        return {
            "event": self.event.to_dict(),
            "obj_event": self.obj_event.to_dict() if self.obj_event else None,
            "start": self.start.strftime("%Y%m%d-%H:%M:%S"),
            "duration": self.duration,
        }

    @classmethod
    def from_dict(cls, config):
        config["event"] = Event.from_dict(config["event"])
        if config.get("obj_event"):
            config["obj_event"] = Event.from_dict(config["obj_event"])
        config["start"] = datetime.datetime.strptime(
            config["start"], "%Y%m%d-%H:%M:%S"
        )
        return cls(**config)
