---
description: 删除已 pin 的条目（二次确认后删除并重新编号）
argument-hint: "[编号|查询词]"
allowed-tools: Bash, Read, Write, Edit, Glob
---

# /pin_delete — 删除已 Pin 的条目

列出 `~/.claude/pin_message.md` 中已 pin 的条目（**编号列表**，协议同 /pin_show），用户回复数字选中一条，**二次确认**后删除并重新顺序编号。

参数 `$ARGUMENTS`：
- **纯数字**（如 `/pin_delete 3`）→ 直达：跳过列表，直接对 PIN #3 执行二次确认
- 其他文本 → 查询词（大小写不敏感子串匹配，只匹配问题摘要不匹配回答正文）
- 空 → 列出全部条目（最近在前）

## 步骤 1：检测引擎

执行：
```bash
ENGINE=$(find "$HOME/workspace/pin_message/scripts" "$HOME/.claude/plugins" -name pin.py 2>/dev/null | head -1)
command -v python3 >/dev/null && [ -n "$ENGINE" ] && echo "ENGINE=$ENGINE" || echo "NO_ENGINE"
```

输出以 `ENGINE=` 开头 → 走【引擎路径】；否则 → 走【手动路径】。

## 引擎路径

```bash
python3 "$ENGINE" --cwd "$PWD" delete $ARGUMENTS
```

按输出 JSON 处理：
- `search_results` → 按【编号列表协议】渲染，请用户回复数字；回复后运行 `python3 "$ENGINE" --cwd "$PWD" show <数字>` 取回该条内容，进入二次确认
- `error`（code=no_pins）→ 告知"还没有 pinned 消息"
- `error`（code=no_match）且有查询词 → 告知无匹配

选中条目后**二次确认**（⚠️ 必须，不可省略）：向用户单独追问"确认删除 PIN #N：<display 摘要>？此操作不可撤销。[y/n]"，只有明确回复 y/是/确认 才执行：
```bash
python3 "$ENGINE" --cwd "$PWD" delete <N>
```
按 `delete_result` 告知"已删除 PIN #N，剩余 M 条"。回复其他内容 → 视为取消，告知"已取消删除"。

## 手动路径（无 python3，全平台可用）

### 1-3. 读取、解析、直达或编号列表

与 /pin_show 手动路径第 1-3 步完全相同：Read 并解析 pin 文件（首行格式头校验失败则停止并提示，不擅自修复），按 PIN 编号倒序，按【编号列表协议】渲染并请用户回复数字。

### 4. 二次确认 ⚠️ 必须

向用户单独追问"确认删除 PIN #N：<display 摘要>？此操作不可撤销。[y/n]"。只有明确回复 y/是/确认 才继续；其余回复视为取消，告知"已取消删除"并停止。

### 5. 删除并重编号

删除选中的条目后，用 Write 重写整个 pin 文件：保留格式头和 `# Pinned Messages`，剩余条目按原相对顺序**重新从 #1 开始顺序编号**（注意同步更新每条标题行编号，正文、`<!-- pin-uuid: -->` 标记、Q/A 内容不变）。告知"已删除 PIN #N，剩余 M 条"。

---

## 编号列表协议（/pin_search / /pin_show / /pin_delete 通用）

向用户展示条目时，**逐条渲染为两行式**（标题行 + 元数据行，与 /resume 风格对齐）：

```
1. <display 摘要>
   <YYYY-MM-DD HH:MM> · session <session 标识> · <preview 前 60 字符，可省略>
2. …
```

然后请用户"回复数字选中（1-N），或回复 q 放弃"。交互规则：
- 回复纯数字且在 1-N 范围内 → 选中该项，继续执行后续步骤
- 回复非数字、越界或 q → 提示"请输入 1-N 的数字，或回复 q 放弃"并**重问一次**；再次无效 → 放弃并说明，**总计最多询问 2 次，不循环**
- 超过 15 条时只展示最近 15 条，附注"仅展示最近 15 条，可用查询词缩小范围"
