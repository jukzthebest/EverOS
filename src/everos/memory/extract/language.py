"""Memory extraction language helpers."""

from __future__ import annotations

import json
import re

from everalgo.llm.protocols import LLMClient

from everos.component.llm import ChatMessage
from everos.config import load_settings
from everos.core.observability.logging import get_logger

logger = get_logger(__name__)

_SAME_LANGUAGE_RULE = (
    "**CRITICAL LANGUAGE RULE**: You MUST output in the SAME language as the "
    "input conversation content. If the conversation content is in Chinese, ALL "
    "output MUST be in Chinese. If in English, output in English. This is mandatory."
)
_ZH_LANGUAGE_RULE = (
    "**CRITICAL LANGUAGE RULE**: You MUST output all natural-language memory "
    "content in Simplified Chinese. Preserve code, commands, paths, URLs, ids, "
    "config keys, schema field names, proper nouns, and necessary quotes verbatim."
)
_ZH_CUSTOM_INSTRUCTIONS = (
    "所有自然语言记忆内容必须使用简体中文，包括 subject、summary、episode、fact、"
    "foresight、evidence、profile、description、basis 等字段值。代码、命令、路径、"
    "URL、ID、配置键和必要引用保持原样。"
)


def memory_prompt(default_prompt: str) -> str | None:
    """Return a localized prompt override, or ``None`` for upstream default."""
    if load_settings().llm.extraction_language != "zh":
        return None
    if _SAME_LANGUAGE_RULE in default_prompt:
        return default_prompt.replace(_SAME_LANGUAGE_RULE, _ZH_LANGUAGE_RULE)
    return f"{_ZH_LANGUAGE_RULE}\n\n{default_prompt}"


def episode_custom_instructions() -> str | None:
    """Extra EpisodeExtractor instructions matching the configured language."""
    if load_settings().llm.extraction_language != "zh":
        return None
    return _ZH_CUSTOM_INSTRUCTIONS


async def localize_texts(llm: LLMClient, texts: list[str]) -> list[str]:
    """Translate memory field values to Simplified Chinese when configured."""
    if load_settings().llm.extraction_language != "zh" or not texts:
        return texts
    try:
        response = await llm.chat(
            [
                ChatMessage(
                    role="user",
                    content=(
                        "请把下面 JSON 数组中的自然语言记忆文本改写为简体中文，"
                        "返回且只返回等长 JSON 字符串数组。保留代码、命令、路径、URL、"
                        "ID、配置键、英文专有名词和必要引用原样。\n\n"
                        f"{json.dumps(texts, ensure_ascii=False)}"
                    ),
                )
            ],
            temperature=0,
        )
        parsed = _extract_json_array(response.content)
        if len(parsed) != len(texts) or not all(isinstance(v, str) for v in parsed):
            raise ValueError("localized response is not an equal-length string array")
        return list(parsed)
    except Exception as exc:
        logger.warning("memory_text_localization_failed", error=str(exc))
        return texts


def _extract_json_array(text: str) -> list[object]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", text)
        if match is None:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, list):
        raise ValueError("localized response is not a JSON array")
    return parsed
