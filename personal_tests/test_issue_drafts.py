"""issue 草稿：抽取脚本交草稿、去重、页面确认创建（假的 gh，不真发）、不创建、会话白名单、扫描状态、中台规则、接口守卫。
聊天内容全是编的。"""
import json
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError

import pytest

from personal import agent_watch, github, issue_drafts
from personal.plugins.wechat import needs
from personal_tests.test_agent_watch import realistic_rules
from personal_tests.test_workspace import instance

T0 = datetime(2026, 11, 20, 10, 0, tzinfo=timezone(timedelta(hours=8)))
PROJECTS = [
    {"id": "tool", "name": "拓客工具", "github": "me/tool"},
    {"id": "up", "name": "上游", "github": "them/lib", "issue_scope": "mine"},
    {"id": "old", "name": "旧的", "github": "me/old", "archived": True},
    {"id": "held", "name": "停的", "github": "me/held", "paused": True},
    {"id": "local", "name": "只在本地", "github": ""},
]
SAID = "下周一之前把导出按钮加上吧，客户那边催了三次了"


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    def nope(args, kind, **kwargs):
        raise AssertionError(f"测试不该真的请求 GitHub：{args}")
    monkeypatch.setattr(github, "run", nope)
    monkeypatch.setattr(agent_watch, "run", lambda args, timeout=120: (_ for _ in ()).throw(AssertionError(f"测试不该真的触发中台：{args}")))
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path / "hub"))
    monkeypatch.setenv("WECHAT_ISSUES_ACCOUNTS", str(tmp_path / "accounts.json"))  # 不读本机真的那份
    monkeypatch.setenv("RECORDING_ISSUES_CONFIG", str(tmp_path / "no-recordings.json"))
    monkeypatch.setattr(issue_drafts, "_spawn", lambda job: job())  # 后台那一步在测试里直接跑完
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError(f"测试不该真的起子进程：{a[0]}")))
    (tmp_path / "hub").mkdir()
    github.reset()
    yield
    github.reset()


@pytest.fixture
def data(tmp_path):
    """装了微信插件的数据目录（大部分用例测的是装了以后的行为；没装的另有用例）。"""
    folder = tmp_path / "data"
    folder.mkdir(exist_ok=True)
    (folder / "plugins.json").write_text('{"wechat": {"installed": true}}', encoding="utf-8")
    return folder


def payload(**extra):
    return {"source": {"chat_key": "abc1234567", "chat": "演示群", "kind": "group"},
            "excerpt": [{"time": "2026-11-20T09:30:00+08:00", "from": "甲", "text": SAID}],
            "message_at": "2026-11-20T09:30:00+08:00", "repo": "me/tool", "candidates": ["tool", "nope", "old"],
            "title": "列表页加导出按钮", "body": "客户要在列表页导出数据。\n\n- 期限：下周一", "labels": ["enhancement"], **extra}


def poster(out="https://github.com/me/tool/issues/12\n", fail=None):
    calls = []

    def post(args, body):
        calls.append((args, body))
        if fail:
            raise fail
        return out
    post.calls = calls
    return post


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_save_validates_and_writes_a_private_file(data):
    result = issue_drafts.save(payload(), PROJECTS, data=data, at=T0)
    draft = result["saved"][0]
    assert result["skipped"] == [] and result["errors"] == []
    assert draft["repo"] == "me/tool" and draft["project_id"] == "tool" and draft["candidates"] == ["tool"]
    assert draft["status"] == "pending" and draft["created_at"] == "2026-11-20T10:00:00+08:00"
    assert issue_drafts.ID.fullmatch(draft["id"])
    assert mode(data / "issue-drafts.json") == 0o600
    shown = issue_drafts.listing(PROJECTS, data)
    assert [d["id"] for d in shown["drafts"]] == [draft["id"]]
    assert "chat_key" not in shown["drafts"][0]["source"] and shown["drafts"][0]["source"]["chat"] == "演示群"
    assert shown["projects"] == [{"id": "tool", "name": "拓客工具", "repo": "me/tool"}, {"id": "up", "name": "上游", "repo": "them/lib"}]
    assert shown["scan"]["status"] == "idle"


@pytest.mark.parametrize("change, why", [
    ({"repo": "x/unknown"}, "不是登记表里能开 issue 的仓库"),
    ({"repo": "me/old"}, "不是登记表里能开 issue 的仓库"),
    ({"repo": "me/held"}, "不是登记表里能开 issue 的仓库"),
    ({"title": " "}, "标题是空的"),
    ({"title": "x" * 201}, "标题太长"),
    ({"body": ""}, "正文是空的"),
    ({"body": "由 Claude 整理"}, "AI 工具"),
    ({"title": "Codex 建议加导出"}, "AI 工具"),
    ({"body": f"原话：{SAID}"}, "聊天原话"),
    ({"labels": ["agent"]}, "agent 标签"),
    ({"labels": "bug"}, "字符串数组"),
    ({"excerpt": "一句话"}, "excerpt"),
])
def test_save_rejects_bad_drafts(data, change, why):
    result = issue_drafts.save(payload(**change), PROJECTS, data=data)
    assert result["saved"] == [] and why in result["errors"][0]["error"]
    assert issue_drafts.load(data)["drafts"] == {}


def test_same_thing_is_drafted_once(data):
    first = issue_drafts.save([payload(), payload()], PROJECTS, data=data)
    assert len(first["saved"]) == 1 and first["skipped"][0]["why"] == "已有待确认的草稿"
    draft = first["saved"][0]
    issue_drafts.create(draft["id"], PROJECTS, data=data, post=poster())
    assert issue_drafts.save(payload(), PROJECTS, data=data)["skipped"][0]["why"] == "已创建过"
    other = issue_drafts.save(payload(title="另一件事", dedupe_key="k2"), PROJECTS, data=data)["saved"][0]
    assert other["dedupe_key"] == "k2"
    issue_drafts.reject(other["id"], data=data)
    assert issue_drafts.save(payload(title="换个说法", dedupe_key="k2"), PROJECTS, data=data)["skipped"][0]["why"] == "你选过不创建"
    assert issue_drafts.load(data)["drafts"] == {}


def test_create_sends_only_title_body_labels_and_drops_the_excerpt(data):
    draft = issue_drafts.save(payload(), PROJECTS, data=data)["saved"][0]
    post = poster()
    result = issue_drafts.create(draft["id"], PROJECTS, title="列表页加导出", body="改过的正文", data=data, post=post, at=T0)
    assert result == {"ok": True, "url": "https://github.com/me/tool/issues/12", "repo": "me/tool", "repo_created": False}
    args, body = post.calls[0]
    assert args == ["gh", "issue", "create", "--repo", "me/tool", "--title", "列表页加导出", "--body-file", "-", "--label", "enhancement"]
    assert body == "改过的正文" and github.writes(args)
    assert SAID not in json.dumps(post.calls, ensure_ascii=False)
    text = (data / "issue-drafts.json").read_text(encoding="utf-8")
    assert SAID not in text and "演示群" not in text  # 创建后原话和会话名不留
    saved = issue_drafts.load(data)
    assert saved["drafts"] == {} and saved["history"][0]["status"] == "created"
    assert saved["history"][0]["issue_url"] == "https://github.com/me/tool/issues/12"
    with pytest.raises(ValueError, match="已经创建过了：https://github.com/me/tool/issues/12"):
        issue_drafts.create(draft["id"], PROJECTS, data=data, post=post)  # 再点一次不重复创建
    assert len(post.calls) == 1


