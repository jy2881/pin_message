#!/usr/bin/env bash
# pin_message 双平台一键安装（Claude Code + Codex）
# 幂等：可重复执行，已有状态不破坏（symlink -f、生成物确定性覆盖、pin 文件存在即跳过）
# 用法：git clone https://github.com/jy2881/pin_message.git && cd pin_message && ./install.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CC_COMMANDS_DIR="$HOME/.claude/commands"
CC_PLUGINS_DIR="$HOME/.claude/plugins/pin_message"
PIN_FILE="${PIN_MESSAGE_FILE:-$HOME/.claude/pin_message.md}"
CODEX_PROMPTS_DIR="$HOME/.codex/prompts"
SKILLS_DIR="${SKILLS_ROOT:-$HOME/.agents/skills}"

COMMANDS="pin pin_search pin_show pin_delete"

echo "==> [1/4] Claude Code：安装 4 个命令（symlink）"
mkdir -p "$CC_COMMANDS_DIR"
for cmd in $COMMANDS; do
  ln -sfn "$REPO_DIR/commands/$cmd.md" "$CC_COMMANDS_DIR/$cmd.md"
  echo "  $CC_COMMANDS_DIR/$cmd.md -> commands/$cmd.md"
done

echo "==> [2/4] Claude Code：安装引擎（pin.py + codex_adapter.py）"
mkdir -p "$CC_PLUGINS_DIR"
for f in pin.py codex_adapter.py; do
  ln -sfn "$REPO_DIR/scripts/$f" "$CC_PLUGINS_DIR/$f"
done

echo "==> [3/4] 初始化 pin 文件（双平台共用；尊重 PIN_MESSAGE_FILE 覆盖）"
mkdir -p "$(dirname "$PIN_FILE")"
if [ ! -f "$PIN_FILE" ]; then
  printf '<!-- pin-message-format: 1 -->\n\n# Pinned Messages\n' > "$PIN_FILE"
  echo "  已创建 $PIN_FILE"
else
  echo "  已存在，跳过：$PIN_FILE"
fi

# ---- Codex 侧：prompts + skills 由 commands/*.md 生成（DRY：仓库内不存重复副本）----

if command -v python3 >/dev/null 2>&1; then
  echo "==> [4/4] Codex/IDE：生成自定义 prompts（调用形式 /prompts:<name>）"
  mkdir -p "$CODEX_PROMPTS_DIR"
  python3 - "$REPO_DIR" "$CODEX_PROMPTS_DIR" <<'PY'
import re
import sys
from pathlib import Path

repo, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
for src in sorted((repo / "commands").glob("*.md")):
    text = src.read_text(encoding="utf-8")
    body = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S).strip()
    header = (
        f"<!-- 由 install.sh 从 commands/{src.name} 生成，请勿手改；"
        f"调用形式：/prompts:{src.stem} -->\n\n"
    )
    (out_dir / src.name).write_text(header + body + "\n", encoding="utf-8")
    print(f"  {out_dir / src.name}")
PY

  echo "==> 生成 skills（~/.agents/skills，Codex/IDE 显式调用）"
  mkdir -p "$SKILLS_DIR"
  python3 - "$REPO_DIR" "$SKILLS_DIR" <<'PY'
import re
import sys
from pathlib import Path

repo, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
for src in sorted((repo / "commands").glob("*.md")):
    text = src.read_text(encoding="utf-8")
    m = re.search(r"\A---\n.*?^description:\s*(.+?)$.*?\n---\n", text, flags=re.S | re.M)
    desc = m.group(1).strip() if m else f"pin_message: {src.stem}"
    body = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S).strip()
    skill_dir = out_dir / src.stem
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill = f"---\nname: {src.stem}\ndescription: {desc}\n---\n\n{body}\n"
    (skill_dir / "SKILL.md").write_text(skill, encoding="utf-8")
    print(f"  {skill_dir / 'SKILL.md'}")
PY
else
  echo "==> [4/4] 未找到 python3：跳过 Codex prompts/skills 生成（引擎路径也不可用，"
  echo "     CC/Codex 均退化为手动路径；安装 python3 后重跑本脚本即可）"
fi

cat <<'EOF'

安装完成 ✓
  Claude Code ：/pin /pin_search /pin_show /pin_delete
  Codex CLI   ：/prompts:pin /prompts:pin_search /prompts:pin_show /prompts:pin_delete
                （或 skills 显式调用；注意不是 /pin）
  也可通过插件市场安装：/plugin marketplace add jy2881/pin_message
EOF
