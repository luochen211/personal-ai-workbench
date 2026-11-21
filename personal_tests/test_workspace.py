"""工作台网页：数据目录可换、旧任务路由已去掉、跨站写入被拒、登记表解析和近月范围。"""
import json
import threading
from contextlib import contextmanager
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
import pytest
from personal import __main__ as app


def test_data_dir_defaults_to_the_repo_and_follows_the_environment(tmp_path):
    import os, subprocess, sys
    code = "from personal import __main__ as app; print(app.DATA)"
    env = {k: v for k, v in os.environ.items() if k != "WORKBENCH_DATA"}
    root = app.ROOT
    assert subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, check=True).stdout.strip() == str((root / ".personal").resolve())
    out = subprocess.run([sys.executable, "-c", code], cwd=root, env={**env, "WORKBENCH_DATA": str(tmp_path / "x")}, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == str((tmp_path / "x").resolve())


def test_html_script_escape(tmp_path, monkeypatch):
    template = tmp_path / "index.html"
    template.write_text('<script type="application/json">__PAIHUO_DATA__</script>')
    monkeypatch.setattr(app, "TEMPLATE", template)
    rendered = app.render({"title": "</script><script>alert(1)</script>"})
    assert rendered.count("</script>") == 1
    assert "\\u003c" in rendered


@contextmanager
def instance(tmp_path, monkeypatch):
    from personal import summaries
    monkeypatch.setattr(summaries, "fetch_descriptions", lambda repos: {})
    monkeypatch.setattr(app, "DATA", tmp_path / "instance")
    monkeypatch.setattr(app, "project_config", lambda: [{"id": "paihuo", "name": "个人项目", "repo_path": str(tmp_path), "github": ""}])
    monkeypatch.setattr(app, "active_project_config", lambda: app.project_config())
    monkeypatch.setattr(app, "scoped_project_config", lambda: app.project_config())
    (tmp_path / "instance").mkdir(exist_ok=True)
    web = app.make_web(0)
    web_thread = threading.Thread(target=web.serve_forever, daemon=True); web_thread.start()
    opener = build_opener(ProxyHandler({}))
    url = f"http://127.0.0.1:{web.server_port}"
    def request(path, data=None, headers=None):
        extra = {"X-Paihuo-Request": "1", "Origin": url, "Content-Type": "application/json"}
        extra.update(headers or {})
        req = Request(url + path, data=json.dumps(data).encode() if data is not None else None, headers=extra)
        with opener.open(req) as response:
            return json.load(response)
    try:
        yield request
    finally:
        web.shutdown(); web.server_close(); web_thread.join()


def test_board_is_issue_only_and_old_task_routes_are_gone(tmp_path, monkeypatch):
    with instance(tmp_path, monkeypatch) as request:
        payload = request("/api/board")
        assert set(payload) == {"projects", "archived", "subgroups", "commit_window", "generated_at"}
        for path, data in (("/api/cards", {"title": "x", "goal": "y", "project_id": "paihuo"}), ("/api/sources/import", {})):
            with pytest.raises(HTTPError) as error:
                request(path, data)
            assert error.value.code == 404
        with pytest.raises(HTTPError) as error:
            request("/api/cards/1")
        assert error.value.code == 404


def test_write_origin_and_input_validation(tmp_path, monkeypatch):
    from personal import issues
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None: ({"waiting": {}}, ""))
    with instance(tmp_path, monkeypatch) as request:
        with pytest.raises(HTTPError) as error:
            request("/api/questions/5/answer", {"choice": 1}, {"Origin": "https://outside.invalid"})
        assert error.value.code == 403
        with pytest.raises(HTTPError) as error:  # 不属于当前项目 issue 的提问不能回答
            request("/api/questions/5/answer", {"choice": 1})
        assert error.value.code == 400
        with pytest.raises(HTTPError) as error:  # 决定包同样只认本人从本机页面提交
            request("/api/issues/o/r/1/decisions", {"answers": [{"id": 5, "choice": 1}]}, {"Origin": "https://outside.invalid"})
        assert error.value.code == 403
        with pytest.raises(HTTPError) as error:  # 不在当前项目的 issue：拒绝原因要说清，不是随便一个 400
            request("/api/issues/o/r/1/decisions", {"answers": [{"id": 5, "choice": 1}]})
        assert error.value.code == 400
        assert json.load(error.value)["error"] == "这条 issue 不在当前项目里"


