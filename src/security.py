"""Inbound injection filtering and outbound secret sanitization."""

from __future__ import annotations

import re

INJECTION_PATTERNS = [
    "ignore previous instructions",
    "ignore all previous",
    "system prompt",
    "developer instruction",
    "reveal your prompt",
    "jailbreak",
    "jail break",
    "越狱",
    "忽略之前的指令",
    "忽略以上",
    "忘记你的规则",
    "提示词泄露",
    "脱敏失效",
]

OUTBOUND_PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "<api_key>"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-\.]{20,}\b"), "<jwt>"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9_\-\.]{20,}"), r"\1 <token>"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|token|access[_-]?key)\s*[=:]\s*[^\s,;\"']+"), r"\1=<redacted>"),
    (
        re.compile(r"\b(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"),
        "<private_ip>",
    ),
    (re.compile(r"/etc/(?:shadow|sudoers)(?:\.d)?\b"), "<path>"),
    (re.compile(r"(?:configs/)?\.env\b"), "<env>"),
]

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def detect_injection(text: str) -> bool:
    lowered = text.lower()
    return any(pattern in lowered for pattern in INJECTION_PATTERNS)


def inbound_sanitize(text: str) -> str:
    return CONTROL_CHARS.sub("", text).strip()


def outbound_sanitize(text: str) -> str:
    for pattern, replacement in OUTBOUND_PATTERNS:
        text = pattern.sub(replacement, text)
    return text
