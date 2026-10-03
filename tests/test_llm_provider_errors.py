# -*- coding: utf-8 -*-
"""LLM provider 的错误可见性守卫(2026-10-03)。

这个文件钉住三个曾经**静默**的行为 —— 它们都不是"功能缺失",是"出事了没人知道":

1. **未知参数被静默吞掉**:此前 `completion(..., max_tokens=512)` 会在
   `_completion()` 抛 `TypeError`,被 `except Exception` 吞掉 → `sleep(5)` × retry
   → 交一个 `failsafe`。调用方看到的是"正常返回了一个兜底值",完全看不出自己
   传错了参数。现在 `max_tokens` 真的可用,拼错的名字则第一次就响亮报错。
2. **上游状态码被丢掉**:此前两个 provider 都是 `response.json()` 直接解,
   HTTP 500 的错误页/网关 HTML 会报成"Expecting value",真正的原因(上游挂了)
   整个消失。现在非 2xx 抛 `UpstreamHTTPError` 并带状态码。
3. **编程错误也退避重试**:重试对 TypeError 没有意义,却要白等一整轮。

守的是项目自己的铁律:**不允许静默** —— "出事后谁能知道?"。
"""
import pytest

from mavisframework.runtime.llm_providers import (
    OllamaProvider, OpenAIProvider, UpstreamHTTPError, _BaseProvider,
)

_CFG = {"base_url": "http://127.0.0.1:1", "model": "dummy", "concurrency": 1}


class _Resp:
    """最小 response 替身(只带 provider 用到的三个属性)。"""

    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {
            "choices": [{"message": {"content": "ok"}}]}
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


class _Probe(_BaseProvider):
    """记录 `_chat` 收到的参数,并可指定抛什么异常。"""

    def __init__(self, exc=None, cfg=None):
        super().__init__(dict(cfg or _CFG))
        self.calls = []
        self.exc = exc
        self.status = 200

    def _chat(self, messages, temperature, response_format=None, max_tokens=None):
        self.calls.append({"temperature": temperature, "max_tokens": max_tokens})
        if self.exc is not None:
            raise self.exc
        return "ok"


# ---------------------------------------------------------------- 1. max_tokens
class TestMaxTokens:
    def test_max_tokens_reaches_provider(self):
        """`max_tokens` 必须真的传下去(此前根本收不到,传了就 TypeError)。"""
        p = _Probe()
        p._completion("hi", None, max_tokens=512)
        assert p.calls[0]["max_tokens"] == 512

    def test_default_sends_nothing(self):
        """默认 None = 不下发该参数,保持上游自己的默认值。

        这一点很关键:直接写死一个数字会让原本不受限的输出被截断,
        那比"传不进去"更糟 —— 是会悄悄改变既有行为的回归。
        """
        p = _Probe()
        p._completion("hi", None)
        assert p.calls[0]["max_tokens"] is None

    def test_unknown_kwarg_raises_immediately(self):
        """拼错的参数名要第一次就炸,且**不许重试**(重试对 TypeError 无意义)。"""
        p = _Probe()
        with pytest.raises(TypeError) as ei:
            p.completion("hi", retry=5, failsafe="SENTINEL", max_token=512)
        assert "max_token" in str(ei.value)      # 报错里要点名是哪个参数
        assert p.calls == []                       # 下游一次都没被调用
        assert "SENTINEL" not in str(ei.value)