def test_catalog_keeps_repository_when_issue_source_disabled(tmp_path, monkeypatch):
    with instance(tmp_path, monkeypatch) as request:
        monkeypatch.setattr(app, "project_config", lambda: [{"id": "paihuo", "name": "个人项目", "repo_path": str(tmp_path), "github": "", "repository": "owner/repo", "group": "个人工具", "source_status": "未开启 Issues"}])
        project = request("/api/board")["projects"][0]
        assert project["repository"] == "owner/repo"
        assert project["group"] == "个人工具"
        assert project["source_status"] == "未开启 Issues"
        assert project["issue_repository"] == ""


def test_subgroup_and_tags_parse_with_defaults(tmp_path, monkeypatch):
    config = tmp_path / "personal/projects.toml"
    config.parent.mkdir()
    config.write_text('''
[[projects]]
id = "content"
name = "内容"
group = "合作项目"
subgroup = "内容生产"

[[projects]]
id = "loose"
name = "没分类"
group = "合作项目"

[[projects]]
id = "mine"
name = "自己的"
group = "开源协作"
subgroup = "一个没见过的分类"
github = ""
repository = "Luochen211/tool"
tags = ["Skill", " ", "Skill"]

[[projects]]
id = "theirs"
name = "上游的"
group = "开源协作"
github = "someone/agent"
repository = "luochen211/agent"
''', encoding="utf-8")
    monkeypatch.setattr(app, "ROOT", tmp_path)
    projects = {p["id"]: p for p in app.project_config()}
    assert app.classify(projects["content"]) == {"subgroup": "内容生产", "tags": []}
    assert app.classify(projects["loose"]) == {"subgroup": "其他", "tags": []}
    assert app.classify(projects["mine"]) == {"subgroup": "一个没见过的分类", "tags": ["我的仓库", "Skill"]}
    assert app.classify(projects["theirs"]) == {"subgroup": "其他", "tags": ["上游贡献"]}
    assert app.subgroup_order(list(projects.values())) == {"合作项目": ["内容生产", "其他"], "开源协作": ["一个没见过的分类", "其他"]}


@pytest.mark.parametrize("field", ['subgroup = 3', 'tags = "我的仓库"', 'tags = ["a", 1]'])
def test_subgroup_and_tags_reject_wrong_types(tmp_path, monkeypatch, field):
    config = tmp_path / "personal/projects.toml"
    config.parent.mkdir()
    config.write_text(f'[[projects]]\nid = "bad"\nname = "坏"\n{field}\n', encoding="utf-8")
    monkeypatch.setattr(app, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="bad"):
        app.project_config()


def test_board_carries_subgroups_in_config_order(tmp_path, monkeypatch):
    with instance(tmp_path, monkeypatch) as request:
        monkeypatch.setattr(app, "project_config", lambda: [
            {"id": "paihuo", "name": "个人项目", "repo_path": str(tmp_path), "github": "dify/dify", "group": "开源协作", "subgroup": "Agent 平台", "tags": ["Python"]}])
        payload = request("/api/board")
        assert payload["projects"][0]["subgroup"] == "Agent 平台"
        assert payload["projects"][0]["tags"] == ["上游贡献", "Python"]
        assert payload["subgroups"] == {"开源协作": ["Agent 平台"]}


def test_inactive_projects_leave_the_board(tmp_path, monkeypatch):
    with instance(tmp_path, monkeypatch) as request:
        both = [{"id": "paihuo", "name": "个人项目", "repo_path": str(tmp_path), "github": ""},
                {"id": "inactive", "name": "旧项目", "repo_path": str(tmp_path), "github": ""}]
        monkeypatch.setattr(app, "project_config", lambda: both)
        monkeypatch.setattr(app, "active_project_config", lambda: both[:1])  # 近月没有本人活动的不算当前项目
        payload = request('/api/board')
        assert [(p['id'], p['enabled']) for p in payload['projects']] == [('paihuo', True)]


