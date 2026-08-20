"""framework.runtime.llm_providers — LLM Provider 实现(自包含,零 modules 依赖)

- OllamaProvider :本地 Ollama(OpenAI 兼容 /chat/completions)
- OpenAIProvider:OpenAI / DeepSeek 等兼容 API

统一行为:带 90s 超时(防挂起)、结构化输出(json_schema)、重试、
LLM 输出 JSON 残渣清理(raw_decode 提取首个对象 + 残渣剥离)。
"""
import json
import os
import re
import threading
import concurrent.futures

import requests


class _BaseProvider:
    """统一入口:带超时、重试、failsafe 的 completion

    并发控制:所有 Provider 共享一个全局信号量(_GLOBAL_SEM),
    限制同时进行的 LLM 请求数——Ollama 单实例并发有限,超了只会排队无收益。
    """

    _GLOBAL_SEM = None          # 全局信号量(进程级,按需创建)
    _GLOBAL_SEM_LOCK = threading.Lock()

    @classmethod
    def _semaphore(cls, size: int = 4):
        """获取全局并发信号量(进程级共享)"""
        with cls._GLOBAL_SEM_LOCK:
            if cls._GLOBAL_SEM is None or cls._GLOBAL_SEM_SIZE != size:
                cls._GLOBAL_SEM = threading.Semaphore(size)
                cls._GLOBAL_SEM_SIZE = size
        return cls._GLOBAL_SEM

    def __init__(self, config: dict):
        self._config = config
        self._api_key = os.getenv("LLM_API_KEY", config.get("api_key", ""))
        self._base_url = config["base_url"]
        self._model = config["model"]
        self._enabled = True
        self._summary = {"total": [0, 0, 0]}
        # 并发上限:配置优先(如 llm.concurrency),默认 4
        self._concurrency = int(config.get("concurrency", 4) or 4)

    # ---------------- 对外接口 ----------------
    def completion(
        self, prompt, retry=10, callback=None, failsafe=None,
        return_type=None, caller="llm_normal", **kwargs
    ):
        response = None
        self._summary.setdefault(caller, [0, 0, 0])
        sem = self._semaphore(self._concurrency)
        for _ in range(retry):
            try:
                # 限流:限制同时进行的 LLM 请求数(Ollama 并发有限)
                with sem:
                    output = self._completion_timeout(prompt, return_type, **kwargs)
                self._summary["total"][0] += 1
                self._summary[caller][0] += 1
                if callback:
                    response = callback(output)
                else:
                    response = output
            except Exception as e:
                from framework.runtime.logger import get_logger

                get_logger("llm").warning(f"LLM completion error: {e}")
                import time

                time.sleep(5)
                response = None
                continue
            if response is not None:
                break
        pos = 2 if response is None else 1
        self._summary["total"][pos] += 1
        self._summary[caller][pos] += 1
        return response if response is not None else failsafe

    def is_available(self):
        return self._enabled

    def get_summary(self):
        des = {}
        for k, v in self._summary.items():
            des[k] = "S:{},F:{}/R:{}".format(v[1], v[2], v[0])
        return {"model": self._model, "summary": des}

    def disable(self):
        self._enabled = False

    # ---------------- 内部 ----------------
    def _completion_timeout(self, prompt, return_type, timeout=90, **kwargs):
        """带超时的 LLM 调用:防止 API 挂起导致模拟无限卡住"""
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self._completion, prompt, return_type, **kwargs)
            return future.result(timeout=timeout)

    def _completion(self, prompt, return_type, temperature=0.5):
        # 生成 JSON schema from Pydantic model(结构化输出)
        response_format = None
        if return_type is not None:
            try:
                schema = return_type.model_json_schema()
                response_format = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": return_type.__name__,
                        "strict": True,
                        "schema": schema,
                    },
                }
            except Exception:
                pass

        messages = [{"role": "user", "content": prompt}]
        ret = self._chat(messages, temperature, response_format)

        # 过滤 <think> 标签
        ret = re.sub(r"<think>.*</think>", "", ret, flags=re.DOTALL)

        if return_type is not None:
            try:
                parsed = json.loads(ret)
                return return_type.model_validate(parsed).res
            except json.JSONDecodeError:
                # 拼接 JSON({...}{...})或文本中夹 JSON:raw_decode 提取首个对象
                try:
                    decoder = json.JSONDecoder()
                    start = ret.find("{")
                    if start >= 0:
                        obj, _ = decoder.raw_decode(ret[start:])
                        return return_type.model_validate(obj).res
                except Exception:
                    pass
                # 仍失败:清理残渣后返回文本
                return self._cleanup_json_residue(ret)
            except Exception as e:
                from framework.runtime.logger import get_logger

                get_logger("llm").warning(f"validate response error: {e}")
                return ret
        return ret

    def _chat(self, messages, temperature, response_format=None):
        raise NotImplementedError

    @staticmethod
    def _cleanup_json_residue(text: str) -> str:
        """清理 LLM 输出中混入的 JSON 语法残渣(如 `"}{"`、孤立的引号/大括号)"""
        text = re.sub(r'"?\}\{"?', "", text)
        text = re.sub(r'[{}"]+$', "", text)
        return text.strip()


class OllamaProvider(_BaseProvider):
    def _chat(self, messages, temperature, response_format=None):
        headers = {"Content-Type": "application/json"}
        params = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if response_format:
            params["response_format"] = response_format
        response = requests.post(
            url=f"{self._base_url}/chat/completions",
            headers=headers,
            json=params,
            stream=False,
            timeout=300,
        )
        data = response.json()
        if data and len(data.get("choices", [])) > 0:
            return data["choices"][0]["message"]["content"]
        return ""


class OpenAIProvider(_BaseProvider):
    def _chat(self, messages, temperature, response_format=None):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        params = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if response_format:
            params["response_format"] = response_format
        response = requests.post(
            url=f"{self._base_url}/chat/completions",
            headers=headers,
            json=params,
            stream=False,
            timeout=300,
        )
        data = response.json()
        if data and len(data.get("choices", [])) > 0:
            return data["choices"][0]["message"]["content"]
        return ""
