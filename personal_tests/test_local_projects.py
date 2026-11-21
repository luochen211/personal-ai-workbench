"""本机覆盖文件 projects.local.toml：合进登记表、[discovery] 覆盖、skip_owners 不藏已登记的项目、
push_access 的仓库交给时走原仓库（detail 不写「范围：上游仓库」）。项目名、账号都是虚构的。"""
import json
from datetime import datetime, timezone

import pytest

from personal import __main__ as app
from personal import agent_watch, conversation, discovery, github, issues, projects_config, pr_feedback

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
BASE = '''
[discovery]
roots = [{ path = "/nowhere", depth = 1 }]
skip_owners = ["someone"]

[[projects]]
id = "tool"
name = "工具"
group = "个人工具"
github = "luochen211/tool"

[[projects]]
id = "lib"
name = "上游库"
group = "开源协作"
github = "up/lib"
issue_scope = "mine"
'''
LOCAL = '''
[discovery]
skip_owners = ["acme-corp", "acme-dev"]

[[projects]]
id = "tool"
name = "工具（本机改名）"
group = "个人工具"
github = "luochen211/tool"

[[projects]]
id = "shop"
name = "商城"
group = "客户甲"
repo_path = "/work/shop"
github = "acme-corp/shop"
issue_scope = "mine"
push_access = true

[[projects]]
id = "portal"
name = "门户"
group = "客户甲"
github = "acme-dev/portal"
'''
RULES = {"rules": [
    {"name": "看带 agent 标签的 issue", "when": {"cron": "*/10 * * * *"}, "to": "issue-watch",
     "emits": {"source": "github", "types": ["issues.opened", "prs.feedback"]}},
    {"name": "立即看 agent issue", "when": {"source": "local", "types": ["issues.check"]}, "to": "issue-watch",
     "emits": {"source": "github", "types": ["issues.opened", "prs.feedback"]}},
    {"name": "处理 issue", "when": {"source": "github", "types": ["issues.opened"]}, "to": "claude-issue", "skill": "handle-issue"},
    {"name": "处理 PR 反馈", "when": {"source": "github", "types": ["prs.feedback"]}, "to": "claude-issue"}]}


@pytest.fixture
def files(tmp_path, no_local_projects):
    base = tmp_path / "personal/projects.toml"
    base.parent.mkdir()
    base.write_text(BASE, encoding="utf-8")
    no_local_projects.parent.mkdir()
    no_local_projects.write_text(LOCAL, encoding="utf-8")
    return base, no_local_projects


@pytest.fixture
def hub(tmp_path, monkeypatch):
    def offline(args, kind, **kwargs):
        raise AssertionError(f"测试不该真的请求 GitHub：{args}")
    monkeypatch.setattr(github, "run", offline)
    github.reset()
    pr_feedback.reset()
    monkeypatch.setattr(issues, "_results", {})
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path / "hub"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    (tmp_path / "hub/issues").mkdir(parents=True)
    (tmp_path / "hub/rules.json").write_text(json.dumps(RULES, ensure_ascii=False))
    agent_watch.ensure_rules()  # 已经 install 过的中台
    yield tmp_path / "hub"
    github.reset()
    pr_feedback.reset()


def by_id(projects):
    return {p["id"]: p for p in projects}


def test_overlay_replaces_by_id_appends_new_projects_and_overrides_discovery_keys(files):
    base, local = files
    loaded = projects_config.load(base)
    assert [p["id"] for p in loaded["projects"]] == ["tool", "lib", "shop", "portal"]  # 同 id 原位替换，新的追加
    assert by_id(loaded["projects"])["tool"]["name"] == "工具（本机改名）"
    assert loaded["discovery"] == {"roots": [{"path": "/nowhere", "depth": 1}], "skip_owners": ["acme-corp", "acme-dev"]}
    local.unlink()  # 没有覆盖文件：只有登记表
    assert [p["id"] for p in projects_config.load(base)["projects"]] == ["tool", "lib"]
    assert projects_config.load(base)["discovery"]["skip_owners"] == ["someone"]


