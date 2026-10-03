"""framework.runtime.llm — LLM 适配接口(可插拔)

框架只依赖 LLMProvider 接口,不绑定具体模型。
- OllamaProvider / OpenAIProvider 为现有实现(modules/model/llm_model.py 的抽象)
- 加新模型 = 新增一个 Provider 实现
"""
from abc import ABC, abstractmethod
from typing import Any, Optional


class LLMProvider(ABC):
    """LLM 统一接口"""

    @abstractmethod
    def completion(
        self,
        prompt: str,
        retry: int = 10,
        callback=None,
        failsafe=None,
        return_type=None,
        caller: str = "llm_normal",
        backoff: float = 5.0,
        raise_on_error: bool = False,
        **kwargs
    ) -> Any:
        """结构化输出调用(带重试/超时/failsafe)

        可透传参数(**kwargs → provider 的 `_completion`):
        - ``temperature``(默认 0.5)
        - ``max_tokens``(默认 **None = 不下发**,沿用上游默认值)

        另有一个**特殊透传**:`timeout`(默认 90 秒)。它在 provider 的
        `_completion_timeout` 上是具名参数,所以 `completion(..., timeout=X)`
        直接生效,只是没写在这里 —— 这一条曾让接入方以为"超时不可调"。
        (2026-10-03 补记;不要为了"显式"把它加进本签名,那样会把它从
        `**kwargs` 里截下来、反而不转发到底层。)

        ``backoff`` 退避秒数、``raise_on_error`` 末次失败是否抛异常,
        均为 2026-10-03 新增,默认值保持原行为。

        `return_type` 若是 Pydantic 模型,**顶层必须有 `res` 字段**
        (框架取 `model_validate(...).res`);缺字段会在发请求前报 TypeError。

        参数/编程类错误(TypeError 等)一律**不重试、直接抛**。
        """
        ...

    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    def get_summary(self) -> dict:
        ...


def create_llm_provider(config: dict) -> LLMProvider:
    """工厂:按配置创建 Provider(provider: ollama / openai / ...)"""
    provider = config.get("provider", "ollama")
    if provider == "ollama":
        from mavisframework.runtime.llm_providers import OllamaProvider
        return OllamaProvider(config)
    if provider == "openai":
        from mavisframework.runtime.llm_providers import OpenAIProvider
        return OpenAIProvider(config)
    raise NotImplementedError("llm provider {} is not supported".format(provider))
