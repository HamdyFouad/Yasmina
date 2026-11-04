"""DeepSeek extraction client (OpenAI-compatible ChatOpenAI).

The client is intentionally limited to extraction/summarization. It never
calculates prices, margins or totals.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.config import Settings

__all__ = ["DeepSeekClient", "DeepSeekUnavailable", "RawItemSpecs"]


class DeepSeekUnavailable(RuntimeError):
    """Raised when no API key or LangChain OpenAI client is available."""


class RawItemSpecs(BaseModel):
    """Raw strings returned by the LLM; normalizers convert them afterwards."""

    model_config = ConfigDict(extra="forbid")

    material: str | None = Field(default=None, description="Material, e.g. Nitril or Latex")
    color: str | None = Field(default=None, description="Colour, e.g. blau or transparent")
    glove_size: str | None = Field(default=None, description="EN 420 size, e.g. M or 8")
    thickness: str | None = Field(default=None, description="Thickness in µm if stated")
    pack_size: str | None = Field(default=None, description="Units per pack/carton if stated")


_SYSTEM_PROMPT = """You extract product specifications from German tender/LV text.
Return only what the text supports. Do not invent values, prices, quantities or
totals. Leave a field null when it is not stated. Do not perform arithmetic."""


class DeepSeekClient:
    """Thin structured-output wrapper around DeepSeek-Chat."""

    def __init__(
        self,
        settings: Settings,
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> None:
        if settings.deepseek_api_key is None:
            raise DeepSeekUnavailable("DEEPSEEK_API_KEY is not configured")
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise DeepSeekUnavailable("langchain-openai is not installed") from exc

        self._chat = ChatOpenAI(
            model=model or settings.deepseek_chat_model,
            api_key=settings.deepseek_api_key.get_secret_value(),
            base_url=settings.deepseek_base_url,
            temperature=settings.deepseek_temperature if temperature is None else temperature,
            max_retries=2,
        )
        self._structured = self._chat.with_structured_output(RawItemSpecs)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        model: str | None = None,
    ) -> "DeepSeekClient":
        return cls(settings, model=model)

    def extract_item_specs(self, text: str) -> RawItemSpecs:
        """Extract material/colour/size/thickness/pack hints from item text."""

        messages: list[Any] = [
            ("system", _SYSTEM_PROMPT),
            ("human", text),
        ]
        result = self._structured.invoke(messages)
        if isinstance(result, RawItemSpecs):
            return result
        if isinstance(result, dict):
            return RawItemSpecs.model_validate(result)
        return RawItemSpecs.model_validate(result.model_dump())

    def summarize(self, text: str, *, max_words: int = 120) -> str:
        """Summarize tender text without changing any business data."""

        response = self._chat.invoke(
            [
                (
                    "system",
                    "Summarize German tender text factually. Do not calculate or infer "
                    f"prices. Keep it under {max_words} words.",
                ),
                ("human", text),
            ]
        )
        content = response.content
        if isinstance(content, list):  # multi-part content
            return " ".join(str(part) for part in content)
        return str(content)