def test_create_refuses_quotes_ai_words_and_missing_repo(data):
    draft = issue_drafts.save(payload(repo=""), PROJECTS, data=data)["saved"][0]
    post = poster()
    with pytest.raises(ValueError, match="还没选仓库"):
        issue_drafts.create(draft["id"], PROJECTS, data=data, post=post)
    with pytest.raises(ValueError, match="聊天原话"):
        issue_drafts.create(draft["id"], PROJECTS, repo="me/tool", body=f"他说：{SAID}", data=data, post=post)
    with pytest.raises(ValueError, match="AI 工具"):
        issue_drafts.create(draft["id"], PROJECTS, repo="me/tool", body="Generated with x", data=data, post=post)
    with pytest.raises(ValueError, match="不是登记表里能开 issue 的仓库"):
        issue_drafts.create(draft["id"], PROJECTS, repo="me/old", data=data, post=post)
    with pytest.raises(ValueError, match="草稿编号不对"):
        issue_drafts.create("../x", PROJECTS, data=data, post=post)
    assert post.calls == [] and issue_drafts.load(data)["drafts"][draft["id"]]["status"] == "pending"


def test_failed_create_keeps_the_draft_and_the_edits(data):
    draft = issue_drafts.save(payload(), PROJECTS, data=data)["saved"][0]
    result = issue_drafts.create(draft["id"], PROJECTS, title="改过的标题", data=data, post=poster(fail=RuntimeError("HTTP 403: Resource not accessible")))
    assert result["ok"] is False and "没有权限" in result["error"] and "草稿还在" in result["error"]
    kept = issue_drafts.load(data)["drafts"][draft["id"]]
    assert kept["status"] == "failed" and kept["title"] == "改过的标题" and kept["excerpt"]
    slow = issue_drafts.create(draft["id"], PROJECTS, data=data, post=poster(fail=subprocess.TimeoutExpired("gh", 60)))
    assert "不确定有没有创建成功" in slow["error"]
    assert issue_drafts.listing(PROJECTS, data)["drafts"][0]["error"] == slow["error"]
    assert issue_drafts.create(draft["id"], PROJECTS, data=data, post=poster())["ok"] is True  # 失败的能再点


def test_two_clicks_at_once_create_one_issue(data):
    draft = issue_drafts.save(payload(), PROJECTS, data=data)["saved"][0]
    seen = []

    def post(args, body):
        with pytest.raises(ValueError, match="正在创建"):
            issue_drafts.create(draft["id"], PROJECTS, data=data, post=post)
        seen.append(args)
        return "https://github.com/me/tool/issues/1\n"
    assert issue_drafts.create(draft["id"], PROJECTS, data=data, post=post)["ok"] is True
    assert len(seen) == 1


def test_picking_a_repo_for_an_unsorted_draft_remembers_the_chat(data):
    draft = issue_drafts.save(payload(repo=""), PROJECTS, data=data)["saved"][0]
    assert draft["repo"] == ""
    issue_drafts.create(draft["id"], PROJECTS, repo="them/lib", data=data, post=poster("https://github.com/them/lib/issues/3\n"), at=T0)
    assert mode(data / "wechat-projects.json") == 0o600
    assert json.loads((data / "wechat-projects.json").read_text(encoding="utf-8")) == {
        "chats": {"abc1234567": {"project_id": "up", "chat": "演示群", "at": "2026-11-20T10:00:00+08:00"}}}
    assert needs.project_of("abc1234567", data=data) == "up" and needs.project_of("", "演示群", data=data) == "up"
    again = issue_drafts.save(payload(repo="", title="第二件事"), PROJECTS, data=data)["saved"][0]
    assert again["repo"] == "them/lib" and again["project_id"] == "up"  # 同一个会话下次直接对应


def test_map_registers_chats_by_name(data, capsys):
    run = lambda *argv: needs.cli(list(argv), projects=PROJECTS, data=data)
    assert run("map", "--chat", "演示群", "--project", "tool") == 0
    assert run("map", "--chat", "演示群", "--project", "old") == 1 and "不是登记表里能开 issue 的项目" in capsys.readouterr().err
    assert needs.mappings(data) == [{"key": "演示群", "chat": "演示群", "project_id": "tool", "at": needs.mappings(data)[0]["at"]}]
    assert mode(data / "wechat-projects.json") == 0o600
    # 抽取脚本交来的草稿带 chat_key，白名单按会话名登记：照样对应上
    draft = issue_drafts.save(payload(repo=""), PROJECTS, data=data)["saved"][0]
    assert draft["repo"] == "me/tool"
    assert run("mappings") == 0 and json.loads(capsys.readouterr().out)[0]["project_id"] == "tool"
    assert run("unmap", "--chat", "演示群") == 0 and needs.mappings(data) == []


def test_command_line_saves_lists_and_reports_the_scan(data, tmp_path, capsys):
    run = lambda *argv: issue_drafts.main(list(argv), projects=PROJECTS, data=data)
    file = tmp_path / "in.json"
    file.write_text(json.dumps([payload(), payload(title="", dedupe_key="bad")], ensure_ascii=False), encoding="utf-8")
    assert run("save", str(file)) == 1  # 一条写得不对：其余照存，退出码 1
    out = capsys.readouterr()
    assert "已存 1 条 issue 草稿" in out.out and "第 2 条没存：标题是空的" in out.err
    assert run("list") == 0
    listed = capsys.readouterr().out
    assert "列表页加导出按钮" in listed and SAID not in listed
    assert run("projects") == 0 and json.loads(capsys.readouterr().out)[0] == {"id": "tool", "name": "拓客工具", "repo": "me/tool"}
    assert run("scan-done", "--source", "wechat", "--found", "5", "--saved", "1") == 0
    assert json.loads(capsys.readouterr().out)["status"] == "done"


def test_scan_runs_once_per_source_and_times_out(data):
    emitted = []
    emit = lambda source, event_id: emitted.append(event_id)
    first = issue_drafts.start_scan(data=data, emit=emit, at=T0)
    assert first["status"] == "running" and first["requested_at"] == "2026-11-20T10:00:00+08:00"
    assert emitted == ["scan:recordings:20261120T100000", "scan:wechat:20261120T100000"]  # 录音（核心）和微信（插件）各一条
    assert mode(data / "issue-drafts-scan.json") == 0o600
    assert issue_drafts.start_scan(data=data, emit=emit, at=T0 + timedelta(minutes=5))["status"] == "running"
    assert len(emitted) == 2  # 还在跑：不重复触发
    late = issue_drafts.scan_state(data, now=T0 + timedelta(minutes=16))
    assert late["status"] == "failed" and "超时" in late["error"]
    assert issue_drafts.start_scan(data=data, emit=emit, at=T0 + timedelta(minutes=16))["status"] == "running"
    assert len(emitted) == 4  # 超时了的可以再点
    half = issue_drafts.scan_done(7, 2, source="wechat", data=data, at=T0 + timedelta(minutes=18))
    assert (half["status"], half["found"], half["saved"]) == ("running", 7, 2)  # 录音还没交回
    done = issue_drafts.scan_done(3, 1, source="recordings", skipped=1, data=data, at=T0 + timedelta(minutes=19))
    assert (done["status"], done["found"], done["saved"], done["skipped"], done["finished_at"]) == ("done", 10, 3, 1, "2026-11-20T10:19:00+08:00")
    partial = issue_drafts.scan_done(0, 0, "微信库没读到", source="wechat", data=data)
    assert partial["status"] == "done" and partial["error"] == "" and "微信没跑成：微信库没读到" in partial["refresh_notes"]
    issue_drafts.scan_done(0, 0, "转写失败", source="recordings", data=data)
    assert issue_drafts.listing(PROJECTS, data)["scan"]["status"] == "failed"  # 全部来源都没跑成才算失败
    assert issue_drafts.listing(PROJECTS, data)["scan"]["error"] == "转写失败"


