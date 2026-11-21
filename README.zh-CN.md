# pin-message

把 AI CLI 会话中重要的问答轮次 pin 下来，随时回看 —— 就像给你的对话历史加书签。同时支持 **Claude Code** 和 **Codex CLI**。

当一轮对话产出了你以后还会用到的东西（一条调试结论、一段配置、一个决策依据），pin 下来。下一次会话——或者三周之后——运行 `/pin_show`，点一下就能看到。

## 命令

| Claude Code | Codex CLI | 作用 |
|-------------|-----------|------|
| `/pin` | `/prompts:pin` | 把当前 session **最近一轮**问答 pin 到 `~/.claude/pin_message.md`（文件不存在则创建）。 |
| `/pin_search [查询词]` | `/prompts:pin_search` | 搜索本次 session 里你问过的所有真实提问，选中一条即 pin。长粘贴内容会压缩展示（如 `[Pasted text #1 +18 lines]`）。 |
| `/pin_show [编号\|查询词]` | `/prompts:pin_show` | 带编号：直达该 PIN。否则：弹出选择框 → 完整问答以 markdown 渲染展示。 |
| `/pin_delete [编号\|查询词]` | `/prompts:pin_delete` | 与 `/pin_show` 相同的选择框，y/n 二次确认后删除并重新编号。 |

## 选择协议

三个带选择框的命令（`/pin_search`、`/pin_show`、`/pin_delete`）都用**原生单选选择框**（`AskUserQuestion` 的 radio 对话框）呈现候选，不再用脆弱的文字编号列表。每个选项显示问题摘要、时间戳和 session 标识：

- 一次最多展示 4 条候选。
- 超过 4 条 → 只展示最近的 4 条，并提示用查询词缩小范围。
- 纯数字参数（如 `/pin_show 3`）直接跳过选择框。

## 双平台引擎

`--platform auto`（默认）自动探测 session 位置：

- **Claude Code**：`~/.claude/projects/<slug>/*.jsonl` —— 六条过滤规则把真实提问与工具回传、斜杠命令残留、sidechain 噪音、打断标记区分开。
- **Codex CLI**：`~/.codex/sessions/**/rollout-*.jsonl`（按 mtime 取最新）—— `response_item/message` 提取 `input_text`/`output_text`，排除工具输出 / `reasoning` / `turn_context`。遇到未知 schema 会 fail-loud（`codex_parse` 错误并附行样本），而不是静默乱猜。

两者同时存在时取较新的一侧，并在 stderr 给出警告；可用 `--platform cc|codex` 强制指定。

每个命令都有**手动路径**（无需 python3）：两种格式的完整解析规则书内嵌在命令提示词里，用 Read/Write/Edit 即可完成。

## 存储格式

`~/.claude/pin_message.md`（可用 `PIN_MESSAGE_FILE` 覆盖）：

```markdown
<!-- pin-message-format: 1 -->

# Pinned Messages

## [PIN #1] 2026-11-20 22:30 | session a19d9146
<!-- pin-uuid: 1c7e3e59-68ec-4972-bd6e-aff5017521b9 -->
**Q:** 现在API应该恢复了你再试试
**A:**
好，继续。写剩余 3 个命令。
```

- 两个平台共用同一个文件；每次读取都校验格式头——文件损坏会先备份（带时间戳的 `.bak`）再重建。
- 写入是原子的（tmp + rename），并用 `flock` 防止并发会话冲突。
- 通过 `uuid` 去重——重复 pin 同一轮会告诉你已有的 PIN 编号。
- 时间戳按本机时间存储和展示。

## 安装

**方式一 —— 一行命令（双平台）：**

```bash
git clone https://github.com/jy2881/pin_message.git && cd pin_message && ./install.sh
```

幂等；把四个命令软链到 `~/.claude/commands`，把引擎装到 `~/.claude/plugins/pin_message/`，从同一份源文件生成 Codex prompts（`~/.codex/prompts/`）和 skills（`~/.agents/skills/`），并创建 pin 文件。

**方式二 —— Claude Code 插件市场：**

```
/plugin marketplace add jy2881/pin_message
/plugin install pin-message@pin-message
```

**方式三 —— 官方 Claude 目录：** 本插件被官方目录收录后，在应用内插件目录里安装即可。

## 开发

```bash
python3 -m pytest tests/ -q   # 47 个测试：CC + Codex 解析、平台探测、选择协议、存储、锁、install.sh
```

## 隐私

本插件完全本地运行。pin 的内容只写入你机器上的 `~/.claude/pin_message.md`（或 `PIN_MESSAGE_FILE` 指向的位置）。不发起任何网络请求、不发送任何遥测、不使用任何第三方服务。你的对话内容不会离开你的机器。

## 支持

Bug 反馈与功能建议：https://github.com/jy2881/pin_message/issues

## 许可

[MIT](./LICENSE)
