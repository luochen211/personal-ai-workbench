"""插件登记表（personal/plugins）：装上才能用、去掉就停用、老数据推断。全部用临时数据目录，不碰真实微信。"""
import json
import stat
import sys

import pytest

from personal import plugins
from personal.plugins.wechat import inbox as wechat


@pytest.fixture
def data(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    return folder


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_old_data_counts_as_installed_until_the_registry_exists(data):
    assert plugins.status(data) == {"wechat": False}
    (data / "wechat.json").write_text("{}", encoding="utf-8")
    assert plugins.installed(data, "wechat")  # 老用户升级后照旧
    plugins.uninstall(data, "wechat")
    assert not plugins.installed(data, "wechat")  # 有了登记表就只认登记表
    assert (data / "wechat.json").exists()  # 数据留着


def test_install_needs_a_reader_the_first_time_and_keeps_an_existing_config(data, tmp_path):
    with pytest.raises(ValueError, match="--vault-dir"):
        plugins.install(data, "wechat")
    assert not plugins.installed(data, "wechat")
    lines = plugins.install(data, "wechat", vault_dir=str(tmp_path / "reader"), python="/usr/bin/python3")
    cfg = json.loads((data / "wechat.json").read_text())
    assert cfg == {"reader": "vault", "vault_dir": str(tmp_path / "reader"), "python": "/usr/bin/python3"}
    assert mode(data / "wechat.json") == 0o600 and mode(data / "plugins.json") == 0o600
    assert any("找不到微信读取工具的目录" in line for line in lines)  # 装上时就说清哪里不对
    (data / "wechat.json").write_text('{"skill_dir": "/old"}', encoding="utf-8")
    plugins.install(data, "wechat", vault_dir="/new", python="/new/python")
    assert json.loads((data / "wechat.json").read_text()) == {"skill_dir": "/old"}  # 已有的不改
    with pytest.raises(ValueError, match="没有这个插件"):
        plugins.install(data, "nope")


def test_uninstalled_wechat_is_off_everywhere(data, tmp_path):
    (data / "wechat.json").write_text(json.dumps({"vault_dir": str(tmp_path), "python": "/usr/bin/python3"}), encoding="utf-8")
    assert wechat.view(data)["enabled"] is True
    assert wechat.config(data)["skill_dir"] == str(tmp_path)  # vault_dir 是现在的写法
    plugins.uninstall(data, "wechat")
    assert wechat.config(data) is None
    assert wechat.view(data) == {"enabled": False, "plugin": False}
    assert wechat.kick(data) == {"enabled": False, "started": False}
    assert wechat.refresh(data) is False
    plugins.install(data, "wechat")
    assert wechat.view(data)["enabled"] is True


def test_command_line(data, capsys):
    run = lambda *argv: plugins.main(list(argv), data=data)
    assert run("list") == 0 and "wechat（微信）：没装" in capsys.readouterr().out
    assert run("install", "wechat") == 2 and "--vault-dir" in capsys.readouterr().err
    assert run("install", "wechat", "--vault-dir", "/r", "--python", "/p") == 0 and "已装上" in capsys.readouterr().out
    assert run("uninstall", "wechat") == 0 and "数据都留着" in capsys.readouterr().out
    assert run("list") == 0 and "没装" in capsys.readouterr().out


def test_running_workbench_follows_the_registry_without_restart(tmp_path, monkeypatch):
    from urllib.error import HTTPError
    from personal_tests.test_workspace import instance
    with instance(tmp_path, monkeypatch) as request:
        data = tmp_path / "instance"
        plugins.install(data, "wechat", vault_dir=str(tmp_path / "reader"), python="/usr/bin/python3")
        assert request("/api/wechat")["enabled"] is True
        plugins.uninstall(data, "wechat")
        assert request("/api/wechat") == {"enabled": False, "plugin": False}
        for path, body in (("/api/wechat/refresh", {}), ("/api/wechat/done", {"key": "wechat:private:abcdef", "ts": 1})):
            with pytest.raises(HTTPError) as error:
                request(path, body)
            assert error.value.code == 404
        plugins.install(data, "wechat")
        assert request("/api/wechat")["enabled"] is True


def test_loader_reads_manifests_and_gates_routes_and_hooks(data, capsys):
    assert plugins.KNOWN["wechat"]["name"] == "微信"
    handler, refused = plugins.route(data, "GET", "/api/wechat")
    assert handler is wechat.view and refused == ""  # open：没装也接，由插件回 enabled: false
    assert plugins.route(data, "POST", "/api/wechat/refresh") == (None, "微信插件没装")
    assert plugins.route(data, "GET", "/api/nothing") == (None, "")
    assert plugins.hook(data, "needs.prepare", data) == []  # 没装不调
    plugins.install(data, "wechat", vault_dir="/r", python="/p")
    assert plugins.route(data, "POST", "/api/wechat/refresh")[0] is wechat.kick_route
    assert plugins.hook(data, "needs.project_of", {"chat_key": "", "chat": "没登记"}, data=data) == [""]
    assert plugins.main(["wechat", "mappings"], data=data) == 0 and capsys.readouterr().out.strip() == "[]"


def test_install_and_uninstall_manage_the_plugin_rule(data, tmp_path, monkeypatch):
    from personal import agent_watch
    from personal_tests.test_agent_watch import realistic_rules
    home = tmp_path / "hub"
    home.mkdir()
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(home))
    rules = realistic_rules(home / "x")  # 写在 hub/rules.json
    checked = []
    monkeypatch.setattr(plugins, "_check_rules", lambda found: checked.append(found))
    lines = plugins.install(data, "wechat", vault_dir="/r", python="/p")
    assert any("加了 Agent wechat-issues" in line and "新增规则「微信拟 issue」" in line for line in lines) and len(checked) == 1
    found = json.loads(rules.read_text())
    root = str(agent_watch.ROOT)
    assert found["agents"]["wechat-issues"] == {"name": "微信拟 issue", "worker": "mini", "use": "command",
                                                "command": ["python3", f"{root}/personal/plugins/wechat/extract.py", "--no-refresh"]}
    assert [r for r in found["rules"] if r["name"] == "微信拟 issue"] == [
        {"name": "微信拟 issue", "when": {"source": "local", "types": ["wechat.issues.scan"]}, "to": "wechat-issues",
         "cwd": root, "tell": "never", "parallel": 1}]
    assert list(home.glob("rules.json.bak-*"))
    assert any("中台规则不用改" in line for line in plugins.install(data, "wechat"))  # 再装一次不改
    lines = plugins.uninstall(data, "wechat")
    found = json.loads(rules.read_text())
    assert "wechat-issues" not in found["agents"] and not any(r["name"] == "微信拟 issue" for r in found["rules"])
    assert not plugins.installed(data, "wechat") and "删了规则「微信拟 issue」" in lines[-1]