def test_scan_records_why_the_trigger_failed(data, monkeypatch, tmp_path):
    def broken(source, event_id):
        raise RuntimeError("中台没在跑")
    state = issue_drafts.start_scan(data=data, emit=broken, at=T0)
    assert state["status"] == "failed" and state["error"] == "没触发成：中台没在跑"
    # 默认的触发：中台没有规则时不发事件，说先 install；有规则才发本机事件
    assert "先运行 python3 -m personal.agent_watch install" in issue_drafts.start_scan(data=data, at=T0)["error"]
    path = realistic_rules(tmp_path / "hub" / "x")
    agent_watch.ensure_rules(path, workbench_data=data)  # 数据目录装了微信插件：录音和微信两条规则都加
    calls = []
    monkeypatch.setattr(agent_watch, "run", lambda args, timeout=120: calls.append(args) or "ok\n")
    assert issue_drafts.start_scan(data=data, at=T0)["status"] == "running"
    assert calls == [["agent-autoloop", "emit", "recordings.issues.scan", "--id", "scan:recordings:20261120T100000", "--subject", "recording-issues"],
                     ["agent-autoloop", "emit", "wechat.issues.scan", "--id", "scan:wechat:20261120T100000", "--subject", "wechat-issues"]]


def test_a_source_without_its_rule_is_skipped_with_a_note(data, tmp_path, monkeypatch):
    path = realistic_rules(tmp_path / "hub" / "x")
    agent_watch.ensure_rules(path, workbench_data=data)
    found = json.loads(path.read_text(encoding="utf-8"))
    found["rules"] = [r for r in found["rules"] if r["name"] != "微信拟 issue"]
    path.write_text(json.dumps(found, ensure_ascii=False), encoding="utf-8")
    calls = []
    monkeypatch.setattr(agent_watch, "run", lambda args, timeout=120: calls.append(args[2]) or "ok\n")
    state = issue_drafts.start_scan(data=data, at=T0)
    assert state["status"] == "running" and calls == ["recordings.issues.scan"]
    assert state["refresh_notes"] == ["微信没读：中台还没有「微信拟 issue」规则，先运行 python3 -m personal.agent_watch install"]


