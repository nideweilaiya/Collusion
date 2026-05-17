"""MCP Sampling 委托调用适配器

通过 MCP 协议的 sampling/createMessage 委托宿主 LLM 调用，
保留宿主缓存，不直接持有 API Key。

工作模式:
  1. 适配器将 chat 请求放入入站队列
  2. MCP 服务器的 asyncio 循环从队列取出，调用 create_message
  3. 结果放入出站队列，适配器同步等待返回

启用条件:
  - MCP 客户端支持 sampling 协议（Reasonix 等）
  - 环境变量 COLLUSION_SAMPLING_MODE=1 或配置 sampling.enabled=true
"""
import queue
import uuid
import time
from typing import List, Dict
from src.llm.base import BaseLLMAdapter


class MCPSamplingAdapter(BaseLLMAdapter):
    """委托 MCP 宿主调用的 LLM 适配器 — 保留宿主缓存"""

    # 类级别的请求/响应队列（跨线程通信）
    _request_queue: queue.Queue = queue.Queue()
    _response_queues: Dict[str, queue.Queue] = {}

    @classmethod
    def set_sampling_callback(cls, callback):
        """设置 MCP 服务器端的回调函数

        callback 签名为 async def callback(messages, max_tokens) -> str
        由 MCP 服务器在初始化时注入
        """
        cls._sampling_callback = callback

    def __init__(self, model: str = "host-default", base_url: str = ""):
        self._model = model
        self._base_url = base_url
        self._sampling_callback = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    @property
    def _callback(self):
        """获取采样回调（类级别共享）"""
        return MCPSamplingAdapter._sampling_callback

    def _do_chat(self, messages: List[Dict], temperature: float,
                 max_tokens: int) -> tuple:
        """通过 MCP Sampling 委托宿主调用 LLM"""
        callback = self._callback
        if callback is None:
            raise RuntimeError(
                "MCP Sampling 回调未设置。"
                "请在 MCP 服务器初始化时调用 MCPSamplingAdapter.set_sampling_callback()"
            )

        request_id = f"sampling_{uuid.uuid4().hex[:8]}"
        response_queue: queue.Queue = queue.Queue()
        MCPSamplingAdapter._response_queues[request_id] = response_queue

        try:
            # 将请求放入队列（asyncio 线程将处理它）
            MCPSamplingAdapter._request_queue.put({
                "id": request_id,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            })

            # 同步等待响应（最多 5 分钟超时）
            try:
                result = response_queue.get(timeout=300)
            except queue.Empty:
                raise TimeoutError(
                    f"Sampling 请求 {request_id} 超时（5分钟）"
                )

            if isinstance(result, Exception):
                raise result

            text = result.get("text", "")
            input_tokens = result.get("input_tokens", 0)
            output_tokens = result.get("output_tokens", 0)

            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens
            return text, input_tokens, output_tokens

        finally:
            MCPSamplingAdapter._response_queues.pop(request_id, None)

    def chat(self, messages: List[Dict], temperature: float = 0.1,
             max_tokens: int = 4096) -> str:
        text, _, _ = self._do_chat(messages, temperature, max_tokens)
        return text

    def cached_call(self, user_context: str, temperature: float = 0.1,
                    max_tokens: int = 4096) -> str:
        """缓存友好调用 — 通过 MCP Sampling 委托"""
        from src.cache_prefix import PREFIX
        messages = [
            {"role": "system", "content": PREFIX},
            {"role": "user", "content": user_context},
        ]
        return self.chat(messages, temperature, max_tokens)

    def cached_call_json(self, user_context: str, temperature: float = 0.1,
                         max_tokens: int = 4096, retries: int = 3) -> Dict:
        """cached_call + JSON 解析"""
        last_error = None
        for attempt in range(retries):
            try:
                text = self.cached_call(user_context, temperature, max_tokens)
                return self._extract_json(text)
            except Exception as e:
                last_error = e
                if attempt < retries - 1:
                    user_context = (
                        f"{user_context}\n\n"
                        f"[上轮输出JSON格式错误，请严格按Schema重新输出。错误: {e}]"
                    )
        raise ValueError(f"JSON解析失败(已重试{retries}次): {last_error}")

    @property
    def model_name(self) -> str:
        return self._model or "host-default"

    @property
    def cost_per_1k_input(self) -> float:
        return 0.0001  # 委托模式下仅统计，实际由宿主计费

    @property
    def cost_per_1k_output(self) -> float:
        return 0.0004  # 委托模式下仅统计，实际由宿主计费

    @property
    def total_cost_rmb(self) -> float:
        return (self.total_input_tokens / 1000 * self.cost_per_1k_input
                + self.total_output_tokens / 1000 * self.cost_per_1k_output)