ARCHIVED_TOML = """
[[projects]]
id = "live"
name = "在做的"
group = "商业与交付"
github = "o/live"

[[projects]]
id = "done"
name = "结束了的"
group = "商业与交付"
repository = "o/done"
github = "o/done"
archived = true
archived_at = "2026-11-12"
"""


def archived_setup(tmp_path, monkeypatch):
    """登记表里一个在做的、一个归档的，两个近月都有本人提交。"""
    config = tmp_path / "personal/projects.toml"
    config.parent.mkdir(exist_ok=True)
    config.write_text(ARCHIVED_TOML, encoding="utf-8")
    monkeypatch.setattr(app, "ROOT", tmp_path)
    recent = app.datetime.now().astimezone().isoformat()
    monkeypatch.setattr(app.commits, "load", lambda data: {"records": {"live": {"last_commit_at": recent}, "done": {"last_commit_at": recent}}})


def test_archived_fields_parse_with_defaults(tmp_path, monkeypatch):
    archived_setup(tmp_path, monkeypatch)
    projects = {p["id"]: p for p in app.project_config()}
    assert not app.archived(projects["live"]) and projects["live"].get("archived_at") is None
    assert app.archived(projects["done"]) and projects["done"]["archived_at"] == "2026-11-12"


@pytest.mark.parametrize("field", ['archived = "yes"', 'archived = 1', 'review_prs = "yes"', 'archived = true\narchived_at = 2026-11-12',
                                   'archived = true\narchived_at = "11/12"', 'archived = true\narchived_at = "2026-13-40"'])
def test_archived_fields_reject_wrong_values(tmp_path, monkeypatch, field):
    config = tmp_path / "personal/projects.toml"
    config.parent.mkdir()
    config.write_text(f'[[projects]]\nid = "bad"\nname = "坏"\n{field}\n', encoding="utf-8")
    monkeypatch.setattr(app, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="bad"):
        app.project_config()


def test_archived_project_leaves_the_active_scope(tmp_path, monkeypatch):
    archived_setup(tmp_path, monkeypatch)
    # 近月有提交也不算当前项目：Issue 列表、待你处理、交给 Agent、GitHub 消息的当前工作台都从这里取
    assert [p["id"] for p in app.active_project_config()] == ["live"]
    assert [p["id"] for p in app.scoped_project_config()] == ["live", "done"]  # 近月范围照旧含归档的


@pytest.mark.parametrize("restore", ['archived = false\narchived_at = "2026-11-12"\n', ""], ids=["false", "removed"])
def test_archive_then_unarchive_restores_everything(tmp_path, monkeypatch, restore):
    """归档 → 取消归档（改成 false 或删掉这两行）→ 项目回到所有视图、监看也恢复，归档期间没有删改任何记录。"""
    from personal import agent_watch, issues
    real_active, real_scoped = app.active_project_config, app.scoped_project_config
    with instance(tmp_path, monkeypatch) as request:
        archived_setup(tmp_path, monkeypatch)
        monkeypatch.setattr(app, "project_config", lambda: app.validate(app.tomllib.loads((tmp_path / "personal/projects.toml").read_text(encoding="utf-8"))["projects"]))
        monkeypatch.setattr(app, "active_project_config", real_active)
        monkeypatch.setattr(app, "scoped_project_config", real_scoped)
        seen = []
        monkeypatch.setattr(issues, "view", lambda projects, **kw: seen.append([p["id"] for p in projects]) or {"issues": []})
        config = tmp_path / "personal/projects.toml"

        def views():
            board = request("/api/board")
            request("/api/issues")
            return (sorted(p["id"] for p in board["projects"]), [p["id"] for p in board["archived"]], sorted(seen[-1]),
                    sorted(agent_watch.projects(config)))

        before = config.read_text(encoding="utf-8")
        assert views() == (["live"], ["done"], ["live"], ["o/live"])
        assert config.read_text(encoding="utf-8") == before  # 读页面不改登记表

        config.write_text(ARCHIVED_TOML.replace('archived = true\narchived_at = "2026-11-12"\n', restore), encoding="utf-8")
        assert views() == (["done", "live"], [], ["done", "live"], ["o/done", "o/live"])


