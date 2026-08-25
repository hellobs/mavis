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
        # 结果缓存:确定性调用白名单(LRU,进程级)
        self._cache_enabled = bool(config.get("cache", True))
        self._cache = {}
        self._cache_order = []
        self._cache_max = int(config.get("cache_max", 2000))
        self._cache_hits = 0

    # ---------------- 对外接口 ----------------
    def completion(
        self, prompt, retry=10, callback=None, failsafe=None,
        return_type=None, caller="llm_normal", **kwargs
    ):
        # 缓存命中:仅确定性调用(见 _CACHEABLE_CALLERS)
        cache_key = None
        if self._cache_enabled and caller in self._CACHEABLE_CALLERS:
            cache_key = (caller, prompt, return_type.__name__ if return_type else "")
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache_hits += 1
                return cached

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
                from mavisframework.runtime.logger import get_logger

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
        result = response if response is not None else failsafe

        if cache_key is not None and result is not None:
            self._cache[cache_key] = result
            self._cache_order.append(cache_key)
            if len(self._cache_order) > self._cache_max:
                old = self._cache_order.pop(0)
                self._cache.pop(old, None)
        return result

    # 确定性调用白名单:同一 prompt 结果应一致的调用才缓存
    # (poignancy 打分 / 复读检查 / 关系摘要短时稳定)
    _CACHEABLE_CALLERS = {
        "poignancy_event",
        "poignancy_chat",
        "generate_chat_check_repeat",
    }

    def cache_stats(self) -> dict:
        total_calls = self._summary["total"][0] + self._cache_hits
        return {
            "hits": self._cache_hits,
            "misses": self._summary["total"][0],
            "hit_rate": round(self._cache_hits / total_calls, 3) if total_calls else 0,
            "cache_size": len(self._cache),
        }

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
            return self._parse_output(ret, return_type)
        return ret

    # ------------------------------------------------------------------
    # 通用输出解析(三层策略,不针对具体残渣形态)
    # ------------------------------------------------------------------
    def _parse_output(self, text: str, return_type) -> str:
        """从 LLM 输出中提取结构化结果,三层递进:

        第 1 层:整体就是合法 JSON → 直接解析
        第 2 层:文本中扫描所有合法 JSON 对象 → 取第一个能通过校验的 res
                (覆盖:分析+最终输出、markdown 代码块、拼接对象、前置废话)
        第 3 层:文本中无合法 JSON(LLM 直接输出了裸文本) → 统一截断清理
        """
        # 第 1 层:整体解析
        try:
            parsed = json.loads(text)
            return return_type.model_validate(parsed).res
        except Exception:
            pass

        # 第 2 层:扫描文本中所有合法 JSON 对象
        for obj in self._scan_json_objects(text):
            try:
                return return_type.model_validate(obj).res
            except Exception:
                continue

        # 第 3 层:裸文本,统一截断清理(去残渣,保文本)
        return self._cleanup_text(text)

    @staticmethod
    def _scan_json_objects(text: str):
        """扫描文本中所有可被 JSONDecoder 解析的对象(不关心残渣形态)

        从每个 '{' 位置尝试 raw_decode,能解析出对象的都收进来。
        返回对象列表(可能为空)。
        """
        objs = []
        idx = 0
        decoder = json.JSONDecoder()
        while True:
            start = text.find("{", idx)
            if start < 0:
                break
            try:
                obj, end = decoder.raw_decode(text[start:])
                objs.append(obj)
                idx = start + max(end, 1)
            except Exception:
                idx = start + 1
        return objs

    @staticmethod
    def _cleanup_text(text: str) -> str:
        """裸文本清理:从第一个"元信息残渣信号"处截断,保留对话正文

        残渣信号 = LLM 在输出正文后附加的元信息起始处,常见形式:
        - JSON 对象残渣: "}{ / " }{ / "} 时间戳 { / 孤立 { }
        - markdown 代码块: ```
        - 引导词:最终输出:/答案是:
        - ISO 时间戳:2025-02-13T21:44:00Z
        从最早出现的位置截断,并将截断点前的孤立引号/括号一并去掉。
        """
        stripped = text.strip()

        # 统一残渣信号:覆盖所有已知元信息起始形式(一个正则,不再逐 case)
        marker = re.compile(
            r'"?\}\s*\{'                       # "}{ / " }{ / }{ (json拼接残渣)
            r'|```'                             # markdown 代码块
            r'|最终输出\s*[：:]'                # 引导词
            r'|答案是\s*[：:]'
            r'|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?'  # ISO时间戳
            r'|"?\}(?=\s*[^"\s{])'             # "} 后跟非引号非大括号(如 "} 时间戳)
        )
        m = marker.search(stripped)
        if m:
            head = stripped[: m.start()]
            # 去掉截断点前残留的孤立引号/大括号/空白
            head = re.sub(r'[\s"{}]+$', "", head)
            return head.strip()

        # 无信号:删除行尾孤立引号/大括号后原样返回
        return re.sub(r'["{}]+$', "", stripped).strip()

    def _chat(self, messages, temperature, response_format=None):
        raise NotImplementedError


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