def test_install_moves_the_old_script_and_uninstall_keeps_edited_rules(data, tmp_path, monkeypatch):
    from personal import agent_watch
    from personal_tests.test_agent_watch import realistic_rules
    home = tmp_path / "hub"
    home.mkdir()
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(home))
    rules = realistic_rules(home / "x")
    monkeypatch.setattr(plugins, "_check_rules", lambda found: None)
    found = json.loads(rules.read_text())
    found["agents"]["wechat-issues"] = {"name": "微信拟 issue", "worker": "mini", "use": "command",
                                        "command": ["python3", "/work/assistant/scripts/wechat_issues.py", "--no-refresh"]}
    found["rules"].append({"name": "微信拟 issue", "when": {"source": "local", "types": ["wechat.issues.scan"]}, "to": "wechat-issues",
                           "cwd": "/work/assistant", "tell": "never", "parallel": 1})
    rules.write_text(json.dumps(found, ensure_ascii=False))
    lines = plugins.install(data, "wechat", vault_dir="/r", python="/p")
    rule_line = next(line for line in lines if line.startswith("中台规则"))
    assert "Agent wechat-issues 的命令换成插件里的" in rule_line and "规则「微信拟 issue」的目录换成本仓库" in rule_line
    found = json.loads(rules.read_text())
    assert found["agents"]["wechat-issues"]["command"][1].endswith("personal/plugins/wechat/extract.py")
    assert next(r for r in found["rules"] if r["name"] == "微信拟 issue")["cwd"] == str(agent_watch.ROOT)
    next(r for r in found["rules"] if r["name"] == "微信拟 issue")["to"] = "claude"  # 本人改过：去掉时不删
    rules.write_text(json.dumps(found, ensure_ascii=False))
    lines = plugins.uninstall(data, "wechat")
    found = json.loads(rules.read_text())
    assert any(r["name"] == "微信拟 issue" for r in found["rules"]) and "改过，没删" in lines[-1]
    assert "wechat-issues" not in found["agents"]  # Agent 没别的规则用了，命令也对得上：删


