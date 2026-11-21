---
description: 删除已 pin 的条目（二次确认后删除并重新编号）
argument-hint: "[编号|查询词]"
allowed-tools: Bash, Read, Write, Edit, Glob, AskUserQuestion
---

# /pin_delete — 删除已 Pin 的条目

列出 `~/.claude/pin_message.md` 中已 pin 的条目（**选择框**，协议同 /pin_show），用户选中一条，**二次确认**后删除并重新顺序编号。

参数 `$ARGUMENTS`：
- **纯数字**（如 `/pin_delete 3`）→ 直达：跳过列表，直接对 PIN #3 执行二次确认
- 其他文本 → 查询词（大小写不敏感子串匹配，只匹配问题摘要不匹配回答正文）
- 空 → 列出全部条目（最近在前）

## 步骤 1：定位引擎并执行（单次 Bash，检测与首调合并）

执行：
```bash
ENGINE=$(find "$HOME/workspace/pin_message/scripts" "$HOME/.claude/plugins" -name pin.py 2>/dev/null | head -1)
if command -v python3 >/dev/null && [ -n "$ENGINE" ]; then
  python3 "$ENGINE" --cwd "$PWD" delete $ARGUMENTS
else
  echo "NO_ENGINE"
fi
```

输出以 `{` 开头（引擎单行 JSON）→ 走【引擎路径】按输出处理；输出为 `NO_ENGINE` → 走【手动路径】。

按输出 JSON 处理：
- `search_results` → 按【选择协议】用 AskUserQuestion 呈现候选；选中后运行 `python3 "$(find "$HOME/workspace/pin_message/scripts" "$HOME/.claude/plugins" -name pin.py 2>/dev/null | head -1)" --cwd "$PWD" show <pin_no>` 取回该条内容，进入二次确认
- `error`（code=no_pins）→ 告知"还没有 pinned 消息"
- `error`（code=no_match）且有查询词 → 告知无匹配

选中条目后**二次确认**（⚠️ 必须，不可省略）：向用户单独追问"确认删除 PIN #N：<display 摘要>？此操作不可撤销。[y/n]"，只有明确回复 y/是/确认 才执行：
```bash
ENGINE=$(find "$HOME/workspace/pin_message/scripts" "$HOME/.claude/plugins" -name pin.py 2>/dev/null | head -1)
python3 "$ENGINE" --cwd "$PWD" delete <N>
```
按 `delete_result` 告知"已删除 PIN #N，剩余 M 条"。回复其他内容 → 视为取消，告知"已取消删除"。

## 手动路径（无 python3，全平台可用）

### 1-3. 读取、解析、直达或选择

与 /pin_show 手动路径第 1-3 步完全相同：Read 并解析 pin 文件（首行格式头校验失败则停止并提示，不擅自修复），按 PIN 编号倒序，按【选择协议】用 AskUserQuestion 呈现候选。

### 4. 二次确认 ⚠️ 必须

向用户单独追问"确认删除 PIN #N：<display 摘要>？此操作不可撤销。[y/n]"。只有明确回复 y/是/确认 才继续；其余回复视为取消，告知"已取消删除"并停止。

### 5. 删除并重编号

删除选中的条目后，用 Write 重写整个 pin 文件：保留格式头和 `# Pinned Messages`，剩余条目按原相对顺序**重新从 #1 开始顺序编号**（注意同步更新每条标题行编号，正文、`<!-- pin-uuid: -->` 标记、Q/A 内容不变）。告知"已删除 PIN #N，剩余 M 条"。

---

## 选择协议（/pin_search / /pin_show / /pin_delete 通用）

用 `AskUserQuestion` 工具呈现候选条目（单选 radio 选择框，**不再用文字编号列表**）。

每条候选 item → 一个 option：
- `label`：display 摘要（过长截断到约 40 字符）
- `description`：`PIN #<pin_no> · session <session> · <YYYY-MM-DD HH:MM>`（`<YYYY-MM-DD HH:MM>` 即引擎 `ts` 字段，已转本机时间；ts 为空则省略该段及其前导 ` · `）

映射规则：
- `multiSelect` 固定 `false`；单个 question，header 用「选择 pin」
- 候选 ≤ 4 条 → 一次全部呈现
- 候选 > 4 条 → 只取最近 4 条，question 文本末尾注明「共 N 条，仅展示最近 4 条，可用查询词缩小范围」
- 构建 option 时按顺序记住 label ↔ item 的对应关系；用户选中后用返回的 label 反查 item，取该 item 的 `pin_no`（/pin_show、/pin_delete）或 `uuid`（/pin_search）继续后续步骤
- 用户选「Other」：输入为纯数字且对应条目存在 → 视为选中该条；否则视为放弃，停止并说明
