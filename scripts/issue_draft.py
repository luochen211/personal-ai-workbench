#!/usr/bin/env python3
"""把提炼出的需求存成 issue 草稿，等用户在工作台收件箱的「新需求」抽屉里确认后再由工作台创建；这个脚本不写 GitHub。

用法（在哪个目录运行都行）：
  python3 <工作台仓库>/scripts/issue_draft.py save <草稿.json>            # 一条对象或数组；- 表示从标准输入读
  python3 <工作台仓库>/scripts/issue_draft.py list                        # 待确认的草稿（不带原话）
  python3 <工作台仓库>/scripts/issue_draft.py projects                    # 能开 issue 的项目（id、名称、仓库）
  python3 <工作台仓库>/scripts/issue_draft.py scan-done --found N --saved M [--error 原因]   # 抽取脚本跑完交回结果
  python3 <工作台仓库>/scripts/issue_draft.py plugins                     # 哪些插件装了
登记要读的微信会话是微信插件的命令：python3 -m personal.plugins wechat map|unmap|mappings（在工作台仓库根目录运行）。

草稿存进数据目录（默认仓库下 .personal/，环境变量 WORKBENCH_DATA 可换）的 issue-drafts.json，逻辑在 personal/issue_drafts.py。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from personal.issue_drafts import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
