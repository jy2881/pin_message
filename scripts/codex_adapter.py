#!/usr/bin/env python3
"""Codex rollout 适配器：把 ~/.codex/sessions/**/rollout-*.jsonl 归一化为
与 Claude Code transcript 同构的条目流 {type, uuid, timestamp, message.content}，
使下游的过滤/配对逻辑双平台复用。

CODEX-FORMAT: verify on real machine —— 本机无 codex CLI，适配器按公开文档
+ 合成 fixture 实现；未知 schema 一律 fail-loud（CodexParseError，附行样本），
绝不静默猜。

归一化规则：
    {"type": "user_message", "message": "..."}                    → user 条目
    {"type": "response_item", "payload": {"type": "message",
        "role": user|assistant, "content": [{input_text|output_text}]}} → 对应条目
    payload.type ∈ _CODEX_KNOWN_SKIP（工具回传/推理等）            → 排除
    顶层 type ∈ _CODEX_KNOWN_TOP（turn_context 等）                → 排除
    其余未知 schema 连续 ≥ BAD_FORMAT_CONSECUTIVE 行               → CodexParseError
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterator, Optional

CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
BAD_FORMAT_CONSECUTIVE = 20  # 连续未知 schema 行数阈值（与 pin.py 的 CC 阈值一致）

# payload.type 已知噪声（工具回传 / 推理 / 检索），直接排除
_CODEX_KNOWN_SKIP = frozenset({
    "function_call", "function_call_output",
    "local_shell_call", "local_shell_call_output",
    "reasoning", "web_search_call", "mcp_call", "mcp_call_output",
})

# 顶层 type 已知非条目行，直接排除
_CODEX_KNOWN_TOP = frozenset({"turn_context", "token_count", "session_meta"})


class CodexParseError(Exception):
    """codex rollout 行 schema 与预期不符（codex 可能升级了格式）。fail-loud。"""


def locate_codex_transcript() -> Optional[Path]:
    """定位 Codex 最近会话：CODEX_SESSIONS_DIR 下 mtime 最新的 rollout-*.jsonl。

    Codex rollout 不按 cwd 分组，"当前 session" = 全局最新文件；并行会话会误选
    （同 CC v1 竞态，warn 处理，v3 再说）。目录不存在 → None。
    """
    if not CODEX_SESSIONS_DIR.is_dir():
        return None
    newest: Optional[Path] = None
    for p in CODEX_SESSIONS_DIR.rglob("rollout-*.jsonl"):
        if newest is None or p.stat().st_mtime > newest.stat().st_mtime:
            newest = p
    return newest


def classify_codex_line(obj: dict) -> tuple[str, Optional[dict]]:
    """把一行 codex rollout JSON 分类归一化。

    返回 ("entry", 归一化条目) / ("skip", None)（已知噪声或空内容）/
    ("unknown", None)（无法识别的 schema，计入 fail-loud）。
    """
    top = obj.get("type")
    if top in _CODEX_KNOWN_TOP:
        return "skip", None
    if top == "user_message":
        text = (obj.get("message") or "").strip()
        if not text:
            return "skip", None
        return "entry", {
            "type": "user",
            "uuid": str(obj.get("uuid") or ""),
            "timestamp": str(obj.get("timestamp") or ""),
            "message": {"content": [{"type": "text", "text": text}]},
        }
    if top == "response_item":
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            return "skip", None
        ptype = payload.get("type")
        if ptype in _CODEX_KNOWN_SKIP:
            return "skip", None
        if ptype == "message":
            role = payload.get("role")
            content = payload.get("content")
            if role in ("user", "assistant") and isinstance(content, list):
                text = "".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") in ("input_text", "output_text")
                )
                if text.strip():
                    return "entry", {
                        "type": role,
                        "uuid": str(payload.get("id") or obj.get("uuid") or ""),
                        "timestamp": str(payload.get("timestamp") or obj.get("timestamp") or ""),
                        "message": {"content": [{"type": "text", "text": text}]},
                    }
            return "skip", None  # message 结构异常/空文本 → 已知形态，不算 unknown
        return "unknown", None  # 未知 payload 类型 → fail-loud 计数
    return "unknown", None  # 未知顶层类型 → fail-loud 计数


def iter_codex_entries(path: Path) -> Iterator[dict]:
    """逐行解析 codex rollout JSONL，归一化为 CC 同构条目流。

    单行损坏 → 跳过计数（stderr）；连续 BAD_FORMAT_CONSECUTIVE 行 unknown →
    CodexParseError（fail-loud，附首行样本，不静默猜）。
    """
    unknown_run = 0
    malformed = 0
    sample = ""
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                unknown_run = 0
                continue
            kind, normalized = classify_codex_line(obj) if isinstance(obj, dict) else ("unknown", None)
            if kind == "entry":
                unknown_run = 0
                yield normalized  # type: ignore[misc]
                continue
            if kind == "skip":
                unknown_run = 0
                continue
            unknown_run += 1
            if not sample:
                sample = line[:200]
            if unknown_run >= BAD_FORMAT_CONSECUTIVE:
                raise CodexParseError(
                    f"{path} 连续 {BAD_FORMAT_CONSECUTIVE} 行无法识别"
                    f"（codex rollout 格式可能已升级）。首行样本：{sample}"
                )
    if malformed:
        print(f"pin.py: 跳过 {malformed} 行损坏的 JSONL", file=sys.stderr)
