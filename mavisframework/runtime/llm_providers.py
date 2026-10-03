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
import time
import concurrent.futures

import requests


# 编程/参数类错误:同样的输入重试必然同样失败,再睡 5 秒重试 10 次纯属空转。
# 这类错误**立刻抛出**,不允许退化成"静默空转 + 交一个看似正常的 failsafe"
# (2026-10-03 修:此前传 `max_tokens=` 会 TypeError → 被 except 吞掉 → 睡 5s ×10
#  → 返回 failsafe,调用方完全看不出自己传错了参数)。
_BUG_ERRORS = (TypeError, AttributeError, NameError, ImportError,
               KeyError, AssertionError, IndexError)


class UpstreamHTTPError(RuntimeError):
    """上游返回非 2xx。

    为什么要专门一个类型(2026-10-03 修):此前两个 provider 都是
    `response.json()` 直接解,**HTTP 500 的事实被整个丢掉** —— 错误页/网关返回的
    HTML 或纯文本会让 JSON 解析器报"Expecting value",而真正的错误是"上游挂了"。
    这里带上状态码与响应体片段,日志里第一眼就能看到 500 而不是一条 JSON 报错。

    它是**可重试**的(不属于 `_BUG_ERRORS`):上游临时过载/网关抖动确实该退避重试。
    """

    def __init__(self, status_code, body=""):
        self.status_code = status_code
        self.body = (body or "").strip()[:200]
        super().__init__("上游返回 HTTP {}: {}".format(status_code, self.body))


def _raise_for_status(response):
    """非 2xx 就带着状态码抛 `UpstreamHTTPError`(2026-10-03 修)。

    不这么做的话,上游 500 的错误页/网关 HTML 会直接进 `response.json()`,
    报出来的是"Expecting value: line 1 column 1"—— 真正的原因(上游挂了)
    被彻底掩盖。状态码是排查的第一现场,不能丢。
    """
    code = getattr(response, "status_code", 200)
    if code is not None and code >= 400:
        body = ""
        try:
            body = response.text
        except Exception:
            body = ""
        raise UpstreamHTTPError(code, body)



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
        return_type=None, caller="llm_normal",
        backoff=5.0, raise_on_error=False, **kwargs
    ):
        """结构化输出调用(带重试/超时/failsafe)。

        `backoff` / `raise_on_error` 为 2026-10-03 新增,**默认行为与之前一致**:
        - `backoff=5.0`:每次失败后的退避秒数(写死版是 5s);
        - `raise_on_error=False`:最后一次仍失败时返回 `failsafe`(原行为);
          置 True 则把最后一个异常抛出去,让调用方知道"这次是真的没跑成"。

        另有一类**不重试**的异常:参数/编程错误(`_BUG_ERRORS`)。重试对它们
        没有意义,一律立刻抛出 —— 静默空转是最贵的一种失败。
        """
        # 缓存命中:仅确定性调用(见 _CACHEABLE_CALLERS)
        cache_key = None
        if self._cache_enabled and caller in self._CACHEABLE_CALLERS:
            cache_key = (caller, prompt, return_type.__name__ if return_type else "")
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache_hits += 1
                return cached

        response = None
        last_err = None
        self._summary.setdefault(caller, [0, 0, 0])
        sem = self._semaphore(self._concurrency)
        for attempt in range(retry):
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
            except _BUG_ERRORS as e:
                # 参数/编程错误:重试只会重复同一个失败,立刻抛(不睡不退不吞)
                from mavisframework.runtime.logger import get_logger
                get_logger("llm").error(
                    "LLM completion 编程错误(不重试): {}: {}".format(type(e).__name__, e))
                raise
            except Exception as e:
                from mavisframework.runtime.logger import get_logger

                last_err = e
                get_logger("llm").warning("LLM completion error: {}".format(e))
                if attempt < retry - 1:      # 最后一轮不再白睡
                    time.sleep(backoff)
                response = None
                continue
            if response is not None:
                break
        pos = 2 if response is None else 1
        self._summary["total"][pos] += 1
        self._summary[caller][pos] += 1
        if response is None and raise_on_error and last_err is not None:
            from mavisframework.runtime.logger import get_logger
            get_logger("llm").error(
                "LLM completion 重试 {} 次仍失败,按 raise_on_error 抛出".format(retry))
            raise last_err
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

    def _completion(self, prompt, return_type, temperature=0.5,
                    max_tokens=None, **unsupported):
        """`max_tokens=None` 表示**不下发该参数**,沿用上游自己的默认值 ——
        这保证默认行为与加这个参数之前逐字节一致(直接写死一个数字会让原本
        不受限的输出被截断,是比"传不进去"更糟的回归)。

        `**unsupported`:以前 `**kwargs` 会被静默吞掉,现在遇到不认识的参数立刻
        报错并列出可用项 —— 拼错参数名要在第一次就炸,而不是睡满一轮退避之后
        交一个 failsafe。
        """
        if unsupported:
            raise TypeError(
                "LLM provider 不支持这些参数: {};可用参数: temperature / max_tokens"
                .format(sorted(unsupported)))
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
        ret = self._chat(messages, temperature, response_format, max_tokens)

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
    def _chat(self, messages, temperature, response_format=None, max_tokens=None):
        headers = {"Content-Type": "application/json"}
        params = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        if response_format:
            params["response_format"] = response_format
        response = requests.post(
            url=f"{self._base_url}/chat/completions",
            headers=headers,
            json=params,
            stream=False,
            timeout=300,
        )
        _raise_for_status(response)
        data = response.json()
        if data and len(data.get("choices", [])) > 0:
            return data["choices"][0]["message"]["content"]
        return ""


class OpenAIProvider(_BaseProvider):
    def _chat(self, messages, temperature, response_format=None, max_tokens=None):
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
        if max_tokens is not None:
            params["max_tokens"] = max_tokens
        if response_format:
            params["response_format"] = response_format
        response = requests.post(
            url=f"{self._base_url}/chat/completions",
            headers=headers,
            json=params,
            stream=False,
            timeout=300,
        )
        _raise_for_status(response)
        data = response.json()
        if data and len(data.get("choices", [])) > 0:
            return data["choices"][0]["message"]["content"]
        return ""