def test_board_lists_archived_projects_apart_and_issue_views_skip_them(tmp_path, monkeypatch):
    from personal import issues
    real_active, real_scoped = app.active_project_config, app.scoped_project_config
    with instance(tmp_path, monkeypatch) as request:
        archived_setup(tmp_path, monkeypatch)
        monkeypatch.setattr(app, "project_config", lambda: app.validate(app.tomllib.loads(ARCHIVED_TOML)["projects"]))
        monkeypatch.setattr(app, "active_project_config", real_active)
        monkeypatch.setattr(app, "scoped_project_config", real_scoped)
        payload = request("/api/board")
        assert [p["id"] for p in payload["projects"]] == ["live"]
        assert [(p["id"], p["archived"], p["archived_at"], p["issue_repository"], p["repository"]) for p in payload["archived"]] == \
            [("done", True, "2026-11-12", "", "o/done")]
        seen = []
        monkeypatch.setattr(issues, "view", lambda projects, **kw: seen.append([p["id"] for p in projects]) or {"issues": []})
        request("/api/issues")
        request("/api/issues/refresh", {})
        assert seen == [["live"], ["live"]]
        with pytest.raises(HTTPError) as error:  # 归档项目的 issue 不能交给 Agent
            request("/api/issues/o/done/1/handoff", {})
        assert error.value.code in (400, 502)


def test_notification_sync_keeps_a_local_store_without_creating_tasks(tmp_path, monkeypatch):
    from personal import notifications
    from personal_tests.test_notifications import row
    with instance(tmp_path, monkeypatch) as request:
        monkeypatch.setattr(app, 'project_config', lambda: [{'id': 'paihuo', 'name': '个人项目', 'repo_path': str(tmp_path), 'repository': 'me/repo'}])
        monkeypatch.setattr(notifications, 'github_get', lambda path, **kw: {'login': 'me'})
        current = row()
        monkeypatch.setattr(notifications, 'fetch_notifications', lambda: [current])
        first = request('/api/github/notifications/sync', {})
        second = request('/api/github/notifications/sync', {})
        assert first['created'] == 1 and second['created'] == second['updated'] == 0
        assert first['notifications'][0]['project_id'] == 'paihuo'
        current['unread'] = False
        assert request('/api/github/notifications/sync', {})['updated'] == 1
        assert not request('/api/github/notifications')['notifications'][0]['unread']
        stored = json.loads((tmp_path / 'instance/github-notifications.json').read_text())
        assert stored['account'] == 'me' and stored['ids'] == [current['id']] and list(stored['rows']) == [current['id']]
        assert sorted(p.name for p in (tmp_path / 'instance').iterdir()) == ['github-notifications.json']  # 只有这一份，不建任务
        monkeypatch.setattr(notifications, 'fetch_notifications', lambda: (_ for _ in ()).throw(app.APIError(502, 'GitHub 读取失败')))
        with pytest.raises(HTTPError) as failure:
            request('/api/github/notifications/sync', {})
        assert failure.value.code == 502
        cached = request('/api/github/notifications')
        assert len(cached['notifications']) == 1 and not cached['notifications'][0]['unread']
        with pytest.raises(HTTPError) as failure:
            request('/api/github/notifications', headers={'Host': 'outside.invalid'})
        assert failure.value.code == 403


def test_handoff_route_needs_local_origin(tmp_path, monkeypatch):
    from personal import issues
    calls = []
    monkeypatch.setattr(issues, "handoff", lambda projects, repo, number, mode=None, **_: calls.append((repo, number, mode)) or {"subject": f"{repo}#{number}"})
    monkeypatch.setattr(issues, "batch_handoff", lambda projects, items, mode=None, **_: calls.append((items, mode)) or {"results": []})
    with instance(tmp_path, monkeypatch) as request:
        assert request("/api/issues/o/r/3/handoff", {}) == {"subject": "o/r#3"}
        assert request("/api/issues/o/r/5/handoff", {"mode": "reply"}) == {"subject": "o/r#5"}  # 「帮我回复」带上模式
        assert request("/api/issues/handoff", {"items": ["o/r#6"], "mode": "handle"}) == {"results": []}
        with pytest.raises(HTTPError) as denied:
            request("/api/issues/o/r/4/handoff", {}, {"Origin": "https://evil.example"})
        assert denied.value.code == 403
    assert calls == [("o/r", "3", None), ("o/r", "5", "reply"), (["o/r#6"], "handle")]


