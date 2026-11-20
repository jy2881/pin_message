---
description: 删除已 pin 的条目（二次确认后删除并重新编号）
argument-hint: "[查询词]"
allowed-tools: Bash, Read, Write, Edit, Glob, AskUserQuestion
---

# /pin_delete — 删除已 Pin 的条目

列出 `~/.claude/pin_message.md` 中已 pin 的条目（流程同 /pin_show），用户选中一条后**二次确认**，确认后删除并重新顺序编号。

参数 `$ARGUMENTS` = 查询词（可空，大小写不敏感子串匹配，只匹配问题摘要不匹配回答正文）。

## 步骤 1：检测引擎

执行：
```bash
ENGINE=$(find "$HOME/.claude/plugins" "$HOME/workspace" -maxdepth 5 -path '*pin_message/scripts/pin.py' 2>/dev/null | head -1); command -v python3 >/dev/null && test -n "$ENGINE" && echo "ENGINE=$ENGINE" || echo "NO_ENGINE"
```

输出以 `ENGINE=` 开头 → 走【引擎路径】；否则 → 走【手动路径】。

## 引擎路径

```bash
python3 "$ENGINE" --cwd "$PWD" pins --page 1 $ARGUMENTS
```

按输出 JSON 处理：
- `search_results` → AskUserQuestion 展示（label = display，最多 3 项 + "下一页"，翻页协议同 /pin_search，最多 5 页）。选中某项 → **二次确认**：AskUserQuestion 单独一问"确认删除 PIN #N：<display 摘要>？此操作不可撤销。"，选项"确认删除" / "取消"。确认后运行 `python3 "$ENGINE" --cwd "$PWD" delete --pin-no <item.pin_no>`，按 `delete_result` 告知"已删除 PIN #N，剩余 M 条"
- `error`（code=no_match）→ 无查询词时告知"还没有 pin 任何内容"；有查询词时告知无匹配
- 任一步用户 Esc → 停止，不做任何修改

## 手动路径（无 python3，全平台可用）

### 1-3. 列表、过滤、选择框

与 /pin_show 手动路径第 1-3 步完全相同：Read 并解析 pin 文件（首行格式头校验失败则停止并提示，不擅自修复），按 PIN 编号倒序，AskUserQuestion 选择框（3 项 + "下一页"，最多 5 页）。

### 4. 二次确认 ⚠️ 必须

AskUserQuestion 单独一问："确认删除 PIN #N：<display 摘要>？此操作不可撤销。" 选项"确认删除" / "取消"。取消或 Esc → 停止。

### 5. 删除并重编号

删除选中的条目后，用 Write 重写整个 pin 文件：保留格式头和 `# Pinned Messages`，剩余条目按原相对顺序**重新从 #1 开始顺序编号**（注意同步更新每条标题行编号，正文、`<!-- pin-uuid: -->` 标记、Q/A 内容不变）。告知"已删除 PIN #N，剩余 M 条"。

---

## 附：display 摘要规范

选择框中只展示问题摘要（pin 文件中的 `**Q:**` 行原文），长于 80 字符则截断 + "…"。
