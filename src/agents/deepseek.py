"""DeepSeek extraction client (OpenAI-compatible ChatOpenAI).

The client is intentionally limited to extraction/summarization. It never
calculates prices, margins or totals.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.config import Settings

__all__ = ["DeepSeekClient", "DeepSeekUnavailable", "RawItemSpecs", "TenderTriage"]


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


class TenderTriage(BaseModel):
    """LLM relevance classification for a cleaning/hygiene tender."""

    model_config = ConfigDict(extra="forbid")

    is_relevant: bool = Field(description="True only for cleaning/hygiene procurement")
    confidence: float = Field(ge=0.0, le=1.0, description="Classifier confidence")
    reason: str = Field(min_length=1, max_length=1000)
    matched_cpv_codes: list[str] = Field(
        default_factory=list,
        description="CPV codes explicitly present in the text, not inferred",
    )


_TRIAGE_PROMPT = """You triage German public procurement notices for a B2B supplier
of cleaning and hygiene products (paper towels, gloves, waste bags, cleaning
chemicals). Mark is_relevant=true only when the notice's subject is one of those
product families. Base the decision only on the supplied text and CPV codes.
Do not infer prices, quantities, eligibility or missing CPV codes. Return a short
factual reason and only CPV codes explicitly present in the text."""

_ITEM_PROMPT = """You extract product specifications from German tender/LV text.
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
        chat: Any | None = None,
    ) -> None:
        if chat is None:
            api_key = (
                settings.deepseek_api_key.get_secret_value().strip()
                if settings.deepseek_api_key is not None
                else ""
            )
            if not api_key:
                raise DeepSeekUnavailable("DEEPSEEK_API_KEY is not configured")
            try:
                from langchain_openai import ChatOpenAI
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise DeepSeekUnavailable("langchain-openai is not installed") from exc

            chat = ChatOpenAI(
                model=model or settings.deepseek_chat_model,
                api_key=api_key,
                base_url=settings.deepseek_base_url,
                temperature=settings.deepseek_temperature if temperature is None else temperature,
                max_retries=2,
            )
        self._chat = chat
        self._structured = chat.with_structured_output(RawItemSpecs)
        self._triage_structured = chat.with_structured_output(TenderTriage)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        model: str | None = None,
    ) -> DeepSeekClient:
        return cls(settings, model=model)

    def extract_item_specs(self, text: str) -> RawItemSpecs:
        """Extract material/colour/size/thickness/pack hints from item text."""

        messages: list[Any] = [
            ("system", _ITEM_PROMPT),
            ("human", text),
        ]
        result = self._structured.invoke(messages)
        if isinstance(result, RawItemSpecs):
            return result
        if isinstance(result, dict):
            return RawItemSpecs.model_validate(result)
        return RawItemSpecs.model_validate(result.model_dump())

    def triage_tender(
        self,
        text: str,
        *,
        cpv_codes: list[str] | None = None,
    ) -> TenderTriage:
        """Classify whether a tender belongs to the hygiene/cleaning portfolio."""

        cpv_line = ", ".join(cpv_codes or []) or "none supplied"
        result = self._triage_structured.invoke(
            [
                ("system", _TRIAGE_PROMPT),
                ("human", f"CPV codes: {cpv_line}\n\nTender text:\n{text}"),
            ]
        )
        if isinstance(result, TenderTriage):
            return result
        if isinstance(result, dict):
            return TenderTriage.model_validate(result)
        return TenderTriage.model_validate(result.model_dump())

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
