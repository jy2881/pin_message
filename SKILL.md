---
name: pin-message
description: Pin important Q&A turns from a Claude Code or Codex CLI session to a local markdown file and recall them anytime. Use it when a conversation produces a result worth keeping — a debug insight, a config snippet, or a decision rationale — that the user will need again later.
version: 0.2.0
summary: Bookmark conversation history — save, search, show, and delete pinned Q&A turns in Claude Code and Codex CLI.
tags: [pin, bookmark, history, memory, conversation]
keywords: [pin, bookmark, history, memory, claude-code, codex]
license: MIT
compatibility: Claude Code 2.x and Codex CLI. Python 3 optional — every operation has a manual no-python fallback.
docs_url: https://github.com/jy2881/pin_message
---

# pin-message

Pin important Q&A turns from your AI CLI session and recall them anytime — like bookmarks for your conversation history. Works in **Claude Code** and **Codex CLI**.

## When to use

Use this skill when a conversation produces something the user will need again later: a hard-won debugging insight, a working config snippet, a decision rationale, or a follow-up plan. Pinning it keeps it one tap away instead of buried in the transcript.

## Operations

Four operations. In Claude Code they map to slash commands; in Codex CLI to prompts.

| Operation | Claude Code | What it does |
|-----------|-------------|--------------|
| Save last turn | `/pin` | Pin the most recent user question + agent answer to the pin file. |
| Search & pin | `/pin_search [query]` | List the session's real questions (newest first), pick one, pin it. |
| Show | `/pin_show [n\|query]` | Show a pinned item by number, or pick from a list. |
| Delete | `/pin_delete [n\|query]` | Delete a pinned item after y/n confirmation, then renumber. |

A plain number (`/pin_show 3`) skips the list and jumps straight to that PIN. The list pickers use a native single-select box (max 4 shown; add a query word to narrow).

## Storage

Pinned items live in one markdown file — `~/.claude/pin_message.md` by default, override with `PIN_MESSAGE_FILE`:

```markdown
<!-- pin-message-format: 1 -->

# Pinned Messages

## [PIN #1] 2026-11-20 22:30 | session a19d9146
<!-- pin-uuid: 1c7e3e59-68ec-4972-bd6e-aff5017521b9 -->
**Q:** <question summary>
**A:**
<full answer>
```

- One file shared across both platforms; the format header is validated on every read.
- Writes are atomic and guarded by `flock` against concurrent sessions.
- Duplicate pins are detected by `uuid`.

## Implementation

The engine is `scripts/pin.py`, a deterministic parser for both transcript formats (Claude Code `~/.claude/projects/<slug>/*.jsonl` and Codex `~/.codex/sessions/**/rollout-*.jsonl`). If Python 3 is unavailable, every operation still works via a manual path: the parsing rulebook is embedded in the command prompts, which locate the transcript and read/write the pin file with plain Read/Write/Edit.

## Privacy

Fully local — pinned content is written only to the user's machine and never transmitted anywhere.
