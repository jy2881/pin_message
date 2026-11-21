# pin-message

Pin important Q&A turns from your AI CLI session and recall them anytime — like bookmarks for your conversation history. Works in **Claude Code** and **Codex CLI**.

When a conversation produces a result you'll need later (a debug insight, a config snippet, a decision rationale), pin it. Next session — or three weeks later — run `/pin_show` and it's one number away.

## Commands

| Claude Code | Codex CLI | What it does |
|-------------|-----------|--------------|
| `/pin` | `/prompts:pin` | Pin the **last** Q&A turn of the current session to `~/.claude/pin_message.md` (creates the file if absent). |
| `/pin_search [query]` | `/prompts:pin_search` | Numbered list of all real questions you asked in this session. Reply with a number → pinned. Long pasted content is compressed for display (e.g. `[Pasted text #1 +18 lines]`). |
| `/pin_show [n\|query]` | `/prompts:pin_show` | With a number: jump straight to that PIN. Otherwise: numbered list of pinned items, reply with a number → full Q&A displayed. |
| `/pin_delete [n\|query]` | `/prompts:pin_delete` | Same picker as `/pin_show`, then a y/n confirmation, then deletes and renumbers. |

## The numbered-list protocol

Plugins have no access to the native `/resume`-style picker (it's a built-in TUI with no plugin API), so all four commands use the same cross-CLI protocol: the engine prints up to 15 items, each rendered as a two-line entry —

```
1. How do I reset the API key?
   2026-11-21 09:00 · session a19d9146 · Run export API_KEY=... first, then…
```

— and you reply with the number. It works identically in Claude Code and Codex because both are continuous conversations. A plain-number argument (`/pin_show 3`) skips the list entirely.

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

## Install

**Option 1 — one-line (recommended, both platforms):**

```bash
git clone https://github.com/jy2881/pin_message.git && cd pin_message && ./install.sh
```

Idempotent; symlinks the four commands into `~/.claude/commands`, installs the engine into `~/.claude/plugins/pin_message/`, generates Codex prompts (`~/.codex/prompts/`) and skills (`~/.agents/skills/`) from the same source files, and creates the pin file (a Codex-only machine has no `~/.claude` — the installer creates it).

**Option 2 — Claude Code plugin marketplace:**

```
/plugin marketplace add jy2881/pin_message
```

**Option 3 — community/official directory:** submit to `anthropics/claude-plugins-community` (PR) or the official directory via https://claude.ai/directory/manage (requires a paid plan, public repo, human review).

## Development

```bash
python3 -m pytest tests/ -q   # 39 tests: CC + Codex parsing, platform detect, list protocol, store, lock, install.sh
```

## License

MIT
