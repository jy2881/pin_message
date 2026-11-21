"""pin.py 引擎测试。

fixture 构造说明：
- 大部分条目用合成 dict（isSidechain 等极端 case 本地无真实数据）
- spec 2 的"真实形态"fixture 从本机 transcript 结构仿制（Claude Code 2.1.63）
- 运行前请将真实 transcript 片段保存到 tests/fixtures/real_session.jsonl（可选，
  缺省时该 spec 自动 fallback 到仿制 fixture，不 fail）
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import pin  # noqa: E402
import codex_adapter  # noqa: E402


# ---------------------------------------------------------------- helpers


def user_entry(text, uuid="u1", **kw):
    e = {
        "type": "user",
        "uuid": uuid,
        "timestamp": "2026-11-20T10:00:00.000Z",
        "isSidechain": False,
        "isMeta": None,
        "userType": "external",
        "message": {"content": [{"type": "text", "text": text}]},
    }
    e.update(kw)
    return e


def assistant_entry(text, uuid="a1", blocks=None, **kw):
    content = blocks if blocks is not None else [{"type": "text", "text": text}]
    e = {
        "type": "assistant",
        "uuid": uuid,
        "timestamp": "2026-11-20T10:00:05.000Z",
        "message": {"content": content},
    }
    e.update(kw)
    return e


def progress_entry(uuid="p1"):
    return {"type": "progress", "uuid": uuid, "timestamp": "2026-11-20T10:00:01.000Z"}


def tool_result_user(uuid="tr1"):
    return {
        "type": "user",
        "uuid": uuid,
        "timestamp": "2026-11-20T10:00:02.000Z",
        "isSidechain": False,
        "isMeta": None,
        "userType": "external",
        "toolUseID": "tool_xxx",
        "message": {
            "content": [
                {"type": "tool_result", "toolUseID": "tool_xxx", "content": "file listing"}
            ]
        },
    }


def write_transcript(tmp_path, entries, name="sess-12345678.jsonl"):
    slug_dir = tmp_path / "projects" / "-tmp-proj"
    slug_dir.mkdir(parents=True)
    f = slug_dir / name
    f.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    return f


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """把 PIN_FILE / PROJECTS_DIR / Codex sessions 重定向到 tmp，cwd 固定为 /tmp/proj。

    v2：auto 平台探测会看真实的 ~/.codex/sessions，必须隔离，否则测试
    结果取决于本机是否装过 codex。
    """
    monkeypatch.setattr(pin, "PIN_FILE", tmp_path / "pin_message.md")
    monkeypatch.setattr(pin, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(codex_adapter, "CODEX_SESSIONS_DIR", tmp_path / "codex_sessions")
    monkeypatch.setattr(pin, "STALE_SECONDS", -1)  # 测试里文件总是"新鲜"
    return {"cwd": "/tmp/proj", "tmp": tmp_path}


# ---------------------------------------------------------------- codex helpers（合成 rollout fixture）


def cx_message(role, text, msg_id):
    """response_item/message 行：role=user 用 input_text，assistant 用 output_text。"""
    block_type = "input_text" if role == "user" else "output_text"
    return {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": role,
            "id": msg_id,
            "content": [{"type": block_type, "text": text}],
        },
    }


def cx_skip(ptype):
    """已知噪声行（工具回传/推理等），带最小可用字段。"""
    return {"type": "response_item", "payload": {"type": ptype, "output": "noise"}}


def cx_top_skip(top):
    """顶层已知非条目行（turn_context 等）。"""
    return {"type": top, "context": "noise"}


def write_rollout(sessions_dir, lines, name="rollout-20261121-090000-abcdef12.jsonl"):
    day_dir = sessions_dir / "2026" / "11" / "21"
    day_dir.mkdir(parents=True, exist_ok=True)
    f = day_dir / name
    f.write_text("\n".join(json.dumps(e) for e in lines), encoding="utf-8")
    return f


# ---------------------------------------------------------------- spec 1


def test_compress_placeholder_form():
    text = "帮我看看这段日志\n[Pasted text #1 +18 lines]\n谢谢"
    d = pin.compress_display(text)
    assert "[Pasted text #1 +18 lines]" in d
    assert "帮我看看这段日志" in d


def test_compress_verbatim_form():
    body = "line content\n" * 100  # >500 chars, 多行
    d = pin.compress_display(body)
    assert d.startswith("line content")
    assert "[Pasted text +100 lines]" in d


def test_compress_short_text():
    assert pin.compress_display("你好") == "你好"


# ---------------------------------------------------------------- spec 2


def test_extract_basic_and_exclusions(env):
    entries = [
        progress_entry(),
        user_entry("真实问题一", uuid="u1"),
        assistant_entry("回答一", uuid="a1"),
        {**user_entry("sidechain 消息", uuid="u2"), "isSidechain": True},
        assistant_entry("不应配对", uuid="a2"),
        {**user_entry("meta 消息", uuid="u3"), "isMeta": True},
        user_entry("真实问题二", uuid="u4"),
        assistant_entry("回答二", uuid="a4"),
    ]
    pairs = pin.extract_qa_pairs(entries)
    assert [p["question"] for p in pairs] == ["真实问题一", "真实问题二"]
    assert pairs[0]["answer"] == "回答一"
    assert pairs[0]["uuid"] == "a1"


# ---------------------------------------------------------------- spec 3


def test_command_message_self_exclusion(env):
    cmd_msg = (
        "<command-message>pin</command-message>\n"
        "<command-name>/pin</command-name>\n<command-args></command-args>"
    )
    entries = [
        user_entry("前一轮问题", uuid="u1"),
        assistant_entry("前一轮回答", uuid="a1"),
        user_entry(cmd_msg, uuid="u2"),
    ]
    pairs = pin.extract_qa_pairs(entries)
    assert len(pairs) == 1
    assert pairs[0]["question"] == "前一轮问题"
    assert pairs[0]["uuid"] == "a1"


# ---------------------------------------------------------------- spec 4


def test_tool_result_not_a_question(env):
    entries = [
        user_entry("真实问题", uuid="u1"),
        assistant_entry("回答", uuid="a1", blocks=[{"type": "tool_use", "name": "Bash"}]),
        tool_result_user(uuid="tr1"),
        user_entry("第二个问题", uuid="u2"),
        assistant_entry("第二个回答", uuid="a2"),
    ]
    pairs = pin.extract_qa_pairs(entries)
    assert [p["question"] for p in pairs] == ["真实问题", "第二个问题"]


# ---------------------------------------------------------------- spec 5


def test_interruption_marker_excluded_and_skip_back(env):
    entries = [
        user_entry("再前一轮", uuid="u0"),
        assistant_entry("回答0", uuid="a0"),
        user_entry("被打断的问题", uuid="u1"),
        assistant_entry("", uuid="a1", blocks=[{"type": "thinking", "thinking": "..."}]),
        user_entry("[Request interrupted by user]", uuid="u2"),
    ]
    pairs = pin.extract_qa_pairs(entries)
    questions = [p["question"] for p in pairs]
    assert "[Request interrupted" not in " ".join(questions)
    # 打断一轮没有 text 回答 → 有 has_answer=False 条目或正确跳过
    assert pairs[0]["question"] == "再前一轮"


# ---------------------------------------------------------------- spec 6


def test_pairing_stops_at_next_question(env):
    entries = [
        user_entry("问题A", uuid="u1"),
        assistant_entry("", uuid="a1", blocks=[{"type": "thinking", "thinking": "t"}]),
        user_entry("问题B", uuid="u2"),
        assistant_entry("回答B", uuid="a2"),
    ]
    pairs = pin.extract_qa_pairs(entries)
    assert len(pairs) == 2
    assert pairs[0]["question"] == "问题A"
    assert pairs[0]["answer"] == ""  # 不得跨界拿到"回答B"
    assert pairs[1]["answer"] == "回答B"


# ---------------------------------------------------------------- spec 7


def test_assistant_text_concat_and_skip_thinking_only(env):
    entries = [
        user_entry("问题", uuid="u1"),
        assistant_entry("", uuid="a0", blocks=[{"type": "thinking", "thinking": "x"}]),
        assistant_entry(
            "",
            uuid="a1",
            blocks=[{"type": "text", "text": "第一段"}, {"type": "tool_use"}, {"type": "text", "text": "第二段"}],
        ),
    ]
    pairs = pin.extract_qa_pairs(entries)
    assert pairs[0]["answer"] == "第一段第二段"
    assert pairs[0]["uuid"] == "a1"


# ---------------------------------------------------------------- spec 8


def test_malformed_lines_skipped(env, tmp_path):
    f = write_transcript(tmp_path, [user_entry("Q", uuid="u1"), assistant_entry("A", uuid="a1")])
    with f.open("a") as fh:
        fh.write("\n{not json}\n{broken")
    pairs = pin.extract_qa_pairs(list(pin.iter_entries(f)))
    assert len(pairs) == 1


def test_bad_format_raises(env, tmp_path):
    f = tmp_path / "bad.jsonl"
    f.parent.mkdir(exist_ok=True)
    f.write_text("\n".join('{"foo": 1}' for _ in range(pin.BAD_FORMAT_CONSECUTIVE + 1)))
    with pytest.raises(pin.BadFormatError):
        list(pin.iter_entries(f))


# ---------------------------------------------------------------- spec 9


def test_append_new_file_with_uuid_marker(env):
    with pin.PinStore() as store:
        ok, no = store.append("uuid-1", "sess1234", "问题摘要", "完整回答", "2026-11-20T10:00:00")
    assert ok and no == 1
    text = pin.PIN_FILE.read_text(encoding="utf-8")
    assert text.splitlines()[0] == pin.FORMAT_HEADER
    assert "<!-- pin-uuid: uuid-1 -->" in text
    assert "**Q:** 问题摘要" in text


def test_uuid_dedup(env):
    with pin.PinStore() as store:
        store.append("uuid-1", "s", "Q1", "A1", "2026-11-20T10:00:00")
        ok, no = store.append("uuid-1", "s", "Q1", "A1", "2026-11-20T10:00:00")
    assert not ok and no == 1
    entries, _ = pin.read_store()
    assert len(entries) == 1


# ---------------------------------------------------------------- spec 10


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl 为 POSIX 专有，Windows 降级无锁")
def test_flock_blocks_second_writer(env):
    with pin.PinStore() as store:
        store.append("u1", "s", "Q", "A", "2026-11-20T10:00:00")
        # 子进程通过 PIN_MESSAGE_FILE 指向同一文件 → 拿不到非阻塞锁 → fail() 输出 error JSON 并退出
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); import pin; store=pin.PinStore(); store.__enter__()" % str(Path(pin.__file__).parent)],
            capture_output=True, text=True, timeout=30,
            env={"PATH": "/usr/bin:/bin", "HOME": str(env["tmp"]),
                 "PIN_MESSAGE_FILE": str(pin.PIN_FILE)},
        )
        payload = json.loads(out.stdout.strip().splitlines()[-1])
        assert payload["code"] == "lock_timeout"
    entries, _ = pin.read_store()
    assert len(entries) == 1  # 并发写入没有破坏数据


# ---------------------------------------------------------------- spec 11


def test_corrupt_file_backed_up_and_rebuilt(env):
    pin.PIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    pin.PIN_FILE.write_text("手工改乱的文件，没有格式头", encoding="utf-8")
    entries, backup = pin.read_store()
    assert entries == []
    assert backup and Path(backup).exists()
    with pin.PinStore() as store:
        store.append("u1", "s", "Q", "A", "2026-11-20T10:00:00")
    assert pin.FORMAT_HEADER in pin.PIN_FILE.read_text(encoding="utf-8")


def test_second_corruption_keeps_first_backup(env):
    pin.PIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    pin.PIN_FILE.write_text("坏文件一", encoding="utf-8")
    _, backup1 = pin.read_store()
    time.sleep(1.1)
    pin.PIN_FILE.write_text("坏文件二", encoding="utf-8")
    _, backup2 = pin.read_store()
    assert backup1 != backup2
    assert Path(backup1).exists() and Path(backup2).exists()


# ---------------------------------------------------------------- spec 12


def test_atomic_write_preserves_old_on_failure(env, monkeypatch):
    with pin.PinStore() as store:
        store.append("u1", "s", "Q", "A", "2026-11-20T10:00:00")
    original = pin.PIN_FILE.read_text(encoding="utf-8")
    monkeypatch.setattr(pin.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        pin.atomic_write(pin.PIN_FILE, "新内容")
    assert pin.PIN_FILE.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------- spec 13


def test_list_items_truncated_at_15():
    """v2 编号列表协议：单次截断上限 MAX_ITEMS=15（分页协议已删）。"""
    items = [{"display": f"问题{i}", "uuid": f"u{i}", "answer": "A"} for i in range(20)]
    out = pin.list_items(items)
    assert out["type"] == "search_results"
    assert out["total"] == 20 and out["truncated"] is True
    assert len(out["items"]) == pin.MAX_ITEMS == 15
    assert [it["index"] for it in out["items"]] == list(range(1, 16))
    out2 = pin.list_items(items[:15])
    assert out2["truncated"] is False


def test_stale_transcript_warn(env, tmp_path, monkeypatch):
    monkeypatch.setattr(pin, "STALE_SECONDS", 0)  # 所有文件都算"旧"
    write_transcript(tmp_path, [user_entry("Q", uuid="u1")])
    path, warn = pin.locate_transcript(env["cwd"])
    assert warn and warn["code"] == "stale_transcript"


def test_no_transcript_dir(env):
    _, warn = pin.locate_transcript(env["cwd"])
    assert warn["code"] == "no_transcript_dir"


# ---------------------------------------------------------------- spec 14


def test_query_matching_case_insensitive_question_only():
    items = [
        {"display": "How to Pin 消息", "answer": "pin 的用法"},
        {"display": " unrelated", "answer": "包含 Pin 词的回答"},
    ]
    assert len(pin.filter_query(items, "pin")) == 1  # 只匹配 display
    assert len(pin.filter_query(items, "PIN")) == 1  # 大小写不敏感
    assert len(pin.filter_query(items, None)) == 2


# ---------------------------------------------------------------- spec 15


def test_cli_output_schemas(env, capsys):
    write_transcript(env["tmp"], [
        user_entry("问题一", uuid="u1"),
        assistant_entry("回答一", uuid="a1"),
    ])

    pin.main(["--cwd", env["cwd"], "last"])
    assert json.loads(capsys.readouterr().out)["type"] == "qa_pair"

    pin.main(["--cwd", env["cwd"], "write-last"])
    assert json.loads(capsys.readouterr().out)["type"] == "write_result"

    pin.main(["--cwd", env["cwd"], "search"])
    assert json.loads(capsys.readouterr().out)["type"] == "search_results"

    pin.main(["--cwd", env["cwd"], "pins"])
    assert json.loads(capsys.readouterr().out)["type"] == "search_results"

    pin.main(["--cwd", env["cwd"], "show", "--pin-no", "1"])
    assert json.loads(capsys.readouterr().out)["type"] == "pin_content"

    pin.main(["--cwd", env["cwd"], "delete", "--pin-no", "1"])
    assert json.loads(capsys.readouterr().out)["type"] == "delete_result"

    pin.main(["--cwd", "/nonexistent/cwd", "last"])
    assert json.loads(capsys.readouterr().out)["type"] == "error"


# ---------------------------------------------------------------- spec 16


def test_delete_renumbers(env):
    with pin.PinStore() as store:
        for i in range(3):
            store.append(f"u{i}", "s", f"Q{i}", f"A{i}", "2026-11-20T10:00:00")
        removed = store.delete(2)
    assert removed["question"] == "Q1"
    entries, _ = pin.read_store()
    assert [e["pin_no"] for e in entries] == [1, 2]  # 重新编号
    text = pin.PIN_FILE.read_text(encoding="utf-8")
    assert "## [PIN #2]" in text and "Q2" in text


# ---------------------------------------------------------------- 端到端冒烟


def test_end_to_end_roundtrip(env, capsys):
    write_transcript(env["tmp"], [
        user_entry("端到端问题", uuid="u1"),
        assistant_entry("端到端回答", uuid="a1"),
    ])
    pin.main(["--cwd", env["cwd"], "write-last"])
    assert json.loads(capsys.readouterr().out)["ok"] is True
    pin.main(["--cwd", env["cwd"], "pins", "端到端"])
    out = json.loads(capsys.readouterr().out)
    assert out["items"][0]["display"] == "端到端问题"
    pin.main(["--cwd", env["cwd"], "show", "--pin-no", "1"])
    assert json.loads(capsys.readouterr().out)["answer"] == "端到端回答"


# ================================================================ v2 新增 specs
# 对应测试计划：~/.gstack/projects/pin-message/panjianwei-main-eng-review-test-plan-20261121-090000.md


# ---- [GAP→新增1] codex 基本提取：response_item/message/user/input_text → QA 对


def test_codex_basic_extraction(env):
    write_rollout(codex_adapter.CODEX_SESSIONS_DIR, [
        cx_message("user", "codex 问题一", "m1"),
        cx_message("assistant", "codex 回答一", "m2"),
    ])
    # cwd 不存在 → CC 探测失败 → auto 落到 codex
    pairs, path, source, payload = pin.load_pairs("/nonexistent/cwd", "auto")
    assert source == "codex"
    assert payload is None
    assert len(pairs) == 1
    assert pairs[0]["question"] == "codex 问题一"
    assert pairs[0]["answer"] == "codex 回答一"
    assert pairs[0]["uuid"] == "m2"


# ---- [GAP→新增2] 排除 function_call_output、local_shell_call_output


def test_codex_excludes_tool_outputs(env):
    write_rollout(codex_adapter.CODEX_SESSIONS_DIR, [
        cx_message("user", "问题", "m1"),
        cx_skip("function_call_output"),
        cx_skip("local_shell_call_output"),
        cx_skip("function_call"),
        cx_skip("local_shell_call"),
        cx_message("assistant", "回答", "m2"),
    ])
    pairs, _, source, _ = pin.load_pairs("/nonexistent/cwd", "auto")
    assert source == "codex"
    assert len(pairs) == 1
    assert pairs[0]["answer"] == "回答"


# ---- [GAP→新增3] 排除 turn_context、reasoning 条目（已知噪声不计 fail-loud）


def test_codex_excludes_turn_context_and_reasoning(env):
    write_rollout(codex_adapter.CODEX_SESSIONS_DIR, [
        cx_top_skip("turn_context"),
        cx_skip("reasoning"),
        cx_message("user", "问题", "m1"),
        cx_message("assistant", "回答", "m2"),
        cx_top_skip("token_count"),
    ])
    pairs, _, source, payload = pin.load_pairs("/nonexistent/cwd", "auto")
    assert source == "codex"
    assert payload is None  # 无 codex_parse 错误
    assert [p["question"] for p in pairs] == ["问题"]


# ---- [GAP→新增4] 配对边界：下一 user message 出现即停止


def test_codex_pairing_boundary(env):
    write_rollout(codex_adapter.CODEX_SESSIONS_DIR, [
        cx_message("user", "问题A", "m1"),
        cx_message("assistant", "回答A", "m2"),
        cx_message("user", "问题B", "m3"),
        cx_message("assistant", "回答B", "m4"),
    ])
    pairs, _, _, _ = pin.load_pairs("/nonexistent/cwd", "auto")
    assert [p["question"] for p in pairs] == ["问题A", "问题B"]
    assert pairs[0]["answer"] == "回答A"
    # 无回答的提问不得跨界配对
    write_rollout(codex_adapter.CODEX_SESSIONS_DIR, [
        cx_message("user", "问题X", "x1"),
        cx_message("user", "问题Y", "x2"),
        cx_message("assistant", "回答Y", "x3"),
    ], name="rollout-20261121-100000-ffffffff.jsonl")
    pairs2, _, _, _ = pin.load_pairs("/nonexistent/cwd", "auto")
    assert pairs2[0]["question"] == "问题X" and pairs2[0]["has_answer"] is False
    assert pairs2[1]["answer"] == "回答Y"


# ---- [GAP→新增5] 未知行 schema → codex_parse fail-loud（含行样本，不静默跳过）


def test_codex_unknown_schema_fail_loud(env):
    write_rollout(codex_adapter.CODEX_SESSIONS_DIR, [
        {"type": "totally_new_format", "data": {"nested": True}}
    ] * (pin.BAD_FORMAT_CONSECUTIVE + 1))
    pairs, path, source, payload = pin.load_pairs("/nonexistent/cwd", "auto")
    assert pairs is None
    assert payload["type"] == "error" and payload["code"] == "codex_parse"
    assert "totally_new_format" in payload["message"]  # 行样本随错误上报


def test_codex_malformed_lines_skipped(env):
    f = write_rollout(codex_adapter.CODEX_SESSIONS_DIR, [
        cx_message("user", "Q", "m1"),
        cx_message("assistant", "A", "m2"),
    ])
    with f.open("a") as fh:
        fh.write("\n{not json}\n{broken")
    pairs, _, source, _ = pin.load_pairs("/nonexistent/cwd", "auto")
    assert source == "codex" and len(pairs) == 1


# ---- [GAP→新增6] 平台自动探测：cc-only / codex-only / both（mtime 新者胜 + warn）


def test_platform_cc_only(env):
    write_transcript(env["tmp"], [
        user_entry("CC 问题", uuid="u1"),
        assistant_entry("CC 回答", uuid="a1"),
    ])
    pairs, path, source, payload = pin.load_pairs(env["cwd"], "auto")
    assert source == "cc"
    assert pairs and pairs[0]["question"] == "CC 问题"


def test_platform_codex_only(env):
    write_rollout(codex_adapter.CODEX_SESSIONS_DIR, [
        cx_message("user", "codex 问题", "m1"),
        cx_message("assistant", "codex 回答", "m2"),
    ])
    pairs, path, source, payload = pin.load_pairs("/nonexistent/cwd", "auto")
    assert source == "codex"
    assert pairs and pairs[0]["question"] == "codex 问题"


def test_platform_both_picks_newer_and_warns(env):
    cc_file = write_transcript(env["tmp"], [
        user_entry("CC 问题", uuid="u1"),
        assistant_entry("CC 回答", uuid="a1"),
    ])
    cx_file = write_rollout(codex_adapter.CODEX_SESSIONS_DIR, [
        cx_message("user", "codex 问题", "m1"),
        cx_message("assistant", "codex 回答", "m2"),
    ])
    now = time.time()
    # CC 更新 → 选 cc
    os.utime(cc_file, (now + 100, now + 100))
    os.utime(cx_file, (now, now))
    path, source, payload = pin.resolve_transcript(env["cwd"], "auto")
    assert source == "cc" and payload["code"] == "platform_ambiguous"
    # Codex 更新 → 选 codex
    os.utime(cc_file, (now, now))
    os.utime(cx_file, (now + 100, now + 100))
    path, source, payload = pin.resolve_transcript(env["cwd"], "auto")
    assert source == "codex" and payload["code"] == "platform_ambiguous"


def test_platform_both_warn_goes_to_stderr(env, capsys):
    write_transcript(env["tmp"], [
        user_entry("CC 问题", uuid="u1"),
        assistant_entry("CC 回答", uuid="a1"),
    ])
    write_rollout(codex_adapter.CODEX_SESSIONS_DIR, [
        cx_message("user", "codex 问题", "m1"),
        cx_message("assistant", "codex 回答", "m2"),
    ])
    pin.main(["--cwd", env["cwd"], "last"])
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["type"] == "qa_pair"
    assert "platform_ambiguous" in captured.err


# ---- [GAP→新增7] --platform cc|codex 强制覆盖


def test_platform_force_override(env, capsys):
    write_transcript(env["tmp"], [
        user_entry("CC 问题", uuid="u1"),
        assistant_entry("CC 回答", uuid="a1"),
    ])
    write_rollout(codex_adapter.CODEX_SESSIONS_DIR, [
        cx_message("user", "codex 问题", "m1"),
        cx_message("assistant", "codex 回答", "m2"),
    ])
    # 强制 codex：即使 CC 记录更新也不用
    pin.main(["--cwd", env["cwd"], "--platform", "codex", "last"])
    assert json.loads(capsys.readouterr().out)["question"] == "codex 问题"
    # 强制 cc
    pin.main(["--cwd", env["cwd"], "--platform", "cc", "last"])
    assert json.loads(capsys.readouterr().out)["question"] == "CC 问题"


def test_platform_neither_error(env):
    _, _, _, payload = pin.load_pairs("/nonexistent/cwd", "auto")
    assert payload["type"] == "error" and payload["code"] == "no_transcript_dir"


# ---- [GAP→新增9] show/delete 纯数字参数 = pin_no 直达，跳过列表


def test_numeric_direct_show_and_delete(env, capsys):
    with pin.PinStore() as store:
        store.append("u1", "s", "Q1", "A1", "2026-11-21T09:00:00")
        store.append("u2", "s", "Q2", "A2", "2026-11-21T09:05:00")
    pin.main(["show", "2"])  # 位置参数纯数字 → 直达
    out = json.loads(capsys.readouterr().out)
    assert out["type"] == "pin_content" and out["question"] == "Q2"
    pin.main(["delete", "1"])
    assert json.loads(capsys.readouterr().out)["type"] == "delete_result"
    entries, _ = pin.read_store()
    assert [e["pin_no"] for e in entries] == [1]  # 重新编号
    assert entries[0]["question"] == "Q2"


# ---- [GAP→新增10] pin 文件 0 条 → no_pins 错误码


def test_no_pins_error(env, capsys):
    for cmd in (["pins"], ["show"], ["delete"]):
        with pytest.raises(SystemExit):  # fail() 以 sys.exit(0) 结束
            pin.main(cmd)
        assert json.loads(capsys.readouterr().out)["code"] == "no_pins"
    # search 语义不同：空 session 是 empty_session，不属于 no_pins


# ---- [GAP→新增11] install.sh 幂等：重复跑无副作用；Codex 分支创建目录 + 空 pin 文件


def test_install_sh_idempotent(tmp_path):
    repo = Path(__file__).resolve().parent.parent
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PIN_MESSAGE_FILE": str(tmp_path / "custom" / "pin.md"),
    }

    def run_install():
        return subprocess.run(
            ["bash", str(repo / "install.sh")],
            capture_output=True, text=True, env=env, cwd=str(repo),
        )

    r1 = run_install()
    assert r1.returncode == 0, r1.stderr
    # CC 分支：4 命令 symlink + 引擎 symlink
    for cmd in ("pin", "pin_search", "pin_show", "pin_delete"):
        link = tmp_path / ".claude" / "commands" / f"{cmd}.md"
        assert link.is_symlink() and "pin" in link.read_text(encoding="utf-8")
    assert (tmp_path / ".claude" / "plugins" / "pin_message" / "pin.py").is_symlink()
    assert (tmp_path / ".claude" / "plugins" / "pin_message" / "codex_adapter.py").is_symlink()
    # Codex 分支：prompts（4）+ skills（4）+ pin 文件（PIN_MESSAGE_FILE 覆盖路径）
    for cmd in ("pin", "pin_search", "pin_show", "pin_delete"):
        assert (tmp_path / ".codex" / "prompts" / f"{cmd}.md").exists()
        assert (tmp_path / ".agents" / "skills" / cmd / "SKILL.md").exists()
    pin_file = tmp_path / "custom" / "pin.md"
    assert pin_file.read_text(encoding="utf-8").splitlines()[0] == pin.FORMAT_HEADER
    # 幂等：第二次运行不重建/覆盖 pin 文件，且报告"跳过"
    before = pin_file.read_text(encoding="utf-8")
    r2 = run_install()
    assert r2.returncode == 0, r2.stderr
    assert pin_file.read_text(encoding="utf-8") == before
    assert "已存在，跳过" in r2.stdout


# ---- [BUGFIX] CC slug 编码：`_` 等非字母数字字符也要转成 `-`（旧实现只换 `/`）


def test_slug_encoding_matches_cc():
    # CC 实测：/Users/pan/pythonProjects/face_recon → -Users-pan-pythonProjects-face-recon
    assert pin.slug_for("/Users/pan/pythonProjects/face_recon") == "-Users-pan-pythonProjects-face-recon"
    assert pin.slug_for("/Users/pan/audio_e2e_test") == "-Users-pan-audio-e2e-test"
    # 大小写保留
    assert pin.slug_for("/Users/pan/Test_Case_Collection_Platform") == "-Users-pan-Test-Case-Collection-Platform"
    # 无下划线的常规路径不受影响
    assert pin.slug_for("/Users/pan/work") == "-Users-pan-work"


def test_locate_transcript_underscore_dir(env, tmp_path):
    # 项目目录含下划线：transcript 落在 `-` 编码的 slug 目录，须能被定位
    cwd = "/Users/pan/pythonProjects/face_recon"
    slug_dir = tmp_path / "projects" / "-Users-pan-pythonProjects-face-recon"
    slug_dir.mkdir(parents=True)
    (slug_dir / "sess-12345678.jsonl").write_text(
        "\n".join(json.dumps(e) for e in [user_entry("Q", uuid="u1"), assistant_entry("A", uuid="a1")]),
        encoding="utf-8",
    )
    pairs, path, source, payload = pin.load_pairs(cwd, "auto")
    assert source == "cc"
    assert not payload or payload["type"] == "warn"  # 定位成功；env fixture 可能附 stale warn
    assert pairs and pairs[0]["question"] == "Q"

