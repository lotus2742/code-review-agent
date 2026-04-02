"""
LLM 客户端模块
- Settings: 集中管理环境变量配置
- call_llm: 调用 LLM 接口（流式输出）
"""

import logging
import sys
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

logger = logging.getLogger(__name__)


# ---- 集中管理环境变量 ----

@dataclass
class Settings:
    openai_api_key: str
    openai_api_base: str
    openai_model: str
    github_token: str
    model_context_tokens: int | None  # 手动指定上下文窗口大小（tokens），None 表示自动推断

    @classmethod
    def load(cls) -> "Settings":
        import os
        missing = []
        for key in ("OPENAI_API_KEY", "OPENAI_API_BASE", "OPENAI_MODEL"):
            if not os.getenv(key):
                missing.append(key)
        if missing:
            logger.error("缺少必要环境变量：%s，请检查 .env 文件", ", ".join(missing))
            sys.exit(1)
        ctx_raw = os.getenv("MODEL_CONTEXT_TOKENS", "")
        model_context_tokens = int(ctx_raw) if ctx_raw.strip().isdigit() else None
        return cls(
            openai_api_key=os.environ["OPENAI_API_KEY"],
            openai_api_base=os.environ["OPENAI_API_BASE"],
            openai_model=os.environ["OPENAI_MODEL"],
            github_token=os.getenv("GITHUB_TOKEN", ""),
            model_context_tokens=model_context_tokens,
        )

    @property
    def max_diff_chars(self) -> int:
        """计算允许的最大 diff 字符数。
        优先使用 MODEL_CONTEXT_TOKENS 环境变量手动指定；
        未指定时按模型名称关键词推断，兜底 8K。
        策略：预留 4000 tokens 给 system prompt + 输出，剩余用于 diff。
        粗略换算：1 token ≈ 4 字符（英文代码）。
        """
        if self.model_context_tokens is not None:
            context_tokens = self.model_context_tokens
        else:
            model = self.openai_model.lower()
            if any(k in model for k in ("qwen-turbo", "qwen-plus", "qwen-max",
                                        "gpt-4o", "gpt-4-turbo", "claude-3",
                                        "deepseek", "gemini")):
                context_tokens = 128_000
            elif any(k in model for k in ("gpt-4-32k",)):
                context_tokens = 32_000
            elif any(k in model for k in ("gpt-3.5-turbo-16k",)):
                context_tokens = 16_000
            else:
                context_tokens = 8_000  # 未知模型，保守兜底
                logger.warning(
                    "⚠️  未能识别模型 [%s] 的上下文大小，默认使用 8K。"
                    "可在 .env 中设置 MODEL_CONTEXT_TOKENS=<token数> 手动指定。",
                    self.openai_model
                )

        reserved_tokens = 4_000          # system prompt + 输出预留
        chars_per_token = 4              # 英文代码粗略换算
        return (context_tokens - reserved_tokens) * chars_per_token


# ---- LLM 调用 ----

def call_llm(messages: list, settings: Settings) -> str:
    client = OpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_api_base,
    )
    full_content = ""
    stream = client.chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        temperature=0.1,
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            full_content += delta.content
            print(delta.content, end="", flush=True)   # 流式输出保留 print，体验更好
    print()
    return full_content

