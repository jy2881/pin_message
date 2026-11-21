# pin-message

Pin important Q&A turns from your AI CLI session and recall them anytime — like bookmarks for your conversation history. Works in **Claude Code** and **Codex CLI**.

When a conversation produces a result you'll need later (a debug insight, a config snippet, a decision rationale), pin it. Next session — or three weeks later — run `/pin_show` and it's one tap away.

## Commands

| Claude Code | Codex CLI | What it does |
|-------------|-----------|--------------|
| `/pin` | `/prompts:pin` | Pin the **last** Q&A turn of the current session to `~/.claude/pin_message.md` (creates the file if absent). |
| `/pin_search [query]` | `/prompts:pin_search` | Search every real question you asked in this session and pin the one you pick. Long pasted content is compressed for display (e.g. `[Pasted text #1 +18 lines]`). |
| `/pin_show [n\|query]` | `/prompts:pin_show` | With a number: jump straight to that PIN. Otherwise: a picker of pinned items → full Q&A rendered as markdown. |
| `/pin_delete [n\|query]` | `/prompts:pin_delete` | Same picker as `/pin_show`, then a y/n confirmation, then deletes and renumbers. |

## The selection protocol

All three picker commands (`/pin_search`, `/pin_show`, `/pin_delete`) show candidates in a **native single-select box** (the `AskUserQuestion` radio dialog) — no fragile text-numbered lists. Each option shows the question summary plus its timestamp and session id:

- Up to 4 candidates are shown at once.
- More than 4 → the 4 most recent are shown, with a hint to narrow the search with a query word.
- A plain-number argument (`/pin_show 3`) skips the picker entirely.

## Dual-platform engine

`--platform auto` (default) auto-detects where the session lives:

- **Claude Code**: `~/.claude/projects/<slug>/*.jsonl` — six filtering rules separate real questions from tool results, slash-command artifacts, sidechain chatter, and interruption markers.
- **Codex CLI**: `~/.codex/sessions/**/rollout-*.jsonl` (newest by mtime) — `response_item/message` extraction with `input_text`/`output_text`, tool outputs / `reasoning` / `turn_context` excluded. Unknown schema fails loudly (`codex_parse` error with a line sample) instead of silently guessing.

If both exist, the newer one wins with a stderr warning; force with `--platform cc|codex`.

Every command also has a **manual path** (no python3): the full parsing rulebook for both formats is embedded in the command prompts, which do everything with Read/Write/Edit.

## Storage format

`~/.claude/pin_message.md` (override with `PIN_MESSAGE_FILE`):

```markdown
<!-- pin-message-format: 1 -->

# Pinned Messages

## [PIN #1] 2026-11-20 22:30 | session a19d9146
<!-- pin-uuid: 1c7e3e59-68ec-4972-bd6e-aff5017521b9 -->
**Q:** 现在API应该恢复了你再试试
**A:**
好，继续。写剩余 3 个命令。
```

- Same file shared by both platforms; format header validated on every read — a corrupted file is backed up (timestamped `.bak`) and rebuilt.
- Writes are atomic (tmp + rename) and guarded by `flock` against concurrent sessions.
- Duplicate pins detected via `uuid` — pinning the same turn twice tells you the existing PIN number.
- Timestamps are stored and displayed in your local machine time.

## Install

**Option 1 — one-line (both platforms):**

```bash
git clone https://github.com/jy2881/pin_message.git && cd pin_message && ./install.sh
```

Idempotent; symlinks the four commands into `~/.claude/commands`, installs the engine into `~/.claude/plugins/pin_message/`, generates Codex prompts (`~/.codex/prompts/`) and skills (`~/.agents/skills/`) from the same source files, and creates the pin file.

**Option 2 — Claude Code plugin marketplace:**

```
/plugin marketplace add jy2881/pin_message
/plugin install pin-message@pin-message
```

**Option 3 — official Claude directory:** install from the in-app plugin directory after this plugin is accepted there.

## Development

```bash
python3 -m pytest tests/ -q   # 47 tests: CC + Codex parsing, platform detect, selection protocol, store, lock, install.sh
```

## Privacy

This plugin is fully local. Pinned content is written only to `~/.claude/pin_message.md` on your machine (or wherever `PIN_MESSAGE_FILE` points). It makes no network calls, sends no telemetry, and uses no third-party services. Your conversation content never leaves your machine.

## Support

Bug reports and feature requests: https://github.com/jy2881/pin_message/issues

## License

[MIT](./LICENSE)
