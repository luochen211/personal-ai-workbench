"""可选的微信范围（personal/plugins/wechat/inbox.py）：没配置不启用；行和类别（只有私聊待回、群里 @我、待收转账算待你回）；
已处理、暂时不处理；后台刷新（成功、读不到微信目录、超时、不叠着跑）；接口。文字都是编的。"""
import json
import os
import stat
import sys
import threading
import time
from datetime import datetime, timedelta

import pytest

from personal import snooze
from personal.plugins.wechat import inbox as wechat, reader

FAKE_ID = "wxid_fake_contact_01"  # 编的账号标识：不能出现在接口和落盘结果里
GROUP_ID = "12345@chatroom"


def stamp(text):
    return int(datetime.strptime(text, "%Y-%m-%d %H:%M").timestamp())


def pending_json(last="2026-11-18 11:20"):
    """技能 vault_cli.py pending --include-identifiers --format json 的形状（字段取自源码），内容是编的。"""
    return {
        "generated_at": "2026-11-18 11:40",
        "window": {"since": stamp("2026-11-11 11:37"), "until": stamp("2026-11-18 11:37"), "since_text": "2026-11-11 11:37",
                   "until_text": "2026-11-18 11:37", "how": "近 7d"},
        "pending_days": 7,
        "manifest": {"name": "decrypt-20261118-113750.json", "created_at": "2026-11-18 11:37", "gaps": []},
        "counts": {},
        "private_pending": [
            {"chat": "阿青", "username": FAKE_ID, "chat_key": wechat.chat_key(FAKE_ID), "kind": "好友", "unread": 1,
             "waiting_count": 3, "waiting_since": "2026-11-18 10:00", "last_time": last, "last_ts": stamp(last),
             "new_in_window": True, "seen_before": False, "low_signal": False,
             "messages": [{"time": "2026-11-18 11:00", "ts": stamp("2026-11-18 11:00"), "from": "阿青", "text": "周五的样片能先发我吗"},
                          {"time": last, "ts": stamp(last), "from": "阿青", "text": "我下午要给客户看"}]},
            {"chat": "老周", "username": "wxid_fake_contact_02", "chat_key": wechat.chat_key("wxid_fake_contact_02"), "kind": "好友",
             "unread": 0, "waiting_count": 1, "waiting_since": "2026-11-17 09:00", "last_time": "2026-11-17 09:00",
             "last_ts": stamp("2026-11-17 09:00"), "new_in_window": True, "seen_before": False, "low_signal": True,
             "messages": [{"time": "2026-11-17 09:00", "ts": stamp("2026-11-17 09:00"), "from": "老周", "text": "好的谢谢"}]},
        ],
        "groups": {
            "at_me": [
                {"chat": "剪辑小组", "username": GROUP_ID, "time": "2026-11-18 09:00", "ts": stamp("2026-11-18 09:00"), "from": "小米",
                 "text": "@我 封面今天能定吗", "muted_group": False, "you_spoke_after": False,
                 "context": [{"time": "2026-11-18 08:58", "ts": stamp("2026-11-18 08:58"), "from": "小米", "text": "封面有两版"}]},
                {"chat": "剪辑小组", "username": GROUP_ID, "time": "2026-11-18 10:30", "ts": stamp("2026-11-18 10:30"), "from": "小米",
                 "text": "@我 再确认一下", "muted_group": False, "you_spoke_after": False, "context": []},
            ],
            "quote_me": [{"chat": "剪辑小组", "username": GROUP_ID, "time": "2026-11-18 08:00", "ts": stamp("2026-11-18 08:00"),
                          "from": "小米", "text": "同意（引用 我：先出粗剪）", "muted_group": False, "you_spoke_after": True, "context": []}],
            "name_mention": [],
            "at_all": [{"chat": "邻居群", "username": "999@chatroom", "time": "2026-11-17 20:00", "ts": stamp("2026-11-17 20:00"),
                        "from": "物业", "text": "@所有人 明天停水", "muted_group": True}],
        },
        "money": {
            "pending_to_me": [{"chat": "阿青", "username": FAKE_ID, "amount": "¥88.00", "memo": "定金", "time": "2026-11-18 09:30",
                               "from": "阿青", "expires": "2026-11-19 09:30", "maybe_expired": False}],
            "refunded_to_me": [],
            "pending_from_me": [{"chat": "老周", "username": "wxid_fake_contact_02", "amount": "¥20.00", "memo": "",
                                 "time": "2026-11-16 12:00", "to": "老周"}],
            "red_packets": [{"chat": "阿青", "username": FAKE_ID, "time": "2026-11-16 08:00", "ts": stamp("2026-11-16 08:00"),
                             "from": "阿青", "text": "[红包] 恭喜发财", "opened_hint": False}],
        },
        "missed_calls": [{"chat": "老周", "username": "wxid_fake_contact_02", "time": "2026-11-15 19:00",
                          "ts": stamp("2026-11-15 19:00"), "from": "老周", "text": "[通话] 未接听", "you_contacted_after": False}],
    }


