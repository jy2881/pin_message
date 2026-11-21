# Setup

## Prerequisites

- **Claude Code** (recommended, for the native `/plugin` flow) or **Codex CLI**.
- Optional: `python3` on your `PATH` for the fast deterministic engine. Without it, every command still works via its built-in manual path (the parsing rulebook is embedded in each command prompt).

## Install

### Via the Claude Code marketplace

```
/plugin marketplace add jy2881/pin_message
/plugin install pin-message@pin-message
```

### Via the one-line installer

```bash
git clone https://github.com/jy2881/pin_message.git && cd pin_message && ./install.sh
```

The installer is idempotent and:

1. Symlinks the four commands into `~/.claude/commands/` (`pin`, `pin_search`, `pin_show`, `pin_delete`).
2. Symlinks the engine (`pin.py` + `codex_adapter.py`) into `~/.claude/plugins/pin_message/`.
3. Creates the pin file at `~/.claude/pin_message.md` (or `$PIN_MESSAGE_FILE`) if it does not exist.
4. Generates Codex prompts (`~/.codex/prompts/`) and skills (`~/.agents/skills/`) from the same command sources.

## Configure

- **Custom pin file location**: set `PIN_MESSAGE_FILE=/path/to/file.md`. All four commands read and write this file.
- **Force a platform**: the engine defaults to `--platform auto` (newer of Claude Code / Codex wins). Override with `--platform cc` or `--platform codex` when invoking `scripts/pin.py` directly.

## Verify

```bash
python3 -m pytest tests/ -q          # 47 tests
claude plugin validate .             # manifest check (Claude Code 2.x)
```

## First use

Run `/pin` after any exchange to save it, then `/pin_show` to list and read pinned items, `/pin_search <keyword>` to find a past question and pin it, and `/pin_delete` to remove one.