def test_a_rule_change_that_fails_the_check_changes_nothing(data, tmp_path, monkeypatch):
    from personal_tests.test_agent_watch import realistic_rules
    home = tmp_path / "hub"
    home.mkdir()
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(home))
    rules = realistic_rules(home / "x")
    before = rules.read_text()

    def refuse(found):
        raise ValueError("改好的中台规则没通过校验，rules.json 没动：坏了")
    monkeypatch.setattr(plugins, "_check_rules", refuse)
    with pytest.raises(ValueError, match="没通过校验"):
        plugins.install(data, "wechat", vault_dir="/r", python="/p")
    assert rules.read_text() == before and not plugins.installed(data, "wechat") and not list(home.glob("rules.json.bak-*"))


def test_articles_command_finds_each_accounts_vault_and_uses_the_readers_python(data, tmp_path, monkeypatch):
    import subprocess
    from personal.plugins.wechat import needs, reader
    plugins.install(data, "wechat", vault_dir=str(tmp_path / "reader"), python="/reader/python")
    monkeypatch.setattr(wechat, "config", lambda base, **k: {"problem": "", "python": "/reader/python", "skill_dir": "/r"})
    monkeypatch.setattr(reader.Vault, "decrypted_dir", lambda self, home=None, timeout=60: f"/vault/{home or 'default'}")
    monkeypatch.setattr(needs, "load_accounts", lambda: {"second": {"chats": [], "home": "/home2"}, "third": {"chats": [], "decrypted_dir": "/v3"}})
    seen = []
    monkeypatch.setattr(subprocess, "run", lambda args, check=False: seen.append(args) or subprocess.CompletedProcess(args, 0))
    assert plugins.main(["wechat", "articles", "--days", "3"], data=data) == 0
    assert seen[0][0] == "/reader/python" and seen[0][1].endswith("personal/plugins/wechat/articles.py")
    assert seen[0][2:] == ["--dir=wechat1=/vault/default", "--dir=second=/vault//home2", "--dir=third=/v3", "--days", "3"]


def test_purge_removes_wechat_data_but_not_shared_records(data, tmp_path, monkeypatch):
    from personal import agent_watch, issue_drafts, snooze
    monkeypatch.setattr(plugins, "update_rules", lambda change: "中台规则不用改")
    plugins.install(data, "wechat", vault_dir=str(tmp_path / "reader"), python="/p")
    for name in ("wechat-pending.json", "wechat-done.json", "wechat-projects.json", "wechat-people.json"):
        (data / name).write_text("{}", encoding="utf-8")
    (data / "snoozed.json").write_text(json.dumps({"items": {"wechat:private:abcdef1234": {"at": "2026-11-21T10:00:00+08:00"}, "o/r#1": {"at": "2026-11-21T10:00:00+08:00"}}}), encoding="utf-8")
    with issue_drafts.book(data) as found:
        found["drafts"].update({"d000000000001": {"source": {"kind": "wechat"}}, "d000000000002": {"source": {"kind": "audio"}}, "d000000000003": {"source": {"kind": "chat"}}})
    assert plugins.main(["uninstall", "wechat"], data=data) == 0 and (data / "wechat.json").exists()  # 默认留着
    lines = plugins.uninstall(data, "wechat", purge=True)
    assert not any((data / f).exists() for f in ("wechat.json", "wechat-pending.json", "wechat-done.json", "wechat-projects.json"))
    assert (data / "wechat-people.json").exists()  # 录音也用
    assert set(snooze.load(data)) == {"o/r#1"} and mode(data / "snoozed.json") == 0o600
    assert set(issue_drafts.load(data)["drafts"]) == {"d000000000002", "d000000000003"}
    assert any("微信条目 1 条、还没创建的微信草稿 1 条" in line for line in lines)
    assert not plugins.installed(data, "wechat")
    with pytest.raises(ValueError, match="--vault-dir"):  # 删了配置：重新装要重新给读取工具
        plugins.install(data, "wechat")