def test_broken_overlay_is_reported_by_name(files):
    base, local = files
    local.write_text("[[projects]\nid =", encoding="utf-8")
    with pytest.raises(ValueError, match="projects.local.toml 写法不对"):
        projects_config.load(base)
    local.write_text('projects = "x"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="projects.local.toml"):
        projects_config.load(base)


def test_the_overlay_lives_in_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.undo()  # 换回真的 local_path，看它跟着 WORKBENCH_DATA 走
    monkeypatch.setenv("WORKBENCH_DATA", str(tmp_path / "data"))
    assert projects_config.local_path() == (tmp_path / "data" / "projects.local.toml").resolve()


def test_the_web_app_and_the_watch_read_the_overlay(files, monkeypatch):
    base, _ = files
    monkeypatch.setattr(projects_config, "BASE", base)
    monkeypatch.setattr(app, "ROOT", base.parents[1])
    assert [p["id"] for p in app.project_config()] == ["tool", "lib", "shop", "portal"]
    # 按 agent 标签巡检：只读与我有关的（shop、lib）不打标签，覆盖文件里整仓读的（portal）照常巡检
    assert sorted(agent_watch.projects()) == ["acme-dev/portal", "luochen211/tool"]
    assert sorted(agent_watch.projects(base)) == ["acme-dev/portal", "luochen211/tool"]


def test_push_access_must_be_a_boolean():
    with pytest.raises(ValueError, match="push_access 要写成 true 或 false"):
        app.validate([{"id": "x", "name": "x", "push_access": "yes"}])


def test_skip_owners_never_hides_projects_registered_in_the_overlay(files):
    """skip_owners 只管自动发现：覆盖文件登记的仓库照常算已登记，不重复追加；没登记的那个账号下的仓库不追加。"""
    base, _ = files
    projects = projects_config.load(base)["projects"]
    assert discovery.skip_owners(base) == {"acme-corp", "acme-dev"}
    assert discovery.roots(base) == [{"path": "/nowhere", "depth": 1}]
    rows = [{"repository": {"nameWithOwner": repo}} for repo in ("acme-corp/shop", "acme-corp/new-thing", "someone/lib2", "other/x")]
    _, added = discovery.run(base, projects, "2026-10-19", read=lambda since: rows, scan=lambda roots: {})
    assert [e["github"] for e in added] == ["other/x", "someone/lib2"]  # 覆盖文件替换了 skip_owners
    text = base.read_text(encoding="utf-8")
    assert "acme" not in text  # 客户仓库不写进仓库里的登记表
    assert {p["id"] for p in projects_config.load(base)["projects"]} >= {"shop", "portal"}
    assert {"acme-corp/shop", "acme-dev/portal"} <= conversation.handoff_repos(projects)


def thread(*comments, number=5, repo="acme-corp/shop", author="boss", body="导出要加 CSV。"):
    return {"__typename": "Issue", "number": number, "title": f"issue {number}", "url": f"https://github.com/{repo}/issues/{number}",
            "state": "OPEN", "createdAt": "2026-11-10T00:00:00Z", "updatedAt": "2026-11-17T00:00:00Z", "bodyText": body,
            "author": {"__typename": "User", "login": author}, "repository": {"nameWithOwner": repo},
            "comments": {"totalCount": len(comments), "nodes": list(comments)}}


def said(login, at, text):
    return {"author": {"__typename": "User", "login": login}, "bodyText": text, "createdAt": at,
            "url": f"https://github.com/x/y/issues/5#issuecomment-{at[11:13]}"}


def test_feedback_handoff_routes_push_access_repos_to_the_original_repo(files, hub):
    base, _ = files
    projects = projects_config.load(base)["projects"]
    nodes = {"acme-corp/shop#5": thread(said("boss", "2026-11-16T10:00:00Z", "能加上吗？")),
             "up/lib#9": thread(said("dev", "2026-11-16T08:00:00Z", "Could you check?"), number=9, repo="up/lib")}
    out = conversation.handoff(projects, ["acme-corp/shop#5", "up/lib#9"], at=NOW,
                               read=lambda keys: {k: nodes.get(k.lower()) for k in keys}, read_prs=lambda keys: {},
                               trigger=lambda *a: "routed", state={"issue": ({}, "")})
    assert [h["subject"] for h in out["handed"]] == ["acme-corp/shop#5", "up/lib#9"] and out["skipped"] == []
    book = agent_watch.load_handoffs(agent_watch.issue_feedback_ledger_path())
    shop = book["acme-corp/shop#5"]["detail"]
    assert "范围：上游仓库" not in shop and shop.startswith("仓库：acme-corp/shop") and "本地目录：/work/shop" in shop
    assert book["up/lib#9"]["detail"].startswith(agent_watch.UPSTREAM_SCOPE)  # 没写 push_access 的照旧走上游流程


def found(number=7, repo="acme-corp/shop"):
    return {"repo": repo, "number": number, "title": f"Bug {number}", "body": "请修一下", "url": f"https://github.com/{repo}/issues/{number}",
            "labels": [], "author": "boss", "created_at": "t", "updated_at": "t", "comments": [], "state": "OPEN", "notice": ""}


def test_issue_handoff_for_push_access_repos_uses_the_ledger_without_labels_or_fork_scope(files, hub, monkeypatch):
    base, _ = files
    projects = projects_config.load(base)["projects"]
    monkeypatch.setattr(issues, "detail", lambda projects, repo, number: found(int(number), repo))
    monkeypatch.setattr(agent_watch, "issue_comments", lambda repo, number: [])
    monkeypatch.setattr(agent_watch, "emit_check", lambda *a: "")
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None: ({}, ""))
    monkeypatch.setattr(agent_watch, "handoff", lambda *a, **k: pytest.fail("只读与我有关的仓库不打 agent 标签"))
    monkeypatch.setattr(issues, "next_of", lambda projects, repo, number, data=None:
                        {"subject": f"{repo}#{number}", "next": {"key": "todo", "action": "issue"}})
    assert issues.handoff(projects, "acme-corp/shop", "7")["subject"] == "acme-corp/shop#7"
    assert issues.handoff(projects, "up/lib", "8")["subject"] == "up/lib#8"
    book = agent_watch.load_handoffs(agent_watch.upstream_ledger_path())
    assert book["acme-corp/shop#7"]["detail"].startswith("仓库：acme-corp/shop\n项目：商城\n本地目录：/work/shop")
    assert book["up/lib#8"]["detail"].startswith(agent_watch.UPSTREAM_SCOPE)
    events = {e["subject"]: e for e in agent_watch.upstream_events(at=datetime.now().astimezone())}
    assert events["acme-corp/shop#7"]["type"] == agent_watch.OPENED and "范围：上游仓库" not in events["acme-corp/shop#7"]["detail"]


