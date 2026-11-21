"""agent-autoloop 监看 issue 的薄入口，装在 ~/.agent-autoloop/issues/watch.py（python3 -m personal.agent_watch install）。

逻辑在派活仓库的 personal/agent_watch.py。仓库位置按顺序取：环境变量 PAIHUO_ROOT、同目录的 paihuo-root 文件
（工作台启动时发现它失效会自动改成当前仓库）。找不到仓库时退出码 1，并发一次桌面通知说明怎么修。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
POINTER = HERE / "paihuo-root"
ALERTED = HERE / "entry-alerted"


def root():
    value = os.environ.get("PAIHUO_ROOT")
    if not value and POINTER.exists():
        value = POINTER.read_text(encoding="utf-8").strip()
    return Path(value) if value else None


def alert_once(text):
    if ALERTED.exists() and ALERTED.read_text(encoding="utf-8") == text:
        return
    script = f"display notification {json.dumps(text, ensure_ascii=False)} with title \"派活：看 issue 没跑成\""
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        pass
    ALERTED.write_text(text, encoding="utf-8")


def main():
    repo = root()
    if not repo or not (repo / "personal/agent_watch.py").exists():
        text = (f"找不到派活仓库的 personal/agent_watch.py（paihuo-root：{repo or '未设置'}）。"
                "在仓库 main 分支运行 python3 -m personal.agent_watch install")
        print(text)
        alert_once(text)
        return 1
    ALERTED.unlink(missing_ok=True)
    sys.path.insert(0, str(repo))
    from personal.agent_watch import main as watch
    return watch([])


if __name__ == "__main__":
    sys.exit(main())