@pytest.fixture
def data(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    return folder


def write_pending(data, raw=None):
    (data / wechat.PENDING).write_text(json.dumps(wechat.sanitize(raw or pending_json()), ensure_ascii=False), encoding="utf-8")


def configure(data, tmp_path, decrypt="ok", **extra):
    """假的技能目录：decrypt_all_dbs.py 按 decrypt（ok / abort / slow）表现，vault_cli.py 把编的结果写到 --output，
    并把收到的参数记进 calls.jsonl。python 是指向当前解释器的符号链接（提示里要给解开后的真实路径）。"""
    skill = tmp_path / "skill"
    (skill / "scripts").mkdir(parents=True, exist_ok=True)
    body = {
        "ok": "print('Done: 3 decrypted, 0 failed, 10 skipped')",
        "abort": ("import sys\nprint('ABORT: none of the 16 keyed WeChat source databases can be read [Errno 1]', file=sys.stderr)\n"
                  "print('Aborted: 0 decrypted, 16 unreadable, outputs unchanged')\nsys.exit(2)"),
        "slow": "import time\ntime.sleep(30)",
    }[decrypt]
    (skill / reader.Vault.DECRYPT).write_text(body + "\n", encoding="utf-8")
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(pending_json(), ensure_ascii=False), encoding="utf-8")
    (skill / reader.Vault.CLI).write_text(f"""
import json, os, sys
args = sys.argv[1:]
with open({str(tmp_path / 'calls.jsonl')!r}, 'a') as log:
    log.write(json.dumps(args) + '\\n')
out = args[args.index('--output') + 1]
fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, 'w') as f:
    f.write(open({str(fixture)!r}).read())
print(json.dumps({{"output": out}}))
""", encoding="utf-8")
    python = tmp_path / "bin/python"
    python.parent.mkdir(exist_ok=True)
    if not python.exists():
        python.symlink_to(sys.executable)
    (data / wechat.CONFIG).write_text(json.dumps({"skill_dir": str(skill), "python": str(python), **extra}), encoding="utf-8")
    return python


def by_key(report):
    return {r["key"]: r for r in report["rows"]}


def key(kind, ident):
    return f"wechat:{kind}:{wechat.chat_key(ident)}"


def test_disabled_without_config(data):
    assert wechat.config(data) is None
    assert wechat.view(data) == {"enabled": False, "plugin": False}
    assert wechat.kick(data) == {"enabled": False, "started": False}
    assert wechat.refresh(data) is False
    assert not (data / wechat.STATUS).exists()