def test_snooze_routes_write_only_the_local_file(tmp_path, monkeypatch):
    with instance(tmp_path, monkeypatch) as request:
        out = request("/api/snooze", {"items": [{"subject": "o/r#3", "their_at": "2026-11-17T00:00:00Z"}], "wait": "1w"})
        assert out["snoozed"] == ["o/r#3"] and out["until"]
        stored = json.loads((tmp_path / "instance/snoozed.json").read_text())["items"]
        assert stored["o/r#3"]["their_at"] == "2026-11-17T00:00:00Z"
        with pytest.raises(HTTPError) as bad:
            request("/api/snooze", {"items": ["o/r#4"], "wait": "1y"})
        assert bad.value.code == 400
        with pytest.raises(HTTPError) as denied:
            request("/api/snooze/restore", {"items": ["o/r#3"]}, {"Origin": "https://evil.example"})
        assert denied.value.code == 403
        assert request("/api/snooze/restore", {"items": ["o/r#3"]}) == {"restored": ["o/r#3"]}
        assert json.loads((tmp_path / "instance/snoozed.json").read_text())["items"] == {}


def test_pr_feedback_routes_need_local_origin(tmp_path, monkeypatch):
    from personal import pr_feedback
    calls = []
    monkeypatch.setattr(pr_feedback, "view", lambda projects, data, refresh=False: {"prs": [], "refresh": refresh})
    monkeypatch.setattr(pr_feedback, "handoff", lambda projects, keys, mode=None, **_: calls.append((keys, mode)) or {"handed": keys})
    with instance(tmp_path, monkeypatch) as request:
        assert request("/api/prs/feedback") == {"prs": [], "refresh": False}
        assert request("/api/prs/feedback/refresh", {}) == {"prs": [], "refresh": True}
        assert request("/api/prs/handoff", {"prs": ["up/x#7", "o/r#3"]}) == {"handed": ["up/x#7", "o/r#3"]}
        assert request("/api/prs/handoff", {"prs": ["up/x#9"], "mode": "reply"}) == {"handed": ["up/x#9"]}
        with pytest.raises(HTTPError) as denied:
            request("/api/prs/handoff", {"prs": ["up/x#8"]}, {"Origin": "https://evil.example"})
        assert denied.value.code == 403
    assert calls == [(["up/x#7", "o/r#3"], None), (["up/x#9"], "reply")]


def test_agents_route_reads_the_running_count(tmp_path, monkeypatch):
    from personal import conversation
    monkeypatch.setattr(conversation, "agents", lambda: {"running": 2, "queued": 1, "agents": []})
    with instance(tmp_path, monkeypatch) as request:
        assert request("/api/agents") == {"running": 2, "queued": 1, "agents": []}
        for gone in ("/api/v1/projects", "/api/cards"):
            with pytest.raises(HTTPError) as error:
                request(gone)
            assert error.value.code == 404


def test_linked_prs_route_is_read_only(tmp_path, monkeypatch):
    from personal import issues
    monkeypatch.setattr(issues, "linked", lambda projects, repo, number: {"prs": [{"number": 9}], "repo": repo, "n": number})
    monkeypatch.setattr(issues, "detail", lambda projects, repo, number: pytest.fail("PR 列表不该走详情"))
    with instance(tmp_path, monkeypatch) as request:
        assert request("/api/issues/up/x/7274/prs") == {"prs": [{"number": 9}], "repo": "up/x", "n": "7274"}


def test_scope_is_due_after_a_day_or_an_hour_after_a_github_error():
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
    at = lambda hours: (now - timedelta(hours=hours)).isoformat()
    assert not app.scope_due({"checked_at": at(23)}, now)
    assert app.scope_due({"checked_at": at(25)}, now)
    assert app.scope_due({}, now) and app.scope_due({"checked_at": "坏数据"}, now)
    assert not app.scope_due({"checked_at": at(0.5), "github_error": "限流"}, now)
    assert app.scope_due({"checked_at": at(2), "github_error": "限流"}, now)