def test_install_adds_the_recording_rule_once(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_ROOT", "/work/assistant")
    path = realistic_rules(tmp_path / "hub" / "x")
    assert "录音拟 issue" in agent_watch.source_rule_missing("录音拟 issue", "recordings.issues.scan", path)
    note = agent_watch.ensure_rules(path, workbench_data=tmp_path / "no-plugins")
    assert "加了 Agent recording-issues" in note and "新增规则「录音拟 issue」" in note and "微信" not in note  # 没装微信插件不加它
    found = json.loads(path.read_text(encoding="utf-8"))
    assert found["agents"]["recording-issues"] == {"name": "录音拟 issue", "worker": "mini", "use": "command",
                                                   "command": ["python3", "/work/assistant/scripts/recording_issues.py", "--no-refresh"]}
    assert [r for r in found["rules"] if r["name"] == "录音拟 issue"] == [
        {"name": "录音拟 issue", "when": {"source": "local", "types": ["recordings.issues.scan"]}, "to": "recording-issues",
         "cwd": "/work/assistant", "tell": "never", "parallel": 1}]
    assert agent_watch.source_rule_missing("录音拟 issue", "recordings.issues.scan", path) == ""
    found["agents"]["recording-issues"]["command"] = ["python3", "/elsewhere.py"]  # 本人改过的照留
    agent_watch.write_json(path, found)
    assert agent_watch.ensure_rules(path, workbench_data=tmp_path / "no-plugins") == ""
    assert json.loads(path.read_text(encoding="utf-8"))["agents"]["recording-issues"]["command"] == ["python3", "/elsewhere.py"]


def test_routes_are_guarded_and_wired(tmp_path, monkeypatch):
    from personal import __main__ as app
    posts, scans = [], []
    monkeypatch.setattr(issue_drafts, "gh_create", lambda args, body: posts.append(args) or "https://github.com/me/tool/issues/9\n")
    monkeypatch.setattr(issue_drafts, "emit_scan", lambda source, event_id: scans.append(event_id))
    monkeypatch.setattr(agent_watch, "source_rule_missing", lambda rule, event, path=None: "")
    views = []
    monkeypatch.setattr(issue_drafts, "repo_exists", lambda full: views.append(full) or True)
    with instance(tmp_path, monkeypatch) as request:
        monkeypatch.setattr(app, "project_config", lambda: PROJECTS)
        draft = issue_drafts.save(payload(), PROJECTS, data=tmp_path / "instance")["saved"][0]
        other = issue_drafts.save(payload(title="不做的", dedupe_key="no"), PROJECTS, data=tmp_path / "instance")["saved"][0]
        got = request("/api/issue-drafts")
        assert {d["id"] for d in got["drafts"]} == {draft["id"], other["id"]} and got["scan"]["status"] == "idle"
        for path in ("/api/issue-drafts/create", "/api/issue-drafts/reject", "/api/issue-drafts/scan"):
            for bad in ({"Origin": "https://evil.example"}, {"X-Paihuo-Request": "0"}):
                with pytest.raises(HTTPError) as denied:
                    request(path, {"id": draft["id"]}, bad)
                assert denied.value.code == 403
        assert posts == [] and scans == []
        with pytest.raises(HTTPError) as wrong:
            request("/api/issue-drafts/create", {"id": draft["id"], "repo": "me/old"})
        assert wrong.value.code == 400
        made = request("/api/issue-drafts/create", {"id": draft["id"], "title": "改过", "body": "正文", "repo": "me/tool"})
        assert made == {"ok": True, "url": "https://github.com/me/tool/issues/9", "repo": "me/tool", "repo_created": False}
        fresh = issue_drafts.save(payload(repo="", title="另起一个", new_repo={"name": "side-tool", "description": "小工具"}), PROJECTS,
                                  data=tmp_path / "instance")["saved"][0]
        shown = next(d for d in request("/api/issue-drafts")["drafts"] if d["id"] == fresh["id"])
        assert shown["new_repo"] == {"name": "side-tool", "description": "小工具"}
        taken = request("/api/issue-drafts/create", {"id": fresh["id"], "new_repo": {"name": "taken", "description": ""}})
        assert taken["ok"] is False and "已经存在" in taken["error"] and views == ["luochen211/taken"]  # 接口把 new_repo 传到了
        with pytest.raises(HTTPError) as both:
            request("/api/issue-drafts/create", {"id": fresh["id"], "repo": "me/tool", "new_repo": {"name": "x"}})
        assert both.value.code == 400
        assert request("/api/issue-drafts/reject", {"id": fresh["id"]}) == {"ok": True}
        assert posts[0][:7] == ["gh", "issue", "create", "--repo", "me/tool", "--title", "改过"]
        assert request("/api/issue-drafts/reject", {"id": other["id"]}) == {"ok": True}
        assert request("/api/issue-drafts/scan", {})["status"] == "running" and len(scans) == 1
        assert request("/api/issue-drafts")["drafts"] == []
        held = issue_drafts.save(payload(title="只是讨论的方向", dedupe_key="held", skipped=True, skip_reason="还在讨论"), PROJECTS,
                                 data=tmp_path / "instance")["saved"][0]
        with pytest.raises(HTTPError) as denied:
            request("/api/issue-drafts/promote", {"id": held["id"]}, {"Origin": "https://evil.example"})
        assert denied.value.code == 403
        assert [d["id"] for d in request("/api/issue-drafts")["skipped"]] == [held["id"]]
        assert request("/api/issue-drafts/promote", {"id": held["id"]}) == {"ok": True}
        got = request("/api/issue-drafts")
        assert [d["id"] for d in got["drafts"]] == [held["id"]] and got["skipped"] == []


# ---- 新建仓库 ----

NEW = {"name": "side-tool", "description": "给客户方用的小工具"}


def fresh_draft(data, **extra):
    return issue_drafts.save(payload(repo="", new_repo=NEW, **extra), PROJECTS, data=data)["saved"][0]


def steps(exists=False, make_fail=None, register_fail=None):
    """假的 gh：记下每一步。"""
    log = []

    def view(full):
        log.append(("view", full))
        if isinstance(exists, Exception):
            raise exists
        return exists

    def make(args):
        log.append(("make", args))
        if make_fail:
            raise make_fail

    def register(full, name, projects, *, data=None):
        log.append(("register", full))
        if register_fail:
            raise register_fail
        return issue_drafts.register_project(full, name, projects, data=data)
    return log, {"view": view, "make": make, "register": register}


def test_save_takes_a_new_repo_proposal(data):
    draft = fresh_draft(data)
    assert (draft["repo"], draft["project_id"], draft["new_repo"]) == ("", "", NEW)
    assert issue_drafts.listing(PROJECTS, data)["drafts"][0]["new_repo"] == NEW and issue_drafts.listing(PROJECTS, data)["owner"] == "luochen211"
    # 这个会话登记过项目：没提议新建的照旧对应，提议新建的不对应
    needs.map_chat("演示群", "tool", PROJECTS, data=data)
    assert issue_drafts.save(payload(repo="", title="普通的"), PROJECTS, data=data)["saved"][0]["repo"] == "me/tool"
    assert issue_drafts.save(payload(repo="", title="另一个新的", new_repo=NEW), PROJECTS, data=data)["saved"][0]["repo"] == ""


@pytest.mark.parametrize("change, why", [
    ({"new_repo": {"name": ".hidden"}}, "不以 . 开头"),
    ({"new_repo": {"name": "有 空格"}}, "只能用字母"),
    ({"new_repo": {"name": "a/b"}}, "只能用字母"),
    ({"new_repo": {"name": "x" * 101}}, "只能用字母"),
    ({"new_repo": {"name": "tool.git"}}, ".git"),
    ({"new_repo": {"name": "ok", "description": "长" * 201}}, "说明太长"),
    ({"new_repo": {"name": "ok", "description": "Generated with Claude Code"}}, "AI 工具"),
    ({"new_repo": {"name": "ok", "description": SAID}}, "聊天原话"),
    ({"new_repo": "side-tool"}, "new_repo 要写成"),
    ({"new_repo": NEW, "repo": "me/tool"}, "二选一"),
])
def test_bad_new_repo_is_refused(data, change, why):
    result = issue_drafts.save(payload(**{"repo": "", **change}), PROJECTS, data=data)
    assert result["saved"] == [] and why in result["errors"][0]["error"]


def test_new_repo_is_created_private_registered_then_the_issue(data):
    draft = fresh_draft(data)
    log, fake = steps()
    post = poster("https://github.com/luochen211/side-tool/issues/1\n")
    result = issue_drafts.create(draft["id"], PROJECTS, data=data, post=post, at=T0, **fake)
    assert result == {"ok": True, "url": "https://github.com/luochen211/side-tool/issues/1", "repo": "luochen211/side-tool", "repo_created": True}
    assert [step[0] for step in log] == ["view", "make", "register"]
    assert log[1][1] == ["gh", "repo", "create", "luochen211/side-tool", "--private", "--description", "给客户方用的小工具"]
    assert "--public" not in log[1][1] and "--internal" not in log[1][1]
    assert post.calls[0][0][:5] == ["gh", "issue", "create", "--repo", "luochen211/side-tool"]
    assert SAID not in json.dumps(log, ensure_ascii=False) + json.dumps(post.calls, ensure_ascii=False)
    # 登记进数据目录的 projects.local.toml，马上就是能开 issue 的项目
    from personal import projects_config
    entry = projects_config.overlay(data / "projects.local.toml")["projects"]
    assert entry == [{"id": "sidetool", "name": "side-tool", "group": "其他", "repo_path": "", "repository": "luochen211/side-tool",
                      "github": "luochen211/side-tool", "source_status": "GitHub Issues"}]
    assert {"id": "sidetool", "name": "side-tool", "repo": "luochen211/side-tool"} in issue_drafts.eligible(PROJECTS + entry)
    past = issue_drafts.load(data)["history"][0]
    assert (past["status"], past["repo"], past["repo_created"]) == ("created", "luochen211/side-tool", True)
    assert issue_drafts.load(data)["drafts"] == {}
    # 没登记过的会话记到新项目上
    assert needs.project_of("abc1234567", data=data) == "sidetool"


def test_repo_create_command_has_no_public_option():
    assert issue_drafts.repo_command("luochen211/x", "") == ["gh", "repo", "create", "luochen211/x", "--private"]
    assert github.writes(issue_drafts.repo_command("luochen211/x", "d"))  # 写操作：瞬断不自动重试


def test_existing_registered_repo_is_used_and_unregistered_one_stops(data):
    projects = PROJECTS + [{"id": "side", "name": "已有", "github": "luochen211/Side-Tool"}]
    draft = fresh_draft(data)
    log, fake = steps(exists=True)
    post = poster("https://github.com/luochen211/Side-Tool/issues/4\n")
    result = issue_drafts.create(draft["id"], projects, data=data, post=post, **fake)
    assert result["ok"] is True and result["repo"] == "luochen211/Side-Tool" and result["repo_created"] is False
    assert [step[0] for step in log] == ["view"] and not (data / "projects.local.toml").exists()
    assert "repo_created" not in issue_drafts.load(data)["history"][0]

    other = fresh_draft(data, title="第二件")
    log, fake = steps(exists=True)
    post = poster()
    stopped = issue_drafts.create(other["id"], PROJECTS, data=data, post=post, **fake)
    assert stopped["ok"] is False and "已经存在，但没登记" in stopped["error"] and "没有建仓库" in stopped["error"]
    assert [step[0] for step in log] == ["view"] and post.calls == []
    kept = issue_drafts.load(data)["drafts"][other["id"]]
    assert kept["status"] == "failed" and kept["new_repo"] == NEW and kept["repo"] == ""


def test_lookup_and_create_failures_build_nothing(data):
    draft = fresh_draft(data)
    log, fake = steps(exists=RuntimeError("connection reset"))
    assert "没查成" in issue_drafts.create(draft["id"], PROJECTS, data=data, post=poster(), **fake)["error"]
    assert [step[0] for step in log] == ["view"]
    log, fake = steps(make_fail=subprocess.TimeoutExpired("gh", 60))
    slow = issue_drafts.create(draft["id"], PROJECTS, data=data, post=poster(), **fake)
    assert "不确定 luochen211/side-tool 有没有建成" in slow["error"] and "先去 GitHub 看" in slow["error"]
    assert [step[0] for step in log] == ["view", "make"]
    log, fake = steps(make_fail=RuntimeError("HTTP 403: Resource not accessible"))
    post = poster()
    denied = issue_drafts.create(draft["id"], PROJECTS, data=data, post=post, **fake)
    assert "没建成仓库" in denied["error"] and post.calls == []
    kept = issue_drafts.load(data)["drafts"][draft["id"]]
    assert kept["new_repo"] == NEW and "repo_created" not in kept and not (data / "projects.local.toml").exists()


def test_issue_failure_after_the_repo_is_built_never_rebuilds_it(data):
    draft = fresh_draft(data)
    log, fake = steps()
    failed = issue_drafts.create(draft["id"], PROJECTS, data=data, post=poster(fail=RuntimeError("HTTP 502")), **fake)
    assert failed["ok"] is False and "私有仓库 luochen211/side-tool 已经建好" in failed["error"] and "没创建成 issue" in failed["error"]
    kept = issue_drafts.load(data)["drafts"][draft["id"]]
    assert (kept["status"], kept["repo"], kept["project_id"], kept["repo_created"]) == ("failed", "luochen211/side-tool", "sidetool", "luochen211/side-tool")
    assert "new_repo" not in kept
    from personal import projects_config
    now = PROJECTS + projects_config.overlay(data / "projects.local.toml")["projects"]  # 页面再点时登记表已经读到新项目
    log2, fake2 = steps()
    post = poster("https://github.com/luochen211/side-tool/issues/1\n")
    again = issue_drafts.create(draft["id"], now, data=data, post=post, **fake2)
    assert again["ok"] is True and again["repo_created"] is True and log2 == []  # 只开 issue：不查、不建、不再登记
    assert issue_drafts.load(data)["history"][0]["repo_created"] is True


def test_registration_failure_says_the_repo_exists_and_retries_without_rebuilding(data):
    draft = fresh_draft(data)
    log, fake = steps(register_fail=OSError("磁盘满了"))
    post = poster()
    result = issue_drafts.create(draft["id"], PROJECTS, data=data, post=post, **fake)
    assert result["ok"] is False and "私有仓库 luochen211/side-tool 已经建好，但没登记进工作台" in result["error"] and post.calls == []
    kept = issue_drafts.load(data)["drafts"][draft["id"]]
    assert kept["repo_created"] == "luochen211/side-tool" and kept["new_repo"] == NEW and kept["error"] == result["error"]
    log2, fake2 = steps()
    post = poster("https://github.com/luochen211/side-tool/issues/1\n")
    assert issue_drafts.create(draft["id"], PROJECTS, data=data, post=post, **fake2)["ok"] is True
    assert [step[0] for step in log2] == ["register"]  # 不再查、不再建


def test_two_clicks_at_once_build_one_repo(data):
    draft = fresh_draft(data)
    log, fake = steps()
    inner = fake["make"]

    def make(args):
        with pytest.raises(ValueError, match="正在创建"):
            issue_drafts.create(draft["id"], PROJECTS, data=data, post=poster(), **fake)
        inner(args)
    fake["make"] = make
    assert issue_drafts.create(draft["id"], PROJECTS, data=data, post=poster(), **fake)["ok"] is True
    assert [step[0] for step in log].count("make") == 1


def test_page_can_switch_between_new_and_existing_repo(data):
    draft = fresh_draft(data)
    log, fake = steps()
    post = poster()
    with pytest.raises(ValueError, match="二选一"):
        issue_drafts.create(draft["id"], PROJECTS, repo="me/tool", new_repo=NEW, data=data, post=post, **fake)
    with pytest.raises(ValueError, match="只能用字母"):
        issue_drafts.create(draft["id"], PROJECTS, new_repo={"name": "不行 的"}, data=data, post=post, **fake)
    assert issue_drafts.create(draft["id"], PROJECTS, repo="me/tool", data=data, post=post, **fake)["repo"] == "me/tool"
    assert log == [] and not (data / "projects.local.toml").exists()  # 改选现有仓库：丢掉 new_repo，不建
    assert "repo_created" not in issue_drafts.load(data)["history"][0]
    plain = issue_drafts.save(payload(title="原本开在现有仓库"), PROJECTS, data=data)["saved"][0]
    log, fake = steps()
    made = issue_drafts.create(plain["id"], PROJECTS, new_repo={"name": "Other_1", "description": ""}, data=data,
                               post=poster("https://github.com/luochen211/Other_1/issues/1\n"), **fake)
    assert made["repo"] == "luochen211/Other_1" and made["repo_created"] is True
    assert log[1][1] == ["gh", "repo", "create", "luochen211/Other_1", "--private"]


def test_a_registered_chat_keeps_its_project_when_a_draft_goes_elsewhere(data):
    needs.map_chat("演示群", "tool", PROJECTS, data=data)
    draft = issue_drafts.save(payload(repo="", new_repo=NEW, source={"chat_key": "演示群", "chat": "演示群", "kind": "wechat"}), PROJECTS, data=data)["saved"][0]
    log, fake = steps()
    assert issue_drafts.create(draft["id"], PROJECTS, data=data, post=poster(), **fake)["ok"] is True
    assert needs.project_of("演示群", "演示群", data=data) == "tool"


def test_register_appends_to_an_existing_overlay_and_avoids_id_clashes(data):
    data.mkdir(exist_ok=True)
    (data / "projects.local.toml").write_text('[[projects]]\nid = "tool2"\nname = "私有"\ngithub = "me/private"', encoding="utf-8")
    one = issue_drafts.register_project("luochen211/Tool", "Tool", PROJECTS, data=data)
    assert one["id"] == "tool3"
    assert issue_drafts.register_project("luochen211/tool", "Tool", PROJECTS, data=data) == one  # 登记过的不再写
    from personal import __main__ as app, projects_config
    found = projects_config.overlay(data / "projects.local.toml")["projects"]
    assert [p["id"] for p in found] == ["tool2", "tool3"] and app.validate(found)


def test_repo_exists_reads_not_found_and_passes_other_errors_on():
    def missing(args, kind, **kwargs):
        assert args == ["gh", "repo", "view", "luochen211/x", "--json", "nameWithOwner"]
        raise RuntimeError("GraphQL: Could not resolve to a Repository with the name 'luochen211/x'.")
    assert issue_drafts.repo_exists("luochen211/x", run=missing) is False
    assert issue_drafts.repo_exists("luochen211/x", run=lambda args, kind, **kwargs: "{}") is True
    with pytest.raises(RuntimeError):
        issue_drafts.repo_exists("luochen211/x", run=lambda args, kind, **kwargs: (_ for _ in ()).throw(RuntimeError("connection reset")))


# ---- 扫描前由工作台刷新微信明文库 ----

def test_scan_refreshes_before_the_event_and_keeps_the_notes(data):
    order = []
    state = issue_drafts.start_scan(data=data, at=T0, refresh=lambda d: order.append("refresh") or ["wechat2 的库没刷新成，读的是上一次刷新时的记录"],
                                    emit=lambda source, event_id: order.append("emit"))
    assert order == ["refresh", "emit", "emit"] and state["status"] == "running"
    assert state["refresh_notes"] == ["wechat2 的库没刷新成，读的是上一次刷新时的记录"]  # 刷新不成不拦这一轮
    issue_drafts.scan_done(3, 1, source="wechat", data=data)
    done = issue_drafts.scan_done(0, 0, source="recordings", data=data)
    assert done["status"] == "done" and done["refresh_notes"] == state["refresh_notes"]  # scan-done 不清掉它
    assert issue_drafts.start_scan(data=data, at=T0 + timedelta(minutes=1), refresh=lambda d: [], emit=lambda s, e: None)["refresh_notes"] == []


def test_scan_returns_running_before_the_background_step_and_records_its_errors(data):
    jobs = []
    state = issue_drafts.start_scan(data=data, at=T0, refresh=lambda d: (_ for _ in ()).throw(KeyError("私密路径")), emit=lambda s, e: None,
                                    spawn=jobs.append)
    assert state["status"] == "running" and len(jobs) == 1  # 接口不等刷新
    jobs[0]()
    failed = issue_drafts.scan_state(data)
    assert failed["status"] == "failed" and failed["error"] == "没触发成：刷新微信库时出错（KeyError）" and "私密路径" not in failed["error"]


def test_a_late_background_step_does_not_overwrite_a_newer_round(data):
    jobs, emitted = [], []
    emit = lambda source, event_id: emitted.append(event_id)
    issue_drafts.start_scan(data=data, at=T0, refresh=lambda d: [], emit=emit, spawn=jobs.append)
    issue_drafts.start_scan(data=data, at=T0 + timedelta(minutes=16), refresh=lambda d: [], emit=emit, spawn=jobs.append)
    jobs[0]()  # 超时那一轮迟到的后台步骤
    assert emitted == [] and issue_drafts.scan_state(data, now=T0 + timedelta(minutes=17))["status"] == "running"
    jobs[1]()
    assert emitted == ["scan:recordings:20261120T101600", "scan:wechat:20261120T101600"]


def test_refresh_all_refreshes_only_accounts_with_registered_chats(data, tmp_path, monkeypatch):
    from personal.plugins.wechat import inbox as wechat
    accounts = {"accounts": {"wechat2": {"chats": ["二号群"], "decrypted_dir": "/x/w2", "home": "/x/home2"},
                             "wechat3": {"chats": ["没登记的群"], "decrypted_dir": "/x/w3", "home": "/x/home3"},
                             "wechat4": {"chats": ["四号群"], "decrypted_dir": "/x/w4"}}}
    (tmp_path / "accounts.json").write_text(json.dumps(accounts, ensure_ascii=False), encoding="utf-8")
    projects = PROJECTS
    for chat in ("二号群", "四号群"):
        needs.map_chat(chat, "tool", projects, data=data)
    cfg = {"problem": "", "python": "/venv/python", "skill_dir": "/skill"}
    monkeypatch.setattr(wechat, "config", lambda base: cfg)
    monkeypatch.setattr(wechat, "refresh", lambda base, cfg=None: (_ for _ in ()).throw(AssertionError("默认号没有登记的会话，不该刷新")))
    ran = []
    notes = needs.refresh_all(data, decrypt=lambda home, python, skill: ran.append((home, python, skill)) or False)
    assert ran == [("/x/home2", "/venv/python", "/skill")]
    assert notes == ["wechat2 的库没刷新成，读的是上一次刷新时的记录", "wechat4 的库没刷新：accounts.json 里没写 home"]
    assert needs.refresh_all(data, decrypt=lambda home, python, skill: True) == ["wechat4 的库没刷新：accounts.json 里没写 home"]


def test_refresh_all_waits_for_the_default_account_and_reports_it(data, monkeypatch):
    from personal.plugins.wechat import inbox as wechat
    needs.map_chat("一号群", "tool", PROJECTS, data=data)
    assert needs.refresh_all(data) == ["默认微信号的库没刷新：工作台没启用微信（没有 wechat.json）"]
    cfg, status, calls = {"problem": "", "python": "/venv/python", "skill_dir": "/skill"}, {"decrypt": {"ok": True}}, []
    monkeypatch.setattr(wechat, "config", lambda base: cfg)
    monkeypatch.setattr(wechat, "refresh", lambda base, cfg=None: calls.append(base) or True)
    monkeypatch.setattr(wechat, "load_status", lambda base: status)
    assert needs.refresh_all(data) == [] and calls == [data]
    status["decrypt"]["ok"] = False
    assert needs.refresh_all(data) == ["默认微信号的库没刷新成，读的是上一次刷新时的记录"]
    cfg["problem"] = "找不到 Python：/venv/python"
    assert needs.refresh_all(data) == ["默认微信号的库没刷新：找不到 Python：/venv/python"]


def test_decrypt_account_runs_under_the_accounts_own_home(monkeypatch):
    seen = {}

    def run(args, **kwargs):
        seen.update(args=args, home=kwargs["env"]["HOME"], timeout=kwargs["timeout"])
        return subprocess.CompletedProcess(args, 0, "Done: 14 decrypted", "")
    monkeypatch.setattr(subprocess, "run", run)
    assert needs.decrypt_account("/x/home2", "/venv/python", "/skill") is True
    assert seen == {"args": ["/venv/python", "/skill/scripts/decrypt_all_dbs.py", "--mode", "incremental"], "home": "/x/home2", "timeout": 600}
    monkeypatch.setattr(subprocess, "run", lambda args, **k: subprocess.CompletedProcess(args, 0, "", "source unreadable [Errno 1] Operation not permitted"))
    assert needs.decrypt_account("/x/home2", "/venv/python", "/skill") is False
    monkeypatch.setattr(subprocess, "run", lambda args, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired(args, 600)))
    assert needs.decrypt_account("/x/home2", "/venv/python", "/skill") is False