def test_rows_buckets_and_badge_kinds(data, tmp_path):
    configure(data, tmp_path)
    write_pending(data)
    report = wechat.view(data)
    rows = by_key(report)
    assert report["enabled"] is True and report["has_data"] is True
    assert rows[key("private", FAKE_ID)]["bucket"] == "reply"
    assert rows[key("private", FAKE_ID)]["text"] == ["周五的样片能先发我吗", "我下午要给客户看"]
    assert "待回 3 条" in rows[key("private", FAKE_ID)]["notes"]
    assert rows[key("private", FAKE_ID)]["hits"][0]["more"].startswith("更早还有 1 条")
    assert rows[key("private", "wxid_fake_contact_02")]["bucket"] == "look"  # 疑似客套/表情不算待你回
    assert "疑似客套/表情" in rows[key("private", "wxid_fake_contact_02")]["notes"]
    at_me = rows[key("at_me", GROUP_ID)]
    assert at_me["bucket"] == "reply" and at_me["count"] == 2  # 同一个群的两条 @我 合成一行
    assert at_me["text"] == ["小米：@我 再确认一下"] and at_me["label"] == "群里 @我"
    assert at_me["hits"][0]["context"][0]["text"] == "封面有两版"
    assert rows[key("quote_me", GROUP_ID)]["bucket"] == "look"
    assert rows[key("at_all", "999@chatroom")]["bucket"] == "look"
    assert "免打扰群" in rows[key("at_all", "999@chatroom")]["notes"]
    money = rows[key("pending_to_me", FAKE_ID)]
    assert money["bucket"] == "reply" and money["text"] == ["阿青 转 ¥88.00（备注：定金），还没收"]
    assert money["ts"] == stamp("2026-11-18 09:30") and money["at"]
    assert rows[key("pending_from_me", "wxid_fake_contact_02")]["bucket"] == "look"
    assert rows[key("red_packets", FAKE_ID)]["bucket"] == "look"
    assert rows[key("missed_calls", "wxid_fake_contact_02")]["bucket"] == "look"
    assert {r["key"] for r in report["rows"] if r["bucket"] == "reply"} == {
        key("private", FAKE_ID), key("at_me", GROUP_ID), key("pending_to_me", FAKE_ID)}
    assert [r["ts"] for r in report["rows"]] == sorted((r["ts"] for r in report["rows"]), reverse=True)
    text = json.dumps(report, ensure_ascii=False)
    assert "wxid_" not in text and "@chatroom" not in text and "username" not in text  # 账号标识不出接口
    assert report["updated_at"].startswith("2026-11-18T11:37")


def test_done_hides_until_a_newer_message(data, tmp_path):
    configure(data, tmp_path)
    write_pending(data)
    row = by_key(wechat.view(data))[key("private", FAKE_ID)]
    wechat.mark_done(data, row["key"], row["ts"])
    assert stat.S_IMODE((data / wechat.DONE).stat().st_mode) == 0o600
    report = wechat.view(data)
    assert row["key"] not in by_key(report) and report["hidden"] == 1
    assert key("pending_to_me", FAKE_ID) in by_key(report)  # 同一个人的别的信号不受影响
    write_pending(data, pending_json(last="2026-11-18 12:05"))  # 对方又发了一条
    assert by_key(wechat.view(data))[row["key"]]["bucket"] == "reply"
    for bad in ("o/r#1", "wechat:private:XYZ"):
        with pytest.raises(ValueError):
            wechat.mark_done(data, bad, 1)
    with pytest.raises(ValueError):
        wechat.mark_done(data, row["key"], "soon")


def test_snooze_reuses_snooze_py_and_wakes_on_newer_message(data, tmp_path, monkeypatch):
    configure(data, tmp_path)
    write_pending(data)
    row = by_key(wechat.view(data))[key("at_me", GROUP_ID)]
    result = snooze.put(data, [{"subject": row["key"], "their_at": row["at"]}], "news")
    assert result["snoozed"] == [row["key"]]
    held = by_key(wechat.view(data))[row["key"]]
    assert held["bucket"] == "hold" and held["snooze"]["from"] == "reply"
    newer = pending_json()
    newer["groups"]["at_me"].append({**newer["groups"]["at_me"][1], "time": "2026-11-18 12:30", "ts": stamp("2026-11-18 12:30")})
    write_pending(data, newer)
    assert by_key(wechat.view(data))[row["key"]]["bucket"] == "reply"
    snooze.put(data, [{"subject": row["key"], "their_at": by_key(wechat.view(data))[row["key"]]["at"]}], "news")
    assert snooze.restore(data, [row["key"]])["restored"] == [row["key"]]
    assert by_key(wechat.view(data))[row["key"]]["bucket"] == "reply"