def test_stale_scope_is_refreshed_in_the_background(monkeypatch):
    from datetime import datetime, timedelta
    monkeypatch.setattr(app, "active_project_config", lambda: [])
    old = {"checked_at": (datetime.now().astimezone() - timedelta(days=2)).isoformat()}
    done, release = threading.Event(), threading.Event()
    def refresh():
        release.wait(5)  # 核对很慢也不挡住调用方
        done.set()
        return {"github_error": ""}
    thread, stop = app.start_scope_keeper(refresh=refresh, load=lambda: old, wake=0.01)
    assert thread.daemon and thread.is_alive() and not done.is_set()
    release.set()
    assert done.wait(5)
    stop.set(); thread.join(5)
    assert not thread.is_alive()


def test_fresh_scope_is_left_alone_and_checked_again_later(monkeypatch):
    from datetime import datetime
    fresh = {"checked_at": datetime.now().astimezone().isoformat()}
    loads, stop = [], threading.Event()
    def load():
        loads.append(1)
        if len(loads) == 3:
            stop.set()
        return fresh
    app.keep_scope_fresh(stop, refresh=lambda: pytest.fail("不该核对"), load=load, wake=0.01)
    assert len(loads) == 3  # 每次醒来都看一眼 checked_at


def test_project_activity_route(tmp_path, monkeypatch):
    with instance(tmp_path, monkeypatch) as request:
        assert request("/api/projects/paihuo/activity?max_age=120") == {"repo": "", "available": False}
        with pytest.raises(HTTPError) as error:
            request("/api/projects/nonexistent/activity")
        assert error.value.code == 400
        with pytest.raises(HTTPError) as error:  # 刷新是写入口，跨站拒绝
            request("/api/projects/paihuo/activity/refresh", {}, {"Origin": "https://outside.invalid"})
        assert error.value.code == 403


def test_project_activity_marks_prs_with_unread_notifications(tmp_path, monkeypatch):
    """同步好的通知按主题对上项目 PR：已合并带未读的提到「有新消息」，开着的那一行带标记；GitHub 读了之后标记消失。"""
    from personal import activity, github, notifications, pr_news
    from personal_tests.test_pr_news import node, comment, pr, report, thread
    github.reset(); pr_news.reset()
    with instance(tmp_path, monkeypatch) as request:
        project = {'id': 'paihuo', 'name': '个人项目', 'repo_path': str(tmp_path), 'repository': 'up/x', 'github': 'up/x'}
        monkeypatch.setattr(app, 'project_config', lambda: [project])
        monkeypatch.setattr(app, 'active_project_config', lambda: [project])
        monkeypatch.setattr(app, 'find_project', lambda pid: project)
        monkeypatch.setattr(activity, 'view', lambda *a, **k: report(open_=[pr(7321), pr(7315)], merged=[pr(7310, 'merged')]))
        monkeypatch.setattr(notifications, 'github_get', lambda path, **kw: {'login': 'me'})
        monkeypatch.setattr(notifications, 'subject_states', lambda data, raws, **kw: ({}, ''))
        rows = [thread(7310, repo='up/x'), thread(7315, repo='up/x', updated='2026-11-12T07:38:46Z')]
        monkeypatch.setattr(notifications, 'fetch_notifications', lambda: [dict(r, id=str(r['id'])) for r in rows])
        monkeypatch.setattr(github, 'graphql', lambda *a, **k: {'r0': {'s7310': node([comment('SparkofSpike', '2026-11-12T08:40:00Z', 'Two ideas. One...')]),
                                                                      's7315': node([comment('Hmbown', '2026-11-12T07:34:23Z', 'thank you so much!')])}})
        request('/api/github/notifications/sync', {})
        out = request('/api/projects/paihuo/activity?max_age=120')
        assert [p['number'] for p in out['prs']['news']] == [7310]
        assert out['prs']['news'][0]['news']['who'] == 'SparkofSpike' and out['prs']['news'][0]['news']['count'] == 1
        assert out['prs']['open'][0]['number'] == 7315 and out['prs']['open'][0]['news']['who'] == 'Hmbown'
        assert [u['number'] for u in out['unread']] == [7310, 7315] and out['unread'][0]['text'] == 'Two ideas.'
        for r in rows:
            r['unread'] = False
        request('/api/github/notifications/sync', {})
        after = request('/api/projects/paihuo/activity')
        assert after['prs']['news'] == [] and after['unread'] == [] and after['prs']['open'][0]['news'] is None
    github.reset(); pr_news.reset()
