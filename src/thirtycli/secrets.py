"""Keep API keys out of anything the terminal prints."""

from __future__ import annotations

import re

# Provider token shapes seen in the wild: OpenAI/DeepSeek/OpenRouter (`sk-`),
# Anthropic (`sk-ant-`), Groq (`gsk_`), xAI (`xai-`), Google (`AIza`), Hugging Face.
_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"sk-ant-[A-Za-z0-9_\-]{6,}"
    r"|sk-[A-Za-z0-9_\-]{8,}"
    r"|gsk_[A-Za-z0-9_\-]{8,}"
    r"|xai-[A-Za-z0-9_\-]{8,}"
    r"|AIza[0-9A-Za-z_\-]{10,}"
    r"|hf_[A-Za-z0-9]{10,}"
    r")"
)
_AUTH = re.compile(r"(?i)\b(authorization\s*:\s*(?:bearer\s+)?|bearer\s+)([A-Za-z0-9._~+/=\-]{8,})")
_ASSIGN = re.compile(r"(?i)\b(api[_-]?key|secret|token)(\s*[=:]\s*)([\"']?)([A-Za-z0-9._\-]{8,})")


def mask_token(token: str) -> str:
    """`sk-abcdef1234567890` -> `sk-****7890`; very short tokens hide fully."""
    token = (token or "").strip()
    if not token:
        return "-"
    if len(token) <= 10:
        return token[:3] + "****"
    return token[:3] + "****" + token[-4:]


def redact(text: str) -> str:
    """Mask anything that looks like a credential, leaving the rest untouched."""
    if not text:
        return text
    if not isinstance(text, str):
        text = str(text)
    text = _AUTH.sub(lambda match: match.group(1) + "****", text)
    text = _ASSIGN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{match.group(3)}****", text
    )
    return _TOKEN.sub(lambda match: mask_token(match.group(0)), text)


mask_key = mask_token