def test_refresh_success_writes_private_file_without_identifiers(data, tmp_path):
    configure(data, tmp_path, since="3d")
    assert wechat.refresh(data) is True
    target = data / wechat.PENDING
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    stored = target.read_text(encoding="utf-8")
    assert "wxid_" not in stored and "@chatroom" not in stored
    assert stat.S_IMODE((data / wechat.STATUS).stat().st_mode) == 0o600
    status = wechat.load_status(data)
    assert status["decrypt"]["ok"] and status["pending"]["ok"] and status["finished_at"]
    report = wechat.view(data)
    assert report["hint"] == "" and len(report["rows"]) == 9
    assert wechat.refresh(data) is True
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert calls[0][:4] == ["--include-identifiers", "pending", "--since", "3d"] and "--previous" not in calls[0]
    assert calls[1][calls[1].index("--previous") + 1] == str(target)  # 第二轮带上上次的结果
    assert not list(data.glob(".wechat-pending*"))  # 临时文件删掉了


def test_permission_abort_keeps_data_and_names_the_real_binary(data, tmp_path):
    python = configure(data, tmp_path, decrypt="abort")
    assert wechat.refresh(data) is True
    report = wechat.view(data)
    assert report["rows"]  # 照样给（上次的）数据
    real = str(python.resolve())
    assert real != str(python)
    assert report["hint"].startswith("后台进程没有读微信目录的权限，到 系统设置 → 隐私与安全性 → 完全磁盘访问 给 " + real + " 打开")
    status = wechat.load_status(data)
    assert status["decrypt"]["aborted"] and not status["decrypt"]["ok"] and status["pending"]["ok"]


def test_manifest_gap_from_an_aborted_run_also_hints(data, tmp_path):
    configure(data, tmp_path)
    raw = pending_json()
    raw["manifest"]["gaps"] = ["最近一次解析被中止（decrypt-20261119-180820.json），数据停留在2026-11-18 11:37"]
    write_pending(data, raw)
    assert "完全磁盘访问" in wechat.view(data)["hint"]


def test_decrypt_timeout_still_runs_pending(data, tmp_path):
    configure(data, tmp_path, decrypt="slow", decrypt_timeout=1)
    started = time.monotonic()
    assert wechat.refresh(data) is True
    assert time.monotonic() - started < 20
    status = wechat.load_status(data)
    assert status["decrypt"]["timeout"] and status["pending"]["ok"]
    assert wechat.view(data)["hint"] == "微信数据解析超时，下一轮再试"


def test_missing_skill_or_python_gives_a_reason_and_does_not_run(data, tmp_path):
    (data / wechat.CONFIG).write_text(json.dumps({"skill_dir": str(tmp_path / "nowhere"), "python": sys.executable}), encoding="utf-8")
    report = wechat.view(data)
    assert report["enabled"] and report["rows"] == [] and report["hint"].startswith("找不到微信读取工具的目录")
    assert wechat.kick(data)["started"] is False and wechat.refresh(data) is False
    configure(data, tmp_path)
    cfg = json.loads((data / wechat.CONFIG).read_text())
    (data / wechat.CONFIG).write_text(json.dumps({**cfg, "python": str(tmp_path / "no-python")}), encoding="utf-8")
    assert wechat.view(data)["hint"].startswith("找不到 Python")
    (data / wechat.CONFIG).write_text("{坏的", encoding="utf-8")
    assert wechat.view(data)["hint"].startswith("wechat.json 读不了")


def test_runs_never_overlap(data, tmp_path):
    configure(data, tmp_path)
    assert wechat._running.acquire(blocking=False)
    try:
        assert wechat.refresh(data) is False
        assert wechat.kick(data) == {"enabled": True, "started": False, "running": True}
        assert wechat.view(data)["running"] is True
    finally:
        wechat._running.release()
    assert not (tmp_path / "calls.jsonl").exists()


def test_due_follows_interval(data, tmp_path):
    configure(data, tmp_path, interval_minutes=15)
    cfg = wechat.config(data)
    assert cfg["interval_minutes"] == 15 and cfg["since"] == "7d"
    assert wechat.due(data, cfg)
    (data / wechat.STATUS).write_text(json.dumps({"started_at": wechat.now().isoformat()}))
    assert not wechat.due(data, cfg)
    assert wechat.due(data, cfg, at=wechat.now() + timedelta(minutes=16))


