# pin-message

Pin important Q&A turns from your Claude Code session and recall them anytime — like bookmarks for your conversation history.

When a conversation produces a result you'll need later (a debug insight, a config snippet, a decision rationale), pin it. Next session — or three weeks later — run `/pin_show` and it's one pick away.

## Commands

| Command | What it does |
|---------|--------------|
| `/pin` | Pin the **last** Q&A turn of the current session to `~/.claude/pin_message.md` (creates the file if absent). |
| `/pin_search [query]` | Searchable picker of all real questions you asked in this session. Pick one → pinned. Long pasted content is compressed for display (e.g. `[Pasted text #1 +18 lines]`). |
| `/pin_show [query]` | Searchable picker of everything you've pinned. Pick one → full Q&A displayed. |
| `/pin_delete [query]` | Searchable picker + confirmation, then deletes the entry and renumbers the rest. |

## Dual-path runtime

Every command works in two modes, auto-detected on each invocation:

- **Engine path** (macOS/Linux with `python3`): a deterministic Python engine (`scripts/pin.py`) does all parsing, filtering, locking, and file I/O. The model only renders the picker. This gives exact, test-covered behavior — including the six filtering rules that separate real questions from tool results, slash-command artifacts, sidechain chatter, and interruption markers in Claude Code transcripts.
- **Manual path** (no python3 — e.g. native Windows installs): the command prompt embeds the full parsing rulebook and does everything with Read/Write/Edit. Fully functional, works anywhere Claude Code runs.

## Storage format

`~/.claude/pin_message.md`:

```markdown
<!-- pin-message-format: 1 -->

# Pinned Messages

## [PIN #1] 2026-11-20 22:30 | session a19d9146
<!-- pin-uuid: 1c7e3e59-68ec-4972-bd6e-aff5017521b9 -->
**Q:** 现在API应该恢复了你再试试
**A:**
好，继续。写剩余 3 个命令。
```

- The format header is validated on every read; a corrupted file is backed up (timestamped `.bak`) and rebuilt rather than silently mis-parsed.
- Writes are atomic (tmp + rename) and guarded by `flock` against concurrent sessions.
- Duplicate pins are detected via the assistant entry's `uuid` — pinning the same turn twice is a no-op that tells you the existing PIN number.

## Install

From a Claude Code marketplace that lists this plugin:

```
/plugin install pin-message@<marketplace-name>
```

Or add this repo directly as a marketplace and install from it.

## Development

```bash
python3 -m pytest tests/ -q   # 24 tests: parsing rules, compression, store, lock, CLI schemas
```

## License

MIT
