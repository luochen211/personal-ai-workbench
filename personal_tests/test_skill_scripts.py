"""handle-pr-feedback 技能自带的评审意见账本脚本的单元测试（只用标准库，不碰 GitHub）。"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "personal" / "skills"


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ledger = load("review_ledger", "handle-pr-feedback/scripts/review_ledger.py")


def test_ledger_splits_numbered_bullets_and_circled_items():
    body = "两点不挡合并、供参考：①第一件；②第二件\n\n还要改的如下：\n**1. 地铁图**\n- 去掉站灯\n- 补表\n2. 首页接真数据"
    items = ledger.split_body(body)
    assert [i[0] for i in items] == ["①", "②", "1.1", "1.2", "2"]
    assert ledger.severity(items[0][1], items[0][2], body) == "建议"
    assert ledger.severity(items[2][1], items[2][2], body) == "必改"


def test_ledger_status_rules():
    assert ledger.judge("已改：提交 1a2b3c4；验证 `pytest -q` → 3 passed") == ""
    assert ledger.judge("已改：提交 1a2b3c4") == "已改但没写怎么验证的"
    assert ledger.judge("不改：理由 评审说留作参考") == ""
    assert ledger.judge("另开") == "另开但没给链接或 #号"
    assert ledger.judge("待处理") == "还没处理"
