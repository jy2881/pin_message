---
description: 把上一轮对话（用户提问 + agent 回答）pin 到 ~/.claude/pin_message.md
argument-hint: ""
allowed-tools: Bash, Read, Write, Edit, Glob
---

# /pin — Pin 上一轮对话

将当前 session 中**最近一个真实用户提问 + agent 回答**写入 `~/.claude/pin_message.md`（文件不存在则创建，已有内容则追加，按 uuid 去重）。

## 步骤 1：检测引擎

执行：
```bash
ENGINE=$(find "$HOME/workspace/pin_message/scripts" "$HOME/.claude/plugins" -name pin.py 2>/dev/null | head -1)
command -v python3 >/dev/null && [ -n "$ENGINE" ] && echo "ENGINE=$ENGINE" || echo "NO_ENGINE"
```

输出以 `ENGINE=` 开头 → 走【引擎路径】；否则 → 走【手动路径】。

## 引擎路径

```bash
python3 "$ENGINE" --cwd "$PWD" write-last
```

按输出 JSON 处理：
- `write_result` → 告知用户"已保存为 PIN #N"
- `error`（code=duplicate）→ 告知"该轮已在 PIN #N 中"
- 其他 `error`/`warn` → 原样转达用户

## 手动路径（无 python3，全平台可用）

### 1. 定位并解析 transcript

按【文末规则书】定位当前 session 的 transcript，提取全部"真实用户提问"及其配对回答。取**最后一对**。

若没有任何合格提问 → 告知"当前 session 还没有可 pin 的对话"并停止。

### 2. 写入 pin 文件

先 Read `~/.claude/pin_message.md`：
- 不存在 → 用 Write 创建，全文：
```markdown
<!-- pin-message-format: 1 -->

# Pinned Messages

## [PIN #1] <当前本地时间 YYYY-MM-DD HH:MM> | session <session 标识，见规则书>
<!-- pin-uuid: <该回答条目的 uuid> -->
**Q:** <display 摘要，见规则书>
**A:**
<完整回答原文>
```
- 已存在 → 检查各条目的 `<!-- pin-uuid: -->` 标记，若与本次 uuid 相同（且均非空）→ 告知"该轮已在 PIN #N 中"并停止；否则用 Edit 在文件末尾追加同格式新条目（N = 最大编号 + 1）。

完成后告知用户"已保存为 PIN #N"。

---

## 规则书 A：Claude Code transcript 解析（已针对 2.x 实测验证）

**定位**：slug = 当前工作目录绝对路径中每段**非字母数字**字符（`/`、`_` 等）替换为单个 `-`，大小写保留，如 `/Users/pan/face_recon` → `-Users-pan-face-recon`（⚠️ `_` 也会变成 `-`，不只是 `/`）；transcript 目录为 `~/.claude/projects/<slug>/`，取其中修改时间最新的 `.jsonl`。若最新文件修改时间早于 5 分钟，先向用户确认再继续（可能在后台会话中运行）。session 标识 = 文件名前 8 字符。

**解析**：文件每行一个 JSON 对象；解析失败的行直接忽略。条目文本提取：`message.content` 为字符串则用之；为数组则拼接所有 `{"type":"text"}` 块的 `text` 字段。

**真实用户提问判定**（一条 `type:user` 条目须同时满足以下 6 条）：
1. `isSidechain` 为假或缺失
2. `isMeta` 为空/假/缺失（排除 skill dump 等元消息）
3. **不含 `tool_result` 块**：条目无 `toolUseID` 字段，且 `message.content` 数组中无 `{"type":"tool_result"}` 块。⚠️ 这类条目 `userType=external`、`isSidechain=false`，元数据与真实提问无法区分，必须检查块类型
4. 提取文本非空
5. 文本不含 `<command-name>` —— 斜杠命令在 transcript 中的真实格式是 `<command-message>pin</command-message>\n<command-name>/pin</command-name>\n<command-args>...`，不是以 `/pin` 开头
6. 文本不以 `[Request interrupted` 开头（系统注入的打断标记，常是 session 最后一条 user 消息，极易误当提问）

**配对回答**：该提问之后、**下一个合格提问出现之前**，最近一个 `type:assistant` 且 content 含至少一个 text 块的条目，拼接其全部 text 块；跳过仅含 thinking/tool_use 的条目（防止用户打断后答案跨界配对）。

**display 摘要**：提问中含 `[Pasted text ...]` 占位符行则原样保留；单条文本超过 500 字符则压缩为 `首行(≤80字符) [Pasted text +N lines]`（N = 总行数）；否则取首行截 80 字符。pasted 占位符示例：`[Pasted text #1 +18 lines]`。

**uuid**：取配对 assistant 条目的 `uuid` 字段值（顶层字段）。

## 规则书 B：Codex rollout 解析

**定位**：`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`，取修改时间最新者（Codex rollout 不按 cwd 分组，"当前 session" = 全局最新文件）。session 标识 = 文件名最后一段（uuid 段）前 8 字符。若 CC 与 Codex 记录同时存在，取 mtime 较新一侧并告知用户你选了哪侧。

**条目提取**：
- `type: "response_item"` 且 `payload.type: "message"`：`role: "user"` 的文本 = `payload.content` 中所有 `{"type":"input_text"}` 块的 `text` 拼接；`role: "assistant"` 同理用 `{"type":"output_text"}` 块
- 顶层 `type: "user_message"`：`message` 字段即用户文本

**排除**（须全部剔除，不得当提问也不得当回答）：
- `payload.type` 为 `function_call` / `function_call_output` / `local_shell_call` / `local_shell_call_output` / `reasoning` / `web_search_call` / `mcp_call` / `mcp_call_output`
- 顶层 `type` 为 `turn_context` / `token_count` / `session_meta`

**fail-loud**：遇到无法识别的行类型 → 停止并告知"无法识别的 Codex 会话格式（codex_parse），请到 github.com/jy2881/pin_message 提 issue"，**不要静默跳过或自行猜测格式**。

**配对回答**：同规则书 A 的边界规则（遇下一合格 user message 即停止）。

**uuid**：取配对条目的 `payload.id`（缺失则留空；留空时跳过 uuid 去重，直接追加）。

## pin 文件格式（双平台共用）

路径 `~/.claude/pin_message.md`（可用环境变量 `PIN_MESSAGE_FILE` 覆盖），首行必须是格式头 `<!-- pin-message-format: 1 -->`。追加条目格式：
```markdown
## [PIN #N] <本地时间 YYYY-MM-DD HH:MM> | session <session 标识>
<!-- pin-uuid: <uuid> -->
**Q:** <display 摘要>
**A:**
<完整回答原文>
```
写入前先检查现有条目的 uuid 是否重复（重复则告知已有编号，不追加）。用 Write/Edit 工具写文件。