# ---- 做完后 @ 对方 ----

def two_speakers(**extra):
    return payload(excerpt=[{"time": "2026-11-20T09:30:00+08:00", "from": "甲", "text": SAID},
                            {"time": "2026-11-20T09:31:00+08:00", "from": "乙", "text": "好的收到"}], **extra)


def closed(reason="COMPLETED", prs=(), comments=()):
    return {"state": "CLOSED", "stateReason": reason, "comments": list(comments),
            "closedByPullRequestsReferences": [{"number": n, "url": f"https://github.com/me/tool/pull/{n}"} for n in prs]}


def test_create_records_who_to_mention_and_remembers_the_accounts(data):
    draft = issue_drafts.save(two_speakers(), PROJECTS, data=data)["saved"][0]
    result = issue_drafts.create(draft["id"], PROJECTS, mention={"甲": "@alice", "乙": ""}, data=data, post=poster())
    assert result["mention"] == ["alice"]
    assert issue_drafts.load(data)["history"][0]["mention"] == ["alice"]
    assert issue_drafts.people(data) == {"甲": "alice"} and mode(data / "wechat-people.json") == 0o600
    shown = issue_drafts.listing(PROJECTS, data)
    assert shown["mentions"] == [{"title": "列表页加导出按钮", "url": "https://github.com/me/tool/issues/12", "logins": ["alice"], "error": ""}]
    # 下一条同一个人：页面不带 mention 也照记下的对应；列表里带出这个对应
    again = issue_drafts.save(two_speakers(title="另一件事", dedupe_key="k2"), PROJECTS, data=data)["saved"][0]
    assert issue_drafts.listing(PROJECTS, data)["people"] == {"甲": "alice"}
    issue_drafts.create(again["id"], PROJECTS, data=data, post=poster("https://github.com/me/tool/issues/13\n"))
    assert issue_drafts.load(data)["history"][0]["mention"] == ["alice"]
    # 清空再创建：不 @，也不再记
    third = issue_drafts.save(two_speakers(title="第三件事", dedupe_key="k3"), PROJECTS, data=data)["saved"][0]
    issue_drafts.create(third["id"], PROJECTS, mention={"甲": ""}, data=data, post=poster("https://github.com/me/tool/issues/14\n"))
    assert "mention" not in issue_drafts.load(data)["history"][0] and issue_drafts.people(data) == {}


