"""framework.runtime.llm_providers — LLM Provider 实现

Ollama / OpenAI(DeepSeek 等兼容 API)Provider。
复用现有 modules/model/llm_model.py 的类,作为框架接口的实现——
框架层不直接依赖 modules,但 Provider 可作为"适配器"包装现有实现。
"""
import os


class _BaseProvider:
    """统一入口:带 90s 超时,防 API 挂起卡死"""

    def __init__(self, config: dict):
        self._config = config
        self._enabled = True
        self._summary = {"total": [0, 0, 0]}

    def completion(
        self, prompt, retry=10, callback=None, failsafe=None,
        return_type=None, caller="llm_normal", **kwargs
    ):
        # 复用现有实现(带超时的 completion)
        from modules.model.llm_model import create_llm_model
        if self._engine is None:
            self._engine = create_llm_model(self._config)
        return self._engine.completion(
            prompt, retry=retry, callback=callback, failsafe=failsafe,
            return_type=return_type, caller=caller, **kwargs
        )

    def is_available(self):
        return self._enabled

    def get_summary(self):
        return {"model": self._config.get("model", ""), "summary": self._summary}


class OllamaProvider(_BaseProvider):
    def __init__(self, config: dict):
        super().__init__(config)
        self._engine = None


class OpenAIProvider(_BaseProvider):
    def __init__(self, config: dict):
        super().__init__(config)
        self._engine = None
