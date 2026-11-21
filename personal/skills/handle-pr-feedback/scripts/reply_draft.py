#!/usr/bin/env python3
"""把回复写成草稿，等用户在工作台「草稿待确认」里确认后再由工作台发出；这个脚本不发任何 GitHub 评论。

用法：
  python3 ~/.claude/skills/<技能>/scripts/reply_draft.py save <草稿.json>   # - 表示从标准输入读
  python3 ~/.claude/skills/<技能>/scripts/reply_draft.py show <owner/repo#编号>

技能目录是软链，这里先解开软链找到工作台仓库，再交给 personal/reply_drafts.py；草稿存进那个仓库的 .personal/reply-drafts.json。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # scripts → 技能 → skills → personal → 仓库根目录
sys.path.insert(0, str(ROOT))

from personal.reply_drafts import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