# ------------------------------------------------------- 2. 上游状态码不能丢
class TestUpstreamStatus:
    @pytest.mark.parametrize("code", [400, 429, 500, 502, 503])
    def test_http_error_is_retried_then_reported(self, code):
        """上游 5xx/4xx 属可重试:耗尽重试后按 `raise_on_error` 决定给什么。

        关键不是"抛不抛",而是**状态码没有在重试过程中丢光** —— 默认路径交
        failsafe(原行为),但日志里必须留得下 500 这个事实。
        """
        p = _Probe(exc=UpstreamHTTPError(code, "upstream exploded"))
        assert p.completion("hi", retry=2, failsafe="FB", backoff=0) == "FB"
        assert len(p.calls) == 2, "可重试错误应该真的重试了"

        p2 = _Probe(exc=UpstreamHTTPError(code, "upstream exploded"))
        with pytest.raises(UpstreamHTTPError) as ei:
            p2.completion("hi", retry=1, failsafe="FB", backoff=0, raise_on_error=True)
        assert ei.value.status_code == code
        assert str(code) in str(ei.value)

    def test_http_error_is_retryable_not_a_bug(self):
        """上游 5xx 该退避重试(临时故障),不该被当成编程错误立刻抛。"""
        assert not issubclass(UpstreamHTTPError, TypeError)

    def test_raise_for_status_checks_code(self):
        from mavisframework.runtime.llm_providers import _raise_for_status
        with pytest.raises(UpstreamHTTPError) as ei:
            _raise_for_status(_Resp(status_code=500, text="<html>502</html>"))
        assert ei.value.status_code == 500
        assert "502" in str(ei.value)          # 响应体片段带上了

    def test_raise_for_status_passes_2xx(self):
        from mavisframework.runtime.llm_providers import _raise_for_status
        _raise_for_status(_Resp(status_code=200))   # 不抛

    def test_error_page_is_not_a_json_error(self):
        """核心回归:上游错误页曾报成 JSON 解析错,状态码整个消失。"""
        from mavisframework.runtime.llm_providers import _raise_for_status
        with pytest.raises(UpstreamHTTPError) as ei:
            _raise_for_status(_Resp(status_code=503, payload=None,
                                    text="<html>Service Unavailable</html>"))
        assert ei.value.status_code == 503
        assert "Expecting value" not in str(ei.value)   # 不再是 JSON 噪音


# --------------------------------------------------- 3. 编程错误不重试 / 不静默
class TestErrorVisibility:
    def test_type_error_not_retried(self):
        p = _Probe(exc=TypeError("boom"))
        with pytest.raises(TypeError):
            p.completion("hi", retry=5, failsafe="SENTINEL")
        assert len(p.calls) == 1, "编程错误不该被重试 5 次"

    def test_default_still_returns_failsafe(self):
        """默认行为不变:可重试错误耗尽后仍返回 failsafe(不是抛)。"""
        p = _Probe(exc=RuntimeError("upstream down"))
        assert p.completion("hi", retry=2, failsafe="FB", backoff=0) == "FB"

    def test_raise_on_error_opt_in(self):
        """`raise_on_error=True` 时末次失败把异常抛出去(让调用方知道)。"""
        p = _Probe(exc=RuntimeError("upstream down"))
        with pytest.raises(RuntimeError, match="upstream down"):
            p.completion("hi", retry=2, failsafe="FB", backoff=0, raise_on_error=True)

    def test_backoff_is_parameterized(self):
        """退避可配,且最后一轮不再白等一个 backoff。"""
        slept = []
        import mavisframework.runtime.llm_providers as lp
        orig = lp.time.sleep
        lp.time.sleep = lambda s: slept.append(s)
        try:
            p = _Probe(exc=RuntimeError("x"))
            p.completion("hi", retry=3, failsafe="FB", backoff=0.25)
        finally:
            lp.time.sleep = orig
        assert slept == [0.25, 0.25], "retry=3 只应睡 2 次,末轮不睡"


# ---------------------------------------------------- 4. 抽象接口与实现一致
class TestInterfaceParity:
    def test_abstract_declares_new_params(self):
        """抽象签名要跟得上,否则第三方 provider 不知道有这两个开关。"""
        import inspect
        from mavisframework.runtime.llm import LLMProvider
        params = inspect.signature(LLMProvider.completion).parameters
        assert params["backoff"].default == 5.0
        assert params["raise_on_error"].default is False

    def test_both_providers_accept_max_tokens(self):
        for cls in (OllamaProvider, OpenAIProvider):
            import inspect
            params = inspect.signature(cls._chat).parameters
            assert "max_tokens" in params, cls.__name__
            assert params["max_tokens"].default is None, cls.__name__
