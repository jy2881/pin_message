#!/usr/bin/env python3
"""pin_message engine — Claude Code / Codex 双平台对话 pin 插件的确定性引擎层。

命令提示词（commands/*.md）通过 Bash 调用本子命令，引擎负责一切解析/读写/输出，
模型只负责把引擎输出的编号列表渲染给用户并引导用户回复数字（统一编号列表
协议，替代 v1 的 AskUserQuestion 分页协议；会话内无原生选择器可用，编号列表
是跨 CLI 社区标准解法）。

解析管线（按 --platform 选择来源）：

    cc     ~/.claude/projects/<slug>/*.jsonl
    codex  ~/.codex/sessions/**/rollout-*.jsonl（mtime 最新者）
              │
              ▼  平台探测：auto → 两侧皆存在取 mtime 新者（stderr warn）；
              │              皆无 → no_transcript_dir
        entries（codex 行归一化为与 CC 同构的 {type,uuid,timestamp,message.content}）
              │
              ▼  CC：6 条过滤；Codex：排除 function_call*/local_shell_call*/
              │       turn_context/reasoning 等已知噪声；未知 schema 连续 ≥20 行
              │       → codex_parse fail-loud（附行样本，不静默猜）
        真实用户提问
              │
              ▼  配对：其后最近 textual assistant；遇下一合格 user 即停止
        QA 对 ──compress──▶ display（[Pasted text #N +X lines] 风格）

编号列表协议（v2，替代 v1 分页协议）：
    list_items 一次输出 ≤15 条（MAX_ITEMS，truncated 标记截断），每条
    {index, display, ts, session, uuid, pin_no, preview}；
    show/delete 的位置参数为纯数字 = pin_no 直达，跳过列表。

输出契约（命令提示词依赖，勿随意改）：
    qa_pair / search_results / pin_content / write_result /
    delete_result / warn / error   —— 均为单行 JSON 到 stdout
"""

from __future__ import annotations

import argparse
try:
    import fcntl  # POSIX 文件锁；Windows 无 fcntl → 降级为不加锁（单用户桌面场景可接受）
except ImportError:
    fcntl = None
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

# ---------------------------------------------------------------- 常量

PIN_FILE = Path(os.environ.get("PIN_MESSAGE_FILE") or (Path.home() / ".claude" / "pin_message.md"))
PROJECTS_DIR = Path.home() / ".claude" / "projects"
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
FORMAT_HEADER = "<!-- pin-message-format: 1 -->"
STALE_SECONDS = 300          # transcript mtime 早于 5 分钟视为可疑
LOCK_TIMEOUT = 2.0           # flock 等待秒数
BAD_FORMAT_CONSECUTIVE = 20  # 连续缺键/未知 schema 行数阈值
LONG_TEXT_THRESHOLD = 500    # 单条文本超过此长度触发压缩（verbatim 形态）
DISPLAY_MAX = 80             # display 摘要手打文字最大长度
MAX_ITEMS = 15               # 编号列表单次截断上限（分页协议已删）

ERROR_CODES = (
    "empty_session|no_match|lock_timeout|bad_format|codex_parse|io_error"
    "|no_transcript_dir|no_pins|duplicate"
)


# ---------------------------------------------------------------- 输出


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def fail(code: str, message: str, exit_code: int = 0) -> "None":
    emit({"type": "error", "code": code, "message": message})
    sys.exit(exit_code)


# ================================================================ transcript 段（CC）


class BadFormatError(Exception):
    """transcript 结构与预期不符（CLI 可能升级了格式）。"""


def slug_for(cwd: str) -> str:
    """cwd → Claude Code 项目目录 slug。

    CC 实测编码规则：把路径中每段非 [A-Za-z0-9] 字符（`/`、`_` 等）替换为单个 `-`，
    大小写保留。例：/Users/pan/pythonProjects/face_recon → -Users-pan-pythonProjects-face-recon。
    （旧实现只替换 `/`，漏掉 `_`，导致含下划线目录的项目找不到 transcript。）
    """
    return re.sub(r"[^A-Za-z0-9]+", "-", cwd)


