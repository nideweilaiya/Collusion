"""LLM 适配器抽象基类"""
from abc import ABC, abstractmethod
from typing import List, Dict
import json
import re


class BaseLLMAdapter(ABC):
    """LLM 适配器抽象基类 — 统一接口，多后端可切换"""

    @abstractmethod
    def chat(self, messages: List[Dict], temperature: float = 0.1,
             max_tokens: int = 4096) -> str:
        """发送对话，返回文本响应"""
        ...

    @abstractmethod
    def _do_chat(self, messages: List[Dict], temperature: float,
                 max_tokens: int) -> tuple:
        """内部调用，返回 (text, input_tokens, output_tokens)"""
        ...

    def chat_with_json(self, messages: List[Dict], temperature: float = 0.1,
                       max_tokens: int = 4096, retries: int = 3) -> Dict:
        """发送对话，强制解析JSON响应。含正则提取 + 自动修复 + 重试"""
        last_error = None
        for attempt in range(retries):
            try:
                text = self.chat(messages, temperature, max_tokens)
                data = self._extract_json(text)
                return data
            except (json.JSONDecodeError, ValueError) as e:
                last_error = e
                if attempt < retries - 1:
                    messages.append({"role": "assistant", "content": text})
                    messages.append({"role": "user",
                                     "content": f"你的回复JSON格式不正确。错误详情：{e}。请修正后重新输出完整的JSON。"})
        raise ValueError(f"JSON解析失败（已重试{retries}次）: {last_error}")

    @staticmethod
    def _repair_json(text: str) -> str:
        """修复常见的 LLM JSON 格式错误"""
        # 1. 移除尾随逗号 (,} 或 ,])
        text = re.sub(r',\s*}', '}', text)
        text = re.sub(r',\s*]', ']', text)

        # 2. 移除 JSON 前后的 markdown 或解释文本
        if not text.strip().startswith('{'):
            m = re.search(r'\{', text)
            if m:
                text = text[m.start():]
        if not text.strip().endswith('}'):
            m = list(re.finditer(r'\}', text))
            if m:
                text = text[:m[-1].end()]

        # 3. 修复截断的 JSON（补全缺失的闭合括号）
        open_braces = text.count('{')
        close_braces = text.count('}')
        if open_braces > close_braces:
            text += '}' * (open_braces - close_braces)
        open_brackets = text.count('[')
        close_brackets = text.count(']')
        if open_brackets > close_brackets:
            text += ']' * (open_brackets - close_brackets)
        # 补全可能截断的字符串（如果最后是 " 后面没有闭合）
        in_string = False
        for ch in text:
            if ch == '"':
                in_string = not in_string
        if in_string:
            text += '"'

        return text

    @classmethod
    def _extract_json(cls, text: str) -> Dict:
        """从文本中提取JSON对象，含自动修复"""
        # 尝试1: 直接解析 + 修复
        repaired = cls._repair_json(text)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

        # 尝试2: 提取 ```json ... ``` 代码块
        m = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        if m:
            try:
                return json.loads(cls._repair_json(m.group(1)))
            except json.JSONDecodeError:
                pass

        # 尝试3: 提取 { ... } 最外层对象
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            try:
                return json.loads(cls._repair_json(m.group(0)))
            except json.JSONDecodeError:
                pass

        # 尝试4: 补全缺失的开头 {
        stripped = text.strip()
        if not stripped.startswith('{') and ':' in stripped:
            try:
                return json.loads(cls._repair_json("{" + stripped + "}"))
            except json.JSONDecodeError:
                pass

        raise ValueError(f"无法从响应中提取JSON: {text[:300]}...")

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...

    @property
    @abstractmethod
    def cost_per_1k_input(self) -> float:
        ...

    @property
    @abstractmethod
    def cost_per_1k_output(self) -> float:
        ...
