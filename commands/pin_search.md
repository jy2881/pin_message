---
description: 搜索当前 session 的历史提问，选择框选中后 pin 到 ~/.claude/pin_message.md
argument-hint: "[查询词]"
allowed-tools: Bash, Read, Write, Edit, Glob, AskUserQuestion
---

# /pin_search — 搜索并 Pin 历史提问

在当前 session 中提取全部"真实用户提问"（按【文末规则书】），以**选择框**展示，用户选中一条，将其问答对写入 `~/.claude/pin_message.md`。

参数 `$ARGUMENTS` = 查询词（可空）。查询词为**大小写不敏感子串匹配，只匹配问题摘要不匹配回答正文**。

## 步骤 1：定位引擎并执行（单次 Bash，检测与首调合并）

执行：
```bash
ENGINE=$(find "$HOME/workspace/pin_message/scripts" "$HOME/.claude/plugins" -name pin.py 2>/dev/null | head -1)
if command -v python3 >/dev/null && [ -n "$ENGINE" ]; then
  python3 "$ENGINE" --cwd "$PWD" search $ARGUMENTS
else
  echo "NO_ENGINE"
fi
```

输出以 `{` 开头（引擎单行 JSON）→ 走【引擎路径】按输出处理；输出为 `NO_ENGINE` → 走【手动路径】。

按输出 JSON 处理：
- `search_results` → 按【选择协议】用 AskUserQuestion 呈现候选
- `error`（code=no_match）→ 告知"没有匹配「$ARGUMENTS」的提问"，建议换查询词或不带查询词重试
- `warn` → 原样转达用户后继续

用户选中后：取该 item 的 `uuid`，运行 `python3 "$(find "$HOME/workspace/pin_message/scripts" "$HOME/.claude/plugins" -name pin.py 2>/dev/null | head -1)" --cwd "$PWD" write --uuid <uuid>`，按输出处理（`write_result` → 告知"已保存为 PIN #N"；`duplicate` → 告知"该轮已在 PIN #N 中"；其他 error → 原样转达）。

## 手动路径（无 python3，全平台可用）

### 1. 提取提问列表

按【文末规则书】定位并解析当前 session transcript，提取全部真实用户提问（含配对回答和 uuid），**按时间倒序取最近 15 条**。

没有任何合格提问 → 告知"当前 session 还没有可 pin 的对话"并停止。

### 2. 过滤

若 $ARGUMENTS 非空：仅保留 display 摘要（规则书 display 摘要节）中包含该查询词（大小写不敏感）的条目。过滤后为空 → 告知"没有匹配「$ARGUMENTS」的提问"并停止。

### 3. 选择

按【选择协议】用 AskUserQuestion 呈现候选。

### 4. 写入

选中后按 /pin 手动路径第 2 步写入 `~/.claude/pin_message.md`（uuid 去重、格式见规则书）。

---

## 选择协议（/pin_search / /pin_show / /pin_delete 通用）

用 `AskUserQuestion` 工具呈现候选条目（单选 radio 选择框，**不再用文字编号列表**）。

每条候选 item → 一个 option：
- `label`：display 摘要（过长截断到约 40 字符）
- `description`：`session <session> · <YYYY-MM-DD HH:MM>`（/pin_search 无 pin_no；`<YYYY-MM-DD HH:MM>` 即引擎 `ts` 字段，已转本机时间；ts 为空则省略该段及其前导 ` · `）

映射规则：
- `multiSelect` 固定 `false`；单个 question，header 用「选择 pin」
- 候选 ≤ 4 条 → 一次全部呈现
- 候选 > 4 条 → 只取最近 4 条，question 文本末尾注明「共 N 条，仅展示最近 4 条，可用查询词缩小范围」
- 构建 option 时按顺序记住 label ↔ item 的对应关系；用户选中后用返回的 label 反查 item，取该 item 的 `pin_no`（/pin_show、/pin_delete）或 `uuid`（/pin_search）继续后续步骤
- 用户选「Other」：输入为纯数字且对应条目存在 → 视为选中该条；否则视为放弃，停止并说明

## 规则书 A：Claude Code transcript 解析（已针对 2.x 实测验证）

**定位**：slug = 当前工作目录绝对路径中每段**非字母数字**字符（`/`、`_` 等）替换为单个 `-`，大小写保留，如 `/Users/pan/face_recon` → `-Users-pan-face-recon`（⚠️ `_` 也会变成 `-`，不只是 `/`）；transcript 目录为 `~/.claude/projects/<slug>/`，取其中修改时间最新的 `.jsonl`。若该 slug 目录不存在（会话内 `cd` 过），**沿父目录向上**找最近的、在 `~/.claude/projects` 下有会话记录的目录（⚠️ 不要跨项目全局搜索，并发多会话时会误选别的 session），并向用户说明你用了这个父目录兜底。若最新文件修改时间早于 5 分钟，先向用户确认再继续（可能在后台会话中运行）。session 标识 = 文件名前 8 字符。

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