@pytest.mark.parametrize("mention, why", [
    ({"甲": "张三"}, "不是 GitHub 账号名"),
    ({"甲": "-bad"}, "不是 GitHub 账号名"),
    ({"丙": "alice"}, "不是这条草稿里的发言人"),
    (["alice"], "mention 要写成"),
])
def test_bad_mention_creates_nothing(data, mention, why):
    draft = issue_drafts.save(two_speakers(), PROJECTS, data=data)["saved"][0]
    post = poster()
    with pytest.raises(ValueError, match=why):
        issue_drafts.create(draft["id"], PROJECTS, mention=mention, data=data, post=post)
    assert post.calls == [] and draft["id"] in issue_drafts.load(data)["drafts"]


def test_own_account_is_never_mentioned(data):
    draft = issue_drafts.save(two_speakers(), PROJECTS, data=data)["saved"][0]
    result = issue_drafts.create(draft["id"], PROJECTS, mention={"甲": issue_drafts.OWNER.upper(), "乙": "bob"}, data=data, post=poster())
    assert result["mention"] == ["bob"]


def created_with(data, mention={"甲": "alice", "乙": "bob"}):
    draft = issue_drafts.save(two_speakers(), PROJECTS, data=data)["saved"][0]
    issue_drafts.create(draft["id"], PROJECTS, mention=mention, data=data, post=poster())


