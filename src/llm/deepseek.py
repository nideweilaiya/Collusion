"""DeepSeek API 适配器 (OpenAI 兼容协议 + 前缀缓存优化)"""
import os
from typing import List, Dict
from src.llm.base import BaseLLMAdapter
from src.cache_prefix import PREFIX


class DeepSeekAdapter(BaseLLMAdapter):
    """DeepSeek API 适配器

    支持 DeepSeek V4 Pro (deepseek-chat) 和 DeepSeek Flash。
    OpenAI 兼容协议，base_url = https://api.deepseek.com/v1

    Key 解析优先级（零配置理念）：
      1. 构造函数传入的 api_key
      2. DEEPSEEK_API_KEY 环境变量
      3. OPENAI_API_KEY 环境变量（兼容通用配置）
      4. LLM_API_KEY 环境变量
      5. 以上都没有 → 抛出明确错误提示

    缓存策略：
    - cached_call(): 使用全局固定 PREFIX 作为 system prompt
    - 每次调用只发送 PREFIX + minimal user context
    - PREFIX 不变 → DeepSeek 自动前缀缓存 → 输入成本降至 ~10%
    - 无对话历史累积 → 每次调用是干净的单轮
    """

    # 环境变量查找顺序（从高到低优先级）
    _ENV_KEY_NAMES = ["DEEPSEEK_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"]

    @classmethod
    def resolve_api_key(cls, explicit_key: str = None) -> str:
        """解析 API Key，按优先级查找多个来源"""
        if explicit_key:
            return explicit_key
        for name in cls._ENV_KEY_NAMES:
            val = os.environ.get(name, "")
            if val:
                return val
        return ""

    def __init__(self, api_key: str = None, model: str = "deepseek-chat",
                 base_url: str = "https://api.deepseek.com/v1"):
        self._api_key = self.resolve_api_key(api_key)
        if not self._api_key:
            raise ValueError(
                "未找到 API Key。请设置环境变量 DEEPSEEK_API_KEY 或 OPENAI_API_KEY，"
                "或在 config.json 中提供 api_key。\n"
                "免费注册: https://platform.deepseek.com"
            )
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._client = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._prefix_cached = False  # 首次调用后 PREFIX 被缓存

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=180,
            )
        return self._client

    # ==================== 缓存友好调用 ====================

    def cached_call(self, user_context: str, temperature: float = 0.1,
                    max_tokens: int = 4096) -> str:
        """使用全局 PREFIX 的无状态单轮调用

        PREFIX 在首次调用后自动被 DeepSeek 缓存，后续调用输入成本 ~10%。
        """
        messages = [
            {"role": "system", "content": PREFIX},
            {"role": "user", "content": user_context},
        ]
        text, inp, out = self._do_chat(messages, temperature, max_tokens)
        self.total_input_tokens += inp
        self.total_output_tokens += out
        self._prefix_cached = True
        return text

    def cached_call_json(self, user_context: str, temperature: float = 0.1,
                         max_tokens: int = 4096, retries: int = 3) -> Dict:
        """cached_call + JSON解析"""
        last_error = None
        for attempt in range(retries):
            try:
                text = self.cached_call(user_context, temperature, max_tokens)
                data = self._extract_json(text)
                return data
            except (Exception) as e:
                last_error = e
                if attempt < retries - 1:
                    user_context = f"{user_context}\n\n[上轮输出JSON格式错误，请严格按Schema重新输出。错误: {e}]"
        raise ValueError(f"JSON解析失败(已重试{retries}次): {last_error}")

    # ==================== 传统调用（向后兼容，无缓存） ====================

    def chat(self, messages: List[Dict], temperature: float = 0.1,
             max_tokens: int = 4096) -> str:
        """传统多轮对话调用（无前缀缓存优化，向后兼容）"""
        text, inp, out = self._do_chat(messages, temperature, max_tokens)
        self.total_input_tokens += inp
        self.total_output_tokens += out
        return text

    def _do_chat(self, messages: List[Dict], temperature: float,
                 max_tokens: int) -> tuple:
        response = self._get_client().chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        choice = response.choices[0]
        text = choice.message.content or ""
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0
        return text, input_tokens, output_tokens

    # ==================== 成本与属性 ====================

    @property
    def total_cost_rmb(self) -> float:
        return (self.total_input_tokens / 1000 * self.cost_per_1k_input
                + self.total_output_tokens / 1000 * self.cost_per_1k_output)

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def cost_per_1k_input(self) -> float:
        if "flash" in self._model.lower():
            return 0.0005
        return 0.001

    @property
    def cost_per_1k_output(self) -> float:
        if "flash" in self._model.lower():
            return 0.002
        return 0.004