def locate_transcript(cwd: str) -> tuple[Optional[Path], Optional[dict]]:
    """定位 CC 当前 session 的 transcript（cwd-slug 目录下 mtime 最新者）。

    返回 (path, payload)。目录不存在/无 jsonl → (None, error 载荷)。
    mtime 早于 STALE_SECONDS → (path, stale_transcript warn)。
    """
    slug_dir = PROJECTS_DIR / slug_for(cwd)
    if not slug_dir.is_dir():
        return None, {
            "type": "error",
            "code": "no_transcript_dir",
            "message": f"找不到 CC 会话记录目录：{slug_dir}",
        }
    files = sorted(slug_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not files:
        return None, {
            "type": "error",
            "code": "no_transcript_dir",
            "message": f"目录中没有任何 .jsonl 会话文件：{slug_dir}",
        }
    newest = files[-1]
    if time.time() - newest.stat().st_mtime > STALE_SECONDS:
        return newest, {
            "type": "warn",
            "code": "stale_transcript",
            "message": (
                f"最新会话文件 {newest.name} 的修改时间超过 "
                f"{STALE_SECONDS // 60} 分钟，可能是后台会话。确认后继续。"
            ),
        }
    return newest, None


def iter_entries(path: Path) -> Iterator[dict]:
    """逐行解析 CC transcript JSONL。

    单行 JSON 损坏 → 跳过（stderr 计数警告）；连续 BAD_FORMAT_CONSECUTIVE 行
    虽为合法 JSON 但缺预期键 → BadFormatError（fail-loud，不静默猜）。
    """
    missing_keys_run = 0
    malformed = 0
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                missing_keys_run = 0
                continue
            if not isinstance(entry, dict) or not all(
                k in entry for k in ("type", "uuid", "timestamp")
            ):
                missing_keys_run += 1
                if missing_keys_run >= BAD_FORMAT_CONSECUTIVE:
                    raise BadFormatError(
                        f"{path} 连续 {BAD_FORMAT_CONSECUTIVE} 行缺少预期字段"
                        f"（type/uuid/timestamp），transcript 格式可能已升级"
                    )
                continue
            missing_keys_run = 0
            yield entry
    if malformed:
        print(f"pin.py: 跳过 {malformed} 行损坏的 JSONL", file=sys.stderr)


def entry_text(entry: dict) -> str:
    """提取条目的纯文本：拼接 message.content 中所有 text 块。"""
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else entry.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def has_tool_result(entry: dict) -> bool:
    """是否含 tool_result 块（工具回传，userType/isSidechain 与真实提问不可区分，
    必须按块类型排除）。"""
    if entry.get("toolUseID"):
        return True
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else entry.get("content")
    if isinstance(content, list):
        return any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in content
        )
    return False


def is_real_user_question(entry: dict) -> bool:
    """6 条过滤规则，判定一条 type:user 条目是否为真实用户提问。"""
    if entry.get("type") != "user":
        return False
    if entry.get("isSidechain"):
        return False
    if entry.get("isMeta"):
        return False
    if has_tool_result(entry):
        return False
    text = entry_text(entry)
    if "<command-name>" in text:  # 斜杠命令调用自身（实测格式非 /xxx 开头）
        return False
    if text.startswith("[Request interrupted"):  # 系统注入的打断标记
        return False
    return bool(text.strip())


def is_textual_assistant(entry: dict) -> bool:
    """assistant 条目且至少含一个 text 块（跳过仅 thinking/tool_use 的）。"""
    if entry.get("type") != "assistant":
        return False
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, list):
        return any(
            isinstance(b, dict) and b.get("type") == "text" for b in content
        )
    return False


def extract_qa_pairs(entries: list[dict]) -> list[dict]:
    """从解析后的条目流提取 QA 对列表（按时间顺序）。

    配对规则：真实提问 → 其后最近的 textual assistant；
    搜索遇下一个真实提问（或文件结束）即停止 —— 防止打断后跨界配对。
    """
    pairs: list[dict] = []
    i = 0
    n = len(entries)
    while i < n:
        entry = entries[i]
        if not is_real_user_question(entry):
            i += 1
            continue
        answer = None
        answer_ts = None
        j = i + 1
        while j < n:
            nxt = entries[j]
            if is_real_user_question(nxt):
                break  # 配对边界：下一个合格提问出现即止
            if is_textual_assistant(nxt):
                answer = entry_text(nxt)
                answer_ts = nxt.get("timestamp", "")
                break
            j += 1
        pairs.append(
            {
                "question": entry_text(entry),
                "answer": answer if answer is not None else "",
                "uuid": entries[j].get("uuid", "") if (answer is not None and j < n) else "",
                "timestamp": answer_ts or entry.get("timestamp", ""),
                "has_answer": answer is not None,
            }
        )
        i = max(j, i + 1) if answer is not None else i + 1
    return pairs