def test_due_backs_off_after_a_permission_abort(data, tmp_path):
    configure(data, tmp_path, interval_minutes=15)
    cfg = wechat.config(data)
    (data / wechat.STATUS).write_text(json.dumps({"started_at": wechat.now().isoformat(), "decrypt": {"aborted": True}}))
    assert not wechat.due(data, cfg, at=wechat.now() + timedelta(minutes=16))
    assert wechat.due(data, cfg, at=wechat.now() + timedelta(minutes=361))


def test_keeper_runs_once_when_due(data, tmp_path):
    configure(data, tmp_path)
    stop = threading.Event()
    thread, _ = wechat.start_keeper(data, stop)
    for _ in range(100):
        if (data / wechat.PENDING).exists() and wechat.load_status(data).get("finished_at"):
            break
        time.sleep(0.1)
    stop.set(); thread.join(5)
    assert wechat.load_status(data)["pending"]["ok"]


def test_endpoints(tmp_path, monkeypatch):
    from personal_tests.test_workspace import instance
    from urllib.error import HTTPError
    with instance(tmp_path, monkeypatch) as request:
        assert request("/api/wechat") == {"enabled": False, "plugin": False}
        with pytest.raises(HTTPError) as error:  # 没装微信插件（也没有老的 wechat.json）：写接口拒绝
            request("/api/wechat/refresh", {})
        assert error.value.code == 404
        data = tmp_path / "instance"
        configure(data, tmp_path)
        write_pending(data)
        report = request("/api/wechat")
        row = next(r for r in report["rows"] if r["kind"] == "private" and r["bucket"] == "reply")
        assert request("/api/wechat/done", {"key": row["key"], "ts": row["ts"]}) == {"done": row["key"]}
        assert row["key"] not in {r["key"] for r in request("/api/wechat")["rows"]}
        with pytest.raises(HTTPError) as error:
            request("/api/wechat/done", {"key": "wechat:private:zz", "ts": 1})
        assert error.value.code == 400
        with pytest.raises(HTTPError) as error:
            request("/api/wechat/done", {"key": row["key"], "ts": row["ts"]}, {"Origin": "https://outside.invalid"})
        assert error.value.code == 403
        other = next(r for r in report["rows"] if r["kind"] == "at_me")
        assert request("/api/snooze", {"items": [{"subject": other["key"], "their_at": other["at"]}], "wait": "1d"})["snoozed"] == [other["key"]]
        assert next(r for r in request("/api/wechat")["rows"] if r["key"] == other["key"])["bucket"] == "hold"
        started = request("/api/wechat/refresh", {})
        assert started["enabled"] is True and (started["started"] or started.get("running"))
        for _ in range(100):
            if not wechat._running.locked():
                break
            time.sleep(0.1)


def test_reader_is_chosen_by_wechat_json(data, tmp_path):
    configure(data, tmp_path)
    cfg = json.loads((data / wechat.CONFIG).read_text())
    assert isinstance(reader.open_reader(wechat.config(data)), reader.Vault)  # 没写 reader 就是本地明文库
    (data / wechat.CONFIG).write_text(json.dumps({**cfg, "reader": "other"}), encoding="utf-8")
    report = wechat.view(data)
    assert report["enabled"] and report["hint"] == "wechat.json 的 reader 不认识：other（现有：vault）"
    assert wechat.refresh(data) is False and not (tmp_path / "calls.jsonl").exists()


def test_vault_decrypt_can_run_under_another_home(tmp_path):
    vault = tmp_path / "vault"
    (vault / "scripts").mkdir(parents=True)
    (vault / reader.Vault.DECRYPT).write_text("import os; print('HOME=' + os.environ['HOME'])\n", encoding="utf-8")
    result = reader.Vault(vault, sys.executable).decrypt(30, home=tmp_path / "home2")
    assert result["ok"] and result["line"] == f"HOME={tmp_path / 'home2'}"