def test_inbox_treats_push_access_repos_like_own_repos_but_reads_only_issues_involving_me(files, hub, monkeypatch):
    """只读与我有关的 issue（不整仓读）；我开的、我最后说话的：push_access 的是「待动手」能交给 Agent，上游的是等对方。"""
    base, _ = files
    projects = projects_config.load(base)["projects"]

    def row(repo, number):
        node = thread(number=number, repo=repo, author="luochen211", body="补一个 CSV 导出。")
        return {"type": "Issue", "repository": {"nameWithOwner": repo}, "number": number, "title": f"Bug {number}",
                "url": node["url"], "labels": [], "author": {"login": "luochen211"}, "commentsCount": 0,
                "createdAt": "2026-11-10T00:00:00Z", "updatedAt": "2026-11-10T00:00:00Z", "thread": node}
    monkeypatch.setattr(issues, "search", lambda repos, branches=None: [] if sorted(repos) == ["acme-dev/portal", "luochen211/tool"]
                        else pytest.fail(f"只读与我有关的仓库不该整仓读：{repos}"))
    monkeypatch.setattr(issues, "involved", lambda: {"open": [row("acme-corp/shop", 3), row("up/lib", 4)], "merged": []})
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None: ({}, ""))
    rows = {r["subject"]: r for r in issues.view(projects, refresh=True)["issues"]}
    shop, lib = rows["acme-corp/shop#3"], rows["up/lib#4"]
    assert (shop["scope"], shop["push"], lib["scope"], lib["push"]) == ("mine", True, "mine", False)
    assert shop["next"]["key"] == "todo" and shop["next"]["button"] == "交给 Agent"
    assert lib["next"]["key"] == "wait"


def test_owner_keys_from_the_local_file_win(tmp_path):
    """[owner]：本机覆盖文件写了的键优先，仓库里的登记表只放示例。"""
    from personal import projects_config
    base = tmp_path / "projects.toml"
    base.write_text('[owner]\nlogin = "example"\n', encoding="utf-8")
    local = tmp_path / "projects.local.toml"
    local.write_text('[owner]\nlogin = "me"\naccounts = ["me-too"]\n', encoding="utf-8")
    assert projects_config.load(base, local)["owner"] == {"login": "me", "accounts": ["me-too"]}