_PASTED_PLACEHOLDER_RE = re.compile(r"^\[Pasted text")


def compress_display(text: str) -> str:
    """把用户提问压缩成一行 display 摘要。

    两种 transcript 形态都有预案：
    (a) 记录为 [Pasted text #N +X lines] 占位符 → 保留占位符原样；
    (b) 原文 verbatim 且总长 > 阈值 → 压缩为 [Pasted text +N lines] 风格。
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return "(空问题)"
    placeholders = [ln.strip() for ln in lines if _PASTED_PLACEHOLDER_RE.match(ln.strip())]
    typed = [ln.strip() for ln in lines if not _PASTED_PLACEHOLDER_RE.match(ln.strip())]
    head = (typed[0] if typed else "")[:DISPLAY_MAX]
    if placeholders:
        suffix = " ".join(placeholders)
        return f"{head} {suffix}".strip()
    if len(text) > LONG_TEXT_THRESHOLD:
        suffix = f"[Pasted text +{len(lines)} lines]"
        return f"{head} {suffix}".strip()
    return " ".join(lines)[:DISPLAY_MAX]


# ================================================================ codex 段
# CODEX-FORMAT: verify on real machine —— 实现见 codex_adapter.py（本文件超 800 行
# 上限后按 Eng Review 决策拆分）；未知 schema 一律 fail-loud（codex_parse）。

from codex_adapter import (  # noqa: E402
    CODEX_SESSIONS_DIR,
    CodexParseError,
    iter_codex_entries,
    locate_codex_transcript,
)


# ================================================================ 平台探测


def resolve_transcript(
    cwd: str, platform: str
) -> tuple[Optional[Path], Optional[str], Optional[dict]]:
    """按 platform（auto|cc|codex）定位 transcript。

    返回 (path, source, payload)：payload 为 warn（不阻塞）或 error（中止）。
    auto：两侧皆存在 → mtime 新者胜 + platform_ambiguous warn；皆无 → no_transcript_dir。
    """
    cc_path = cx_path = None
    cc_payload: Optional[dict] = None
    if platform in ("auto", "cc"):
        cc_path, cc_payload = locate_transcript(cwd)
    if platform in ("auto", "codex"):
        cx_path = locate_codex_transcript()

    if platform == "auto":
        if cc_path and cx_path:
            source, newest = max(
                (("cc", cc_path), ("codex", cx_path)),
                key=lambda t: t[1].stat().st_mtime,
            )
            warn = {
                "type": "warn",
                "code": "platform_ambiguous",
                "message": (
                    "CC 与 Codex 会话记录同时存在，已选用较新的 "
                    f"{source} 侧；可用 --platform cc|codex 强制指定"
                ),
            }
            return newest, source, warn
        if cc_path:
            return cc_path, "cc", cc_payload
        if cx_path:
            return cx_path, "codex", None
        return None, None, {
            "type": "error",
            "code": "no_transcript_dir",
            "message": (
                "找不到会话记录目录：CC ~/.claude/projects 与 "
                "Codex ~/.codex/sessions 均不存在"
            ),
        }
    if platform == "cc":
        if cc_path:
            return cc_path, "cc", cc_payload
        return None, None, cc_payload or {
            "type": "error",
            "code": "no_transcript_dir",
            "message": f"找不到 CC 会话记录目录：{PROJECTS_DIR / slug_for(cwd)}",
        }
    # codex
    if cx_path:
        return cx_path, "codex", None
    return None, None, {
        "type": "error",
        "code": "no_transcript_dir",
        "message": f"找不到 Codex 会话记录目录：{CODEX_SESSIONS_DIR}",
    }


# ================================================================ pinstore 段


def parse_store(text: str) -> list[dict]:
    """解析 pin 文件为条目列表。无格式头 → 视为损坏（调用方负责备份重建）。"""
    if not text.strip():
        return []
    if FORMAT_HEADER not in text.splitlines()[0]:
        raise ValueError("pin_message.md 缺少格式头，文件可能损坏或被手工修改")
    entries: list[dict] = []
    current: Optional[dict] = None
    section = None  # "q" | "a"
    for line in text.splitlines()[1:]:
        if line.startswith("## [PIN #"):
            if current:
                entries.append(current)
            header = line
            m = re.match(r"## \[PIN #(\d+)\]", header)
            pin_no = int(m.group(1)) if m else 0
            session = header.split("|")[-1].strip() if "|" in header else ""
            if session.startswith("session "):
                session = session[len("session "):]
            current = {
                "pin_no": pin_no,
                "uuid": "",
                "session": session,
                "timestamp": "",
                "question": "",
                "answer": "",
            }
            section = None
        elif line.startswith("<!-- pin-uuid:"):
            if current is not None:
                current["uuid"] = line.split(":")[1].strip().rstrip(" -->").strip()
        elif line.startswith("**Q:**"):
            if current is not None:
                current["question"] = line[len("**Q:**"):].strip()
                section = "q"
        elif line.startswith("**A:**"):
            if current is not None:
                section = "a"
        elif current is not None and section in ("q", "a"):
            key = "question" if section == "q" else "answer"
            current[key] = (current[key] + "\n" + line).strip("\n") if current[key] else line
    if current:
        entries.append(current)
    return entries


def render_store(entries: list[dict]) -> str:
    """条目列表 → pin 文件全文（重新顺序编号）。"""
    parts = [FORMAT_HEADER, "", "# Pinned Messages", ""]
    for i, e in enumerate(entries, 1):
        ts = e.get("timestamp", "")[:16].replace("T", " ")
        parts.append(f"## [PIN #{i}] {ts} | session {e.get('session', '')}")
        parts.append(f"<!-- pin-uuid: {e.get('uuid', '')} -->")
        parts.append(f"**Q:** {e.get('question', '')}")
        parts.append("**A:**")
        parts.append(e.get("answer", ""))
        parts.append("")
    return "\n".join(parts)


def read_store() -> tuple[list[dict], Optional[str]]:
    """读取 pin 文件。不存在 → 空列表；损坏 → (备份路径, 空列表)。"""
    if not PIN_FILE.exists():
        return [], None
    text = PIN_FILE.read_text(encoding="utf-8")
    try:
        return parse_store(text), None
    except ValueError:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = PIN_FILE.with_name(f"{PIN_FILE.name}.bak.{stamp}")
        backup.write_text(text, encoding="utf-8")
        return [], str(backup)


def atomic_write(path: Path, content: str) -> None:
    """tmp + rename 原子写。"""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".pin_tmp_", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class PinStore:
    """flock 保护的 read-modify-write。"""

    def __init__(self) -> None:
        self._lock_path = PIN_FILE.with_name(PIN_FILE.name + ".lock")

    def __enter__(self) -> "PinStore":
        PIN_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self._lock_path.open("a+")
        if fcntl is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self._fh.close()
                fail("lock_timeout", "另一个会话正在写入 pin 文件，请重试", exit_code=1)
        return self

    def __exit__(self, *exc: Any) -> None:
        if fcntl is not None:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        self._fh.close()

    def load(self) -> tuple[list[dict], Optional[str]]:
        return read_store()

    def save(self, entries: list[dict]) -> None:
        atomic_write(PIN_FILE, render_store(entries))

    def append(self, uuid: str, session: str, question: str, answer: str,
               timestamp: str) -> tuple[bool, int]:
        """追加一条。同 uuid 已存在 → (False, 已有编号)。成功 → (True, 新编号)。"""
        entries, backup = self.load()
        for e in entries:
            if e.get("uuid") and uuid and e["uuid"] == uuid:
                return False, e["pin_no"]
        entries.append({
            "pin_no": len(entries) + 1,
            "uuid": uuid,
            "session": session,
            "timestamp": timestamp,
            "question": question,
            "answer": answer,
        })
        self.save(entries)
        return True, len(entries)

    def delete(self, pin_no: int) -> Optional[dict]:
        entries, _ = self.load()
        target = next((e for e in entries if e["pin_no"] == pin_no), None)
        if target is None:
            return None
        entries = [e for e in entries if e["pin_no"] != pin_no]
        self.save(entries)  # render_store 重新顺序编号
        return target


# ================================================================ cli 段


def session_id_of(path: Path) -> str:
    """session 标识：CC 取文件名前 8 字符；codex rollout-<ts>-<uuid> 取末段 uuid 前 8。"""
    name = path.stem
    if name.startswith("rollout-"):
        return name.split("-")[-1][:8]
    return name[:8]


def list_items(items: list[dict]) -> dict:
    """编号列表协议：一次输出 ≤MAX_ITEMS 条，truncated 标记截断（分页协议已删）。"""
    chunk = items[:MAX_ITEMS]
    return {
        "type": "search_results",
        "total": len(items),
        "truncated": len(items) > MAX_ITEMS,
        "items": [
            {
                "index": i + 1,
                "display": e["display"],
                "ts": (e.get("timestamp", "") or "")[:16].replace("T", " "),
                "session": e.get("session", ""),
                "uuid": e.get("uuid", ""),
                "pin_no": e.get("pin_no"),
                "preview": (e.get("answer", "") or "")[:200],
            }
            for i, e in enumerate(chunk)
        ],
    }


def filter_query(items: list[dict], query: Optional[str], key: str = "display") -> list[dict]:
    """大小写不敏感子串匹配，只匹配问题摘要不匹配回答正文。"""
    if not query:
        return items
    q = query.lower()
    return [e for e in items if q in e.get(key, "").lower()]


def load_pairs(
    cwd: str, platform: str
) -> tuple[Optional[list[dict]], Optional[Path], Optional[str], Optional[dict]]:
    """平台探测 → transcript 解析 → QA 对。

    返回 (pairs, path, source, payload)：payload 为 warn（不阻塞）或 error（中止）。
    codex 解析失败 → codex_parse 错误码（fail-loud）；CC → bad_format。
    """
    path, source, payload = resolve_transcript(cwd, platform)
    if path is None:
        return None, None, None, payload
    try:
        if source == "codex":
            entries = list(iter_codex_entries(path))
        else:
            entries = list(iter_entries(path))
    except CodexParseError as exc:
        return None, None, source, {"type": "error", "code": "codex_parse", "message": str(exc)}
    except BadFormatError as exc:
        return None, None, source, {"type": "error", "code": "bad_format", "message": str(exc)}
    return extract_qa_pairs(entries), path, source, payload


def emit_warn_if_any(payload: Optional[dict]) -> None:
    """warn 载荷打到 stderr（stdout 保持单行 JSON 契约），不阻塞执行。"""
    if payload and payload.get("type") == "warn":
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)


def _fail_if_error(payload: Optional[dict]) -> bool:
    """error 载荷 → emit + True（调用方 return）；否则 False。"""
    if payload and payload.get("type") == "error":
        emit(payload)
        return True
    return False


def _pin_no_from_args(args: argparse.Namespace) -> Optional[int]:
    """数字直达：--pin-no 或位置参数 target 为纯数字 → pin_no；否则 None（走列表）。"""
    if getattr(args, "pin_no", None) is not None:
        return args.pin_no
    target = getattr(args, "target", None)
    if target and target.isdigit():
        return int(target)
    return None


def cmd_last(args: argparse.Namespace) -> None:
    pairs, path, source, payload = load_pairs(args.cwd, args.platform)
    if _fail_if_error(payload):
        return
    emit_warn_if_any(payload)
    if not pairs:
        fail("empty_session", "当前 session 还没有可 pin 的对话")
    pair = pairs[-1]
    emit({
        "type": "qa_pair",
        "question": pair["question"],
        "answer": pair["answer"],
        "uuid": pair["uuid"],
        "session": session_id_of(path),
        "display": compress_display(pair["question"]),
    })


def cmd_search(args: argparse.Namespace) -> None:
    pairs, path, source, payload = load_pairs(args.cwd, args.platform)
    if _fail_if_error(payload):
        return
    emit_warn_if_any(payload)
    if not pairs:
        fail("empty_session", "当前 session 还没有可 pin 的对话")
    recent = list(reversed(pairs))  # 倒序，最近在前；列表层截断到 MAX_ITEMS 并标记 truncated
    items = [
        {
            "display": compress_display(p["question"]),
            "uuid": p["uuid"],
            "session": session_id_of(path),
            "timestamp": p.get("timestamp", ""),
            "answer": p["answer"],
            "question": p["question"],
        }
        for p in recent
    ]
    items = filter_query(items, args.query)
    if not items:
        fail("no_match", f"没有匹配「{args.query}」的提问")
    emit(list_items(items))


def store_items_for_listing(query: Optional[str]) -> list[dict]:
    entries, backup = read_store()
    items = [
        {
            "display": e["question"] or "(空问题)",
            "uuid": e.get("uuid", ""),
            "pin_no": e["pin_no"],
            "answer": e.get("answer", ""),
            "question": e.get("question", ""),
            "session": e.get("session", ""),
            "timestamp": e.get("timestamp", ""),
        }
        for e in reversed(entries)  # 最近 pin 在前
    ]
    return filter_query(items, query)


def cmd_pins(args: argparse.Namespace) -> None:
    items = store_items_for_listing(getattr(args, "query", None))
    if not items:
        if getattr(args, "query", None):
            fail("no_match", f"没有匹配「{args.query}」的 pin")
        fail("no_pins", "（还没有 pinned 消息）")
    emit(list_items(items))


def cmd_show(args: argparse.Namespace) -> None:
    pin_no = _pin_no_from_args(args)
    if pin_no is None:
        cmd_pins(args)  # 渲染编号列表；提示词引导用户回复数字后 /pin_show N 直达
        return
    entries, _ = read_store()
    target = next((e for e in entries if e["pin_no"] == pin_no), None)
    if target is None:
        fail("no_match", f"PIN #{pin_no} 不存在")
    emit({
        "type": "pin_content",
        "pin_no": target["pin_no"],
        "question": target["question"],
        "answer": target["answer"],
    })


def find_pair_by_uuid(
    cwd: str, platform: str, uuid: str
) -> tuple[Optional[dict], Optional[Path], Optional[dict]]:
    pairs, path, source, payload = load_pairs(cwd, platform)
    if payload and payload.get("type") == "error":
        return None, None, payload
    for p in pairs or []:
        if p["uuid"] and p["uuid"] == uuid:
            return p, path, None
    return None, None, {"type": "error", "code": "no_match",
                        "message": f"当前 session 中找不到 uuid={uuid} 的问答对"}


def write_pair(pair: dict, session: str) -> None:
    with PinStore() as store:
        ok, pin_no = store.append(
            uuid=pair.get("uuid", ""),
            session=session,
            question=compress_display(pair["question"]),
            answer=pair.get("answer", ""),
            timestamp=pair.get("timestamp", ""),
        )
    if not ok:
        emit({"type": "error", "code": "duplicate",
              "message": f"该轮已在 PIN #{pin_no} 中"})
        return
    emit({"type": "write_result", "ok": True, "pin_no": pin_no})


def cmd_write(args: argparse.Namespace) -> None:
    pair, path, err = find_pair_by_uuid(args.cwd, args.platform, args.uuid)
    if err:
        emit(err)
        return
    write_pair(pair, session_id_of(path))


def cmd_write_last(args: argparse.Namespace) -> None:
    pairs, path, source, payload = load_pairs(args.cwd, args.platform)
    if _fail_if_error(payload):
        return
    emit_warn_if_any(payload)
    if not pairs:
        fail("empty_session", "当前 session 还没有可 pin 的对话")
    write_pair(pairs[-1], session_id_of(path))


def cmd_delete(args: argparse.Namespace) -> None:
    pin_no = _pin_no_from_args(args)
    if pin_no is None:
        cmd_pins(args)  # 渲染编号列表；提示词引导用户回复数字后 /pin_delete N 直达
        return
    with PinStore() as store:
        removed = store.delete(pin_no)
    if removed is None:
        fail("no_match", f"PIN #{pin_no} 不存在")
    entries, _ = read_store()
    emit({
        "type": "delete_result",
        "ok": True,
        "removed_pin_no": pin_no,
        "remaining": len(entries),
    })


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(prog="pin.py")
    parser.add_argument("--cwd", default=os.getcwd())
    parser.add_argument("--platform", choices=("auto", "cc", "codex"), default="auto")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("last")

    p = sub.add_parser("search")
    p.add_argument("query", nargs="?")

    p = sub.add_parser("pins")
    p.add_argument("query", nargs="?")

    p = sub.add_parser("show")
    p.add_argument("target", nargs="?")
    p.add_argument("--pin-no", type=int)

    p = sub.add_parser("write")
    p.add_argument("--uuid", required=True)

    sub.add_parser("write-last")

    p = sub.add_parser("delete")
    p.add_argument("target", nargs="?")
    p.add_argument("--pin-no", type=int)

    args = parser.parse_args(argv)
    handlers = {
        "last": cmd_last,
        "search": cmd_search,
        "pins": cmd_pins,
        "show": cmd_show,
        "write": cmd_write,
        "write-last": cmd_write_last,
        "delete": cmd_delete,
    }
    try:
        handlers[args.cmd](args)
    except BrokenPipeError:
        pass
    except OSError as exc:
        fail("io_error", f"文件操作失败：{exc}", exit_code=1)


if __name__ == "__main__":
    main()
