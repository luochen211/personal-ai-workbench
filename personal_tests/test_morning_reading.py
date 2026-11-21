"""生财晨读脚本：开对话、等答完、交回执；工作台不通或助理没答完时写「没跑成」。内容是编的。"""
import importlib.util
import json
from datetime import date
from pathlib import Path

spec = importlib.util.spec_from_file_location("morning_reading", Path(__file__).resolve().parents[1] / "scripts" / "morning_reading.py")
reading = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reading)
DAY = date(2026, 11, 22)


def fake(replies):
    calls = []

    def request(port, path, body=None):
        calls.append((path, body))
        answer = replies.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    return calls, request


def test_waits_for_the_reply_and_sends_it_to_feishu(capsys):
    text = "1. **《FDE 报价》** https://scys.com/x\n为什么：…\n```issue-draft\n{}\n```"
    calls, request = fake([{"id": "c1"}, {"running": True}, {"running": False, "messages": [{"role": "user"}, {"role": "assistant", "text": text}]}])
    sent = []
    assert reading.main([], request=request, sleep=lambda s: None, today=lambda: DAY, send=lambda *a: sent.append(a)) == 0
    assert calls[0][0] == "/api/chat/send" and calls[0][1]["text"].startswith("生财晨读 11-22")
    assert capsys.readouterr().out.strip() == "生财晨读 11-22 已发个人飞书。"
    title, body, key = sent[0]
    assert title == "生财晨读 11-22" and key.startswith("scys-20261122-") and len(key) <= 50
    assert body.startswith("1. **《FDE 报价》**") and "issue-draft" not in body and body.endswith("工作台「对话」页。")


def test_feishu_failure_falls_back_to_a_hub_notice(capsys):
    _, request = fake([{"id": "c1"}, {"running": False, "messages": [{"role": "assistant", "text": "两篇"}]}])

    def broken(*_):
        raise RuntimeError("lark-cli 退出码 1：token expired")
    assert reading.main([], request=request, sleep=lambda s: None, today=lambda: DAY, send=broken) == 0
    out = capsys.readouterr().out
    assert out.startswith("生财晨读 11-22 飞书没发出去：lark-cli 退出码 1：token expired")
    notice = json.loads(out.split("```agent-autoloop\n")[1].split("\n```")[0])["notice"][0]
    assert notice["fyi"] and notice["key"] == "scys-reading-2026-11-22" and notice["text"].startswith("两篇")


def test_send_feishu_builds_the_lark_call(tmp_path, monkeypatch):
    (tmp_path / "rules.json").write_text(json.dumps({"channels": {"feishu": {"answerers": ["ou_me"]}}}))
    monkeypatch.setattr(reading, "HOME", tmp_path)
    got = []

    class Done:
        returncode, stdout, stderr = 0, '{"ok": true}', ""
    reading.send_feishu("生财晨读 11-22", "正文", "k1", run=lambda args, **kw: got.append(args) or Done())
    args = got[0]
    assert args[0] == str(tmp_path / "bin" / "lark-personal") and args[args.index("--user-id") + 1] == "ou_me"
    assert args[args.index("--as") + 1] == "bot" and args[args.index("--markdown") + 1] == "**生财晨读 11-22**\n\n正文"


def test_failures_say_not_done(capsys):
    _, request = fake([OSError("connection refused")])
    assert reading.main([], request=request, sleep=lambda s: None, today=lambda: DAY) == 1
    assert capsys.readouterr().out.startswith("没跑成：工作台没连上")
    _, request = fake([{"id": "c1"}, {"running": False, "messages": [{"role": "assistant", "text": "", "error": "额度用完了"}]}])
    assert reading.main([], request=request, sleep=lambda s: None, today=lambda: DAY) == 1
    assert "额度用完了" in capsys.readouterr().out


def test_gives_up_and_stops_after_the_wait(capsys, monkeypatch):
    monkeypatch.setattr(reading, "WAIT", 10)
    calls, request = fake([{"id": "c1"}, {"running": True}, {"running": True}, {"running": True}, {"ok": True}])
    assert reading.main([], request=request, sleep=lambda s: None, today=lambda: DAY) == 1
    assert calls[-1] == ("/api/chat/stop", {"id": "c1"}) and "没答完" in capsys.readouterr().out