def test_round_comments_once_when_the_issue_is_closed_as_completed(data):
    created_with(data)
    post, seen = poster(""), []

    def view(repo, number):
        seen.append((repo, number))
        return state[0]
    state = [{"state": "OPEN", "stateReason": "", "comments": [], "closedByPullRequestsReferences": []}]
    assert issue_drafts.mention_round(data=data, view=view, post=post) == [] and post.calls == []
    state[0] = closed(prs=[15])
    assert issue_drafts.mention_round(data=data, view=view, post=post, at=T0)[0]["done"] == "sent"
    args, body = post.calls[0]
    assert args == ["gh", "issue", "comment", "12", "--repo", "me/tool", "--body-file", "-"]
    assert body == "@alice @bob 这条已做完，改动见 #15，麻烦看一下。"
    past = issue_drafts.load(data)["history"][0]
    assert past["mention_done"] == "sent" and past["mention_at"] == "2026-11-20T10:00:00+08:00"
    assert issue_drafts.listing(PROJECTS, data)["mentions"] == []
    assert issue_drafts.mention_round(data=data, view=view, post=post) == [] and len(post.calls) == 1 and len(seen) == 2


def test_round_skips_not_planned_and_issues_already_mentioned(data):
    created_with(data)
    post = poster("")
    assert issue_drafts.mention_round(data=data, view=lambda r, n: closed("NOT_PLANNED"), post=post)[0]["done"] == "skipped"
    assert post.calls == []
    with issue_drafts.book(data) as found:
        del found["history"][0]["mention_done"]
    mine = {"author": {"login": issue_drafts.OWNER}, "body": "@Alice @bob 做好了"}
    other = {"author": {"login": "someone"}, "body": "@alice @bob"}
    assert issue_drafts.mention_round(data=data, view=lambda r, n: closed(comments=[other]), post=post)[0]["done"] == "sent"
    with issue_drafts.book(data) as found:
        del found["history"][0]["mention_done"]
    assert issue_drafts.mention_round(data=data, view=lambda r, n: closed(comments=[mine]), post=post)[0]["done"] == "already"
    assert len(post.calls) == 1 and post.calls[0][1] == "@alice @bob 这条已做完，麻烦看一下。"


def test_round_keeps_the_reason_when_the_comment_fails_and_retries(data):
    created_with(data)
    bad = poster(fail=RuntimeError("HTTP 403: Resource not accessible"))
    assert issue_drafts.mention_round(data=data, view=lambda r, n: closed(), post=bad)[0]["error"]
    waiting = issue_drafts.listing(PROJECTS, data)["mentions"]
    assert waiting[0]["error"] and "mention_done" not in issue_drafts.load(data)["history"][0]
    late = poster(fail=subprocess.TimeoutExpired("gh", 60))
    assert "下一轮" in issue_drafts.mention_round(data=data, view=lambda r, n: closed(), post=late)[0]["error"]
    good = poster("")
    assert issue_drafts.mention_round(data=data, view=lambda r, n: closed(), post=good)[0]["done"] == "sent"
    past = issue_drafts.load(data)["history"][0]
    assert past["mention_done"] == "sent" and "mention_error" not in past


def test_round_without_anyone_to_mention_asks_github_nothing(data):
    draft = issue_drafts.save(payload(), PROJECTS, data=data)["saved"][0]
    issue_drafts.create(draft["id"], PROJECTS, data=data, post=poster())
    assert issue_drafts.mention_round(data=data) == []  # github.run 在测试里一调用就报错


