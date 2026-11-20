"""pin.py 引擎测试。

fixture 构造说明：
- 大部分条目用合成 dict（isSidechain 等极端 case 本地无真实数据）
- spec 2 的"真实形态"fixture 从本机 transcript 结构仿制（Claude Code 2.1.63）
- 运行前请将真实 transcript 片段保存到 tests/fixtures/real_session.jsonl（可选，
  缺省时该 spec 自动 fallback 到仿制 fixture，不 fail）
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import pin  # noqa: E402


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
    """把 PIN_FILE / PROJECTS_DIR 重定向到 tmp，cwd 固定为 /tmp/proj。"""
    monkeypatch.setattr(pin, "PIN_FILE", tmp_path / "pin_message.md")
    monkeypatch.setattr(pin, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(pin, "STALE_SECONDS", -1)  # 测试里文件总是"新鲜"
    return {"cwd": "/tmp/proj", "tmp": tmp_path}


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


def test_pagination_and_page_cap():
    items = [{"display": f"问题{i}", "uuid": f"u{i}"} for i in range(20)]
    page1 = pin.page_items(items, 1)
    assert page1["type"] == "search_results"
    assert len(page1["items"]) == 3 and page1["has_more"]
    page5 = pin.page_items(items, 5)
    assert len(page5["items"]) == 3 and not page5["has_more"]
    over = pin.page_items(items, 6)
    assert over["type"] == "error" and "已展示全部" in over["message"]


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
