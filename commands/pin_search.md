---
description: 搜索当前 session 的历史提问，选中后 pin 到 ~/.claude/pin_message.md
argument-hint: "[查询词]"
allowed-tools: Bash, Read, Write, Edit, Glob, AskUserQuestion
---

# /pin_search — 搜索并 Pin 历史提问

在当前 session 中提取全部"真实用户提问"（按【文末规则书】），用 AskUserQuestion 选择框让用户选中一条，将其问答对写入 `~/.claude/pin_message.md`。

参数 `$ARGUMENTS` = 查询词（可空）。查询词为**大小写不敏感子串匹配，只匹配问题摘要不匹配回答正文**。

## 步骤 1：检测引擎

执行：
```bash
ENGINE=$(find "$HOME/.claude/plugins" "$HOME/workspace" -maxdepth 5 -path '*pin_message/scripts/pin.py' 2>/dev/null | head -1); command -v python3 >/dev/null && test -n "$ENGINE" && echo "ENGINE=$ENGINE" || echo "NO_ENGINE"
```

输出以 `ENGINE=` 开头 → 走【引擎路径】；否则 → 走【手动路径】。

## 引擎路径

```bash
python3 "$ENGINE" --cwd "$PWD" search --page 1 $ARGUMENTS
```

按输出 JSON 处理：
- `search_results` → 用 AskUserQuestion 展示本页 items（每项 label = display，最多 3 项 + 第 4 项"下一页"）。用户选"下一页"→ 重新运行同命令 `--page N+1`（最多 5 页；第 5 页后仍要选"下一页"则告知"已展示全部，可换查询词重试"）。用户选中某项 → 取该 item 的 `uuid`，运行 `python3 "$ENGINE" --cwd "$PWD" write --uuid <uuid>`，按 write 结果告知用户（含 duplicate 提示）
- `error`（code=no_match）→ 告知用户，可建议换查询词或不带查询词列出最近 15 条
- `warn`（stale_transcript）→ 用 AskUserQuestion 向用户确认后继续
- 用户按 Esc 取消 → 安静停止，不写文件

## 手动路径（无 python3，全平台可用）

### 1. 提取提问列表

按【文末规则书】定位并解析当前 session transcript，提取全部真实用户提问（含配对回答和 uuid），**按时间倒序取最近 15 条**。

没有任何合格提问 → 告知"当前 session 还没有可 pin 的对话"并停止。

### 2. 过滤

若 $ARGUMENTS 非空：仅保留 display 摘要（规则书第 6 条）中包含该查询词（大小写不敏感）的条目。过滤后为空 → 告知"没有匹配「$ARGUMENTS」的提问"。

### 3. 选择框分页

用 AskUserQuestion 展示：每项 label = 条目 display 摘要（不超过 3 项），第 4 项固定"下一页"。最多翻 5 页（共 15 条）。翻完仍无选中 → 告知"已展示全部，可换查询词重试"。用户 Esc → 停止。

### 4. 写入

选中后按 /pin 的手动路径第 2 步写入 `~/.claude/pin_message.md`（uuid 去重、格式见规则书）。

---

## 规则书（transcript 解析，已针对 Claude Code 2.x 实测验证）

**定位**：slug = 当前工作目录绝对路径把每个 `/` 换成 `-`（保留开头 `-`），如 `/Users/pan/work` → `-Users-pan-work`；transcript 目录为 `~/.claude/projects/<slug>/`，取其中修改时间最新的 `.jsonl`。若最新文件修改时间早于 5 分钟，先向用户确认再继续（可能在后台会话中运行）。

**解析**：文件每行一个 JSON 对象；解析失败的行直接忽略。条目文本提取：`message.content` 为字符串则用之；为数组则拼接所有 `{"type":"text"}` 块的 `text` 字段。

**真实用户提问判定**（一条 `type:user` 条目须同时满足以下 6 条）：
1. `isSidechain` 为假或缺失
2. `isMeta` 为空/假/缺失（排除 skill dump 等元消息）
3. **不含 `tool_result` 块**：条目无 `toolUseID` 字段，且 `message.content` 数组中无 `{"type":"tool_result"}` 块。⚠️ 这类条目 `userType=external`、`isSidechain=false`，元数据与真实提问无法区分，必须检查块类型
4. 提取文本非空
5. 文本不含 `<command-name>` —— 斜杠命令在 transcript 中的真实格式是 `<command-message>pin</command-message>\n<command-name>/pin</command-name>\n<command-args>...`，不是以 `/pin` 开头
6. 文本不以 `[Request interrupted` 开头（系统注入的打断标记，常是 session 最后一条 user 消息，极易误当提问）

**配对回答**：该提问之后、**下一个合格提问出现之前**，最近一个 `type:assistant` 且 content 含至少一个 text 块的条目，拼接其全部 text 块；跳过仅含 thinking/tool_use 的条目（防止用户打断后答案跨界配对）。

**display 摘要（第 6 条）**：提问中含 `[Pasted text ...]` 占位符行则原样保留；单条文本超过 500 字符则压缩为 `首行(≤80字符) [Pasted text +N lines]`（N = 总行数）；否则取首行截 80 字符。pasted 占位符示例：`[Pasted text #1 +18 lines]`。

**uuid**：取配对 assistant 条目的 `uuid` 字段值（顶层字段）。

**pin 文件**：路径 `~/.claude/pin_message.md`，首行必须是格式头 `<!-- pin-message-format: 1 -->`。追加条目格式：
```markdown
## [PIN #N] <本地时间 YYYY-MM-DD HH:MM> | session <transcript 文件名前 8 字符>
<!-- pin-uuid: <assistant 条目 uuid> -->
**Q:** <display 摘要>
**A:**
<完整回答原文>
```
写入前先检查现有条目的 uuid 是否重复（重复则告知已有编号，不追加）。用 Write/Edit 工具写文件。