def test_a_recording_draft_never_joins_the_chat_whitelist(data):
    source = {"chat_key": "getnote:n1", "chat": "得到 · 周会", "kind": "getnote"}
    draft = issue_drafts.save(payload(repo="", source=source), PROJECTS, data=data)["saved"][0]
    assert draft["repo"] == "" and draft["source"]["kind"] == "getnote"
    assert issue_drafts.create(draft["id"], PROJECTS, repo="me/tool", data=data, post=poster())["ok"]
    assert needs.mappings(data) == []


# ---- 上传录音 ----

def test_upload_saves_the_audio_locally_and_starts_a_round(data, tmp_path, monkeypatch):
    import io
    import os
    folder = tmp_path / "drop"
    (tmp_path / "recordings.json").write_text(json.dumps({"audio_dir": str(folder)}), encoding="utf-8")
    monkeypatch.setenv("RECORDING_ISSUES_CONFIG", str(tmp_path / "recordings.json"))
    scans = []
    result = issue_drafts.save_audio("../../客户 电话.M4A", io.BytesIO(b"abc"), 3, data=data, at=T0,
                                     scan=lambda data=None: scans.append(1) or {"status": "running"})
    assert result == {"saved": "20261120-100000-客户 电话.m4a", "scan": {"status": "running"}, "queued": False}
    saved = folder / result["saved"]
    assert saved.read_bytes() == b"abc" and [p.name for p in folder.iterdir()] == [saved.name] and scans == [1]
    assert T0.timestamp() - saved.stat().st_mtime >= 60  # 抽取脚本不读一分钟内改过的文件
    for name, stream, length, why in (("笔记.txt", io.BytesIO(b"abc"), 3, "只收音频文件"), ("a.mp3", io.BytesIO(b""), 0, "MB 以内"),
                                      ("a.mp3", io.BytesIO(b"ab"), 3, "没传完"), ("a.mp3", io.BytesIO(b"x"), issue_drafts.MAX_AUDIO + 1, "MB 以内")):
        with pytest.raises(ValueError, match=why):
            issue_drafts.save_audio(name, stream, length, data=data, at=T0, scan=lambda data=None: scans.append(1))
    assert [p.name for p in folder.iterdir()] == [saved.name] and scans == [1]
    again = issue_drafts.save_audio("b.mp3", io.BytesIO(b"abc"), 3, data=data, scan=lambda data=None: {"status": "running"})  # 不给时间也能存
    assert (folder / again["saved"]).is_file()
    (tmp_path / "recordings.json").write_text(json.dumps({"audio_dir": ""}), encoding="utf-8")
    with pytest.raises(ValueError, match="关掉了"):
        issue_drafts.save_audio("a.mp3", io.BytesIO(b"abc"), 3, data=data)


# ---- 没拟的话题 ----

def test_skipped_topics_are_kept_apart_and_can_be_promoted_or_dropped(data, capsys):
    held = payload(title="新产品方向：个人助理", dedupe_key="held", skipped=True, skip_reason="  只是讨论，\n还没定要做  ")
    result = issue_drafts.save([payload(), held], PROJECTS, data=data, at=T0)
    topic = next(d for d in result["saved"] if d["dedupe_key"] == "held")
    assert topic["status"] == "skipped" and topic["skip_reason"] == "只是讨论， 还没定要做"
    shown = issue_drafts.listing(PROJECTS, data)
    assert len(shown["drafts"]) == 1 and shown["drafts"][0]["status"] == "pending"  # 不算待评审
    assert [d["id"] for d in shown["skipped"]] == [topic["id"]] and "chat_key" not in shown["skipped"][0]["source"]
    # 同一件事再交来：跳过，不管这次拟没拟
    again = issue_drafts.save(payload(title="新产品方向：个人助理", dedupe_key="held"), PROJECTS, data=data)
    assert again["skipped"] == [{"dedupe_key": "held", "why": "已在没拟的话题里"}]
    # 还没补成草稿的不能直接创建
    with pytest.raises(ValueError, match="没有这条待确认"):
        issue_drafts.create(topic["id"], PROJECTS, data=data, post=poster())
    assert issue_drafts.promote(topic["id"], data=data, at=T0 + timedelta(hours=1)) == {"ok": True}
    shown = issue_drafts.listing(PROJECTS, data)
    assert shown["skipped"] == [] and shown["drafts"][0]["id"] == topic["id"]  # 补成的排在最前
    assert "skip_reason" not in shown["drafts"][0] and shown["drafts"][0]["status"] == "pending"
    with pytest.raises(ValueError, match="没有这条没拟的话题"):
        issue_drafts.promote(topic["id"], data=data)
    # 「不要」和不创建一样：记进 history，同一件事不再交来
    other = issue_drafts.save(payload(title="另一个方向", dedupe_key="drop", skipped=True), PROJECTS, data=data)["saved"][0]
    assert other["skip_reason"] == "没有说明"
    assert issue_drafts.reject(other["id"], data=data) == {"ok": True}
    assert issue_drafts.save(payload(title="另一个方向", dedupe_key="drop", skipped=True), PROJECTS, data=data)["skipped"][0]["why"] == "你选过不创建"
    # 命令行：分开报条数；scan-done 带回没拟的条数
    assert issue_drafts.main(["scan-done", "--source", "wechat", "--found", "9", "--saved", "1", "--skipped", "2"], projects=PROJECTS, data=data) == 0
    assert json.loads(capsys.readouterr().out)["skipped"] == 2
    assert issue_drafts.scan_state(data)["skipped"] == 2


def test_skipped_topics_still_pass_the_checks(data, tmp_path, capsys):
    bad = issue_drafts.save(payload(title="方向", body=f"背景：{SAID}", skipped=True), PROJECTS, data=data)
    assert bad["saved"] == [] and "聊天原话" in bad["errors"][0]["error"]
    file = tmp_path / "in.json"
    file.write_text(json.dumps([payload(), payload(title="方向", dedupe_key="h", skipped=True)], ensure_ascii=False), encoding="utf-8")
    assert issue_drafts.main(["save", str(file)], projects=PROJECTS, data=data) == 0
    assert "已存 1 条 issue 草稿、1 条没拟的话题" in capsys.readouterr().out


def test_without_the_wechat_plugin_nothing_touches_wechat(data, capsys):
    """去掉微信插件：不刷新微信库、会话登记命令拒绝、开 issue 时不记会话；草稿和录音照常。"""
    (data / "plugins.json").write_text('{"wechat": {"installed": false}}', encoding="utf-8")
    assert needs.refresh_all(data, decrypt=lambda *a: pytest.fail("不该刷新微信库")) == []
    from personal import plugins
    for argv in (("map", "--chat", "演示群", "--project", "tool"), ("unmap", "--chat", "演示群"), ("mappings",)):
        assert plugins.main(["wechat", *argv], data=data) == 2 and "微信插件没装" in capsys.readouterr().err
    run = lambda *argv: issue_drafts.main(list(argv), projects=PROJECTS, data=data)
    assert run("plugins") == 0 and json.loads(capsys.readouterr().out) == {"wechat": False}
    draft = issue_drafts.save(payload(repo=""), PROJECTS, data=data)["saved"][0]
    issue_drafts.create(draft["id"], PROJECTS, repo="them/lib", data=data, post=poster("https://github.com/them/lib/issues/3\n"), at=T0)
    assert not (data / "wechat-projects.json").exists()