def test_backups_in_the_same_second_do_not_overwrite_each_other(tmp_path, monkeypatch):
    from datetime import datetime
    from personal import agent_watch
    monkeypatch.setattr(agent_watch, "now", lambda: datetime(2026, 11, 21, 10, 5, 28))
    target = tmp_path / "rules.json"
    first = agent_watch.backup_path(target)
    first.write_text("1")
    second = agent_watch.backup_path(target)
    assert first.name == "rules.json.bak-20261121100528" and second.name == "rules.json.bak-20261121100528-2"


def test_overview_lists_plugins_with_setup_checks(data, tmp_path, monkeypatch):
    monkeypatch.setattr(plugins, "update_rules", lambda change: "中台规则不用改")
    found = plugins.overview(data)["plugins"]
    assert [p["id"] for p in found] == ["wechat"] and found[0]["installed"] is False and found[0]["needs_config"] is True
    checks = {c["key"]: c for c in found[0]["checks"]}
    assert list(checks) == ["reader", "python", "disk", "rule", "chats", "data"]
    assert checks["reader"]["ok"] is False and "wechat.json" in checks["reader"]["detail"]
    assert checks["rule"]["ok"] is None and checks["chats"]["ok"] is False
    result = plugins.act(data, {"id": "wechat", "vault_dir": str(tmp_path / "reader"), "python": sys.executable})
    assert result["lines"][0] == "已装上「微信」插件" and result["plugins"][0]["installed"] and not result["plugins"][0]["needs_config"]
    python = {c["key"]: c for c in result["plugins"][0]["checks"]}["python"]
    assert python["ok"] in (True, False) and (python["ok"] or python["hint"].startswith(sys.executable))
    assert plugins.act(data, {"id": "wechat"}, remove=True)["plugins"][0]["installed"] is False
    with pytest.raises(ValueError, match="没有这个插件"):
        plugins.act(data, {"id": "nope"})


def test_missing_packages_are_reported_and_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(wechat.reader, "run", lambda command, timeout: calls.append(command) or (0, "zstandard\n", "", False))
    wechat._packages.clear()
    assert wechat.missing_packages("/py", at=1000) == ["zstandard"]
    assert wechat.missing_packages("/py", at=1300) == ["zstandard"] and len(calls) == 1  # 十分钟内用上次的
    assert wechat.missing_packages("/py", at=2000) == ["zstandard"] and len(calls) == 2


def test_plugin_dialog_endpoints(tmp_path, monkeypatch):
    from personal_tests.test_workspace import instance
    monkeypatch.setattr(plugins, "update_rules", lambda change: "中台规则不用改")
    with instance(tmp_path, monkeypatch) as request:
        assert request("/api/plugins")["plugins"][0]["installed"] is False
        done = request("/api/plugins/install", {"id": "wechat", "vault_dir": str(tmp_path / "reader"), "python": "/usr/bin/python3"})
        assert done["plugins"][0]["installed"] and request("/api/wechat")["enabled"] is True
        assert request("/api/plugins/uninstall", {"id": "wechat"})["plugins"][0]["installed"] is False
        assert request("/api/wechat") == {"enabled": False, "plugin": False}
