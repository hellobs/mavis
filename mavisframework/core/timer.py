"""framework.core.timer — 模拟时钟(纯逻辑,可注入,零全局状态)

从 modules/utils/timer.py 迁移:去掉 GenerativeAgentsMap 全局单例,
Timer 由外部创建并注入(Simulator / Game 持有),便于并发与测试。
"""
import datetime
from typing import Optional


def to_date(date_str: str, date_format: str = "%Y%m%d-%H:%M:%S") -> datetime.datetime:
    if date_format == "%H:%M" and date_str.startswith("24:"):
        date_str = date_str.replace("24:", "0:")
    return datetime.datetime.strptime(date_str, date_format)


def daily_duration(date: datetime.datetime, mode: str = "minute"):
    duration = date.hour % 24
    if mode == "hour":
        return duration
    duration = duration * 60 + date.minute
    if mode == "minute":
        return duration
    return datetime.timedelta(minutes=duration)


class Timer:
    """模拟时钟:持有当前时间,支持 forward 推进与格式化输出"""

    def __init__(self, start: Optional[str] = None):
        self._mode = "on_time"
        if start:
            d_format = "%Y%m%d-%H:%M" if "-" in start else "%H:%M"
            self._offset = to_date(start, d_format)
        else:
            self._offset = datetime.datetime.now()

    def forward(self, offset: int):
        self._offset += datetime.timedelta(minutes=offset)

    def get_date(self, date_format: str = ""):
        date = self._offset
        if date_format:
            return date.strftime(date_format)
        return date

    def get_delta(self, start, end=None, mode="minute"):
        end = end or self.get_date()
        seconds = (end - start).total_seconds()
        if mode == "second":
            return seconds
        if mode == "minute":
            return round(seconds / 60)
        if mode == "hour":
            return round(seconds / 3600)
        return end - start

    def daily_format(self):
        return self.get_date("%A %B %d")

    def get_weekday(self, t: datetime.datetime) -> str:
        weekday_dict = {
            0: "星期一",
            1: "星期二",
            2: "星期三",
            3: "星期四",
            4: "星期五",
            5: "星期六",
            6: "星期日",
        }
        return weekday_dict[t.weekday()]

    def daily_format_cn(self) -> str:
        weekday = self.get_weekday(self.get_date())
        date = self.get_date("%Y年%m月%d日")
        return f"{date}（{weekday}）"

    def time_format_cn(self, t: datetime.datetime) -> str:
        weekday = self.get_weekday(t)
        date = t.strftime("%Y年%m月%d日")
        time = t.strftime("%H:%M")
        return f"{date}（{weekday}）{time}"

    def daily_duration(self, mode: str = "minute"):
        return daily_duration(self.get_date(), mode)

    def daily_time(self, duration: int) -> datetime.datetime:
        base = self.get_date().replace(hour=0, minute=0, second=0, microsecond=0)
        return base + datetime.timedelta(minutes=duration)

    @property
    def mode(self):
        return self._mode
