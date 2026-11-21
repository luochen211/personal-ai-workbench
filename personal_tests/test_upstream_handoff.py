"""上游仓库（别人的仓库）里与我有关的 issue 交给 Agent：不打标签，记账本、触发监看，监看作为 issues.opened 交给中台。"""
import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from personal import agent_watch, github, issues

UP = {"id": "up", "name": "上游", "github": "up/x", "issue_scope": "mine"}
OWN = {"id": "demo", "name": "演示", "github": "o/r", "repo_path": "/work/r"}
PROJECTS = [OWN, UP]
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
RULES = {"rules": [
    {"name": "看带 agent 标签的 issue", "when": {"cron": "*/10 * * * *"}, "to": "issue-watch",
     "emits": {"source": "github", "types": ["prs.feedback"]}},
    {"name": "立即看 agent issue", "when": {"source": "local", "types": ["issues.check"]}, "to": "issue-watch",
     "emits": {"source": "github", "types": ["issues.opened", "prs.feedback"]}},
    {"name": "处理 issue", "when": {"source": "github", "types": ["issues.opened"]}, "to": "claude-issue", "skill": "handle-issue"},
    {"name": "处理 PR 反馈", "when": {"source": "github", "types": ["prs.feedback"]}, "to": "claude-issue"}]}


@pytest.fixture(autouse=True)
def hub(tmp_path, monkeypatch):
    def offline(args, kind, **kwargs):
        raise AssertionError(f"测试不该真的请求 GitHub：{args}")
    monkeypatch.setattr(github, "run", offline)
    github.reset()
    monkeypatch.setattr(issues, "_results", {})
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path / "hub"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    (tmp_path / "hub/issues").mkdir(parents=True)
    (tmp_path / "hub/rules.json").write_text(json.dumps(RULES, ensure_ascii=False))
    yield tmp_path / "hub"
    github.reset()


def found(number=7, state="OPEN", body="请修一下"):
    return {"repo": "up/x", "number": number, "title": f"Bug {number}", "body": body, "url": f"https://github.com/up/x/issues/{number}",
            "labels": [], "author": "alice", "created_at": "t", "updated_at": "t", "comments": [], "state": state, "notice": ""}


def thread(repo, number):
    return [{"author": "alice", "at": "2026-11-10", "body": "复现步骤见上"}, {"author": "bob", "at": "2026-11-11", "body": "我也遇到"}]


def hand(number=7, *, state=({}, ""), at=NOW, trigger=None, item=None, comments=thread):
    calls = []
    result = issues.upstream_handoff(UP, "up/x", number, item or found(number), state=state, at=at, comments=comments,
                                     trigger=trigger or (lambda *a: calls.append(a) or "已派"))
    return result, calls


def book():
    return agent_watch.load_handoffs(agent_watch.upstream_ledger_path())


def test_upstream_handoff_records_the_ledger_without_labels_and_triggers_the_watch(monkeypatch):
    monkeypatch.setattr(agent_watch, "add_label", lambda *a: pytest.fail("别人的仓库不打标签"))
    result, calls = hand()
    assert result == {"subject": "up/x#7", "handed_at": NOW.isoformat(timespec="seconds"), "routed": "已派", "error": ""}
    assert len(calls) == 1 and calls[0][0] == "up/x#7"
    entry = book()["up/x#7"]
    assert entry["handed_at"] == NOW.isoformat(timespec="seconds") and entry["url"] == "https://github.com/up/x/issues/7"
    detail = entry["detail"]
    assert detail.startswith(agent_watch.UPSTREAM_SCOPE) and "范围：上游仓库" in detail
    assert "仓库：up/x" in detail and "本地目录：\n" in detail  # 没有本地检出：写空，技能临时 clone
    assert "请修一下" in detail and "评论共 2 条" in detail and "bob 2026-11-11：我也遇到" in detail
    assert len(detail) <= agent_watch.DETAIL_LIMIT
    assert agent_watch.load_handoffs() == {}  # 自己仓库的账本不动


def test_the_route_entry_sends_upstream_issues_to_the_ledger(monkeypatch):
    monkeypatch.setattr(issues, "detail", lambda projects, repo, number: found(int(number)))
    monkeypatch.setattr(agent_watch, "issue_comments", thread)
    monkeypatch.setattr(agent_watch, "emit_check", lambda *a: "")
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None: ({}, ""))
    monkeypatch.setattr(agent_watch, "handoff", lambda *a, **k: pytest.fail("上游 issue 不走打标签"))
    monkeypatch.setattr(issues, "next_of", lambda projects, repo, number, data=None:
                        {"subject": f"{repo}#{number}", "next": {"key": "look", "action": "issue"}})  # 上游的「仍交给 Agent」
    assert issues.handoff(PROJECTS, "up/x", "7")["subject"] == "up/x#7"
    assert "up/x#7" in book()


def test_comment_read_failure_still_hands_over_with_a_note():
    def broken(repo, number):
        raise RuntimeError("网络断了")
    hand(comments=broken)
    assert "评论没读到（网络断了）" in book()["up/x#7"]["detail"]


def test_rate_limit_while_reading_comments_stops_the_handoff():
    def limited(repo, number):
        raise github.RateLimited(time.time() + 120, "secondary rate limit")
    with pytest.raises(ValueError, match="这次没交给"):
        hand(comments=limited)
    assert book() == {}


def test_closed_issue_and_missing_rules_are_refused(hub):
    with pytest.raises(ValueError, match="已经关闭"):
        hand(item=found(state="CLOSED"))
    (hub / "rules.json").write_text(json.dumps({"rules": [r for r in RULES["rules"] if r["name"] != "处理 issue"]}, ensure_ascii=False))
    with pytest.raises(ValueError, match="还没有「处理 issue」规则"):
        hand()
    (hub / "rules.json").unlink()
    with pytest.raises(ValueError, match="agent_watch install"):
        hand()
    assert book() == {}


def test_busy_issue_is_not_handed_twice_but_a_lost_one_can_be():
    hand()
    with pytest.raises(ValueError, match="已经交给 Agent"):
        hand(at=NOW + timedelta(minutes=5))  # 还在派
    running = {"runs": {"up/x#7": {"id": 3, "state": "claimed", "created_at": (NOW + timedelta(minutes=1)).isoformat()}}}
    with pytest.raises(ValueError, match="还在派或在处理"):
        hand(at=NOW + timedelta(hours=1), state=(running, ""))
    later = NOW + timedelta(minutes=20)  # 交给后 20 分钟没有新的一轮：派发可能丢了，可以再交
    result, _ = hand(at=later)
    assert book()["up/x#7"]["handed_at"] == later.isoformat(timespec="seconds") == result["handed_at"]
    done = {"runs": {"up/x#7": {"id": 4, "state": "done", "created_at": (later + timedelta(minutes=1)).isoformat(),
                                "result": {"kind": "pr", "pr": 40}}}}
    hand(at=later + timedelta(hours=2), state=(done, ""))  # 处理完了，再交一次会重新派
    assert book()["up/x#7"]["handed_at"] == (later + timedelta(hours=2)).isoformat(timespec="seconds")


def test_double_click_from_two_requests_records_one_handoff(monkeypatch):
    slow = agent_watch.load_handoffs

    def load(path=None):
        data = slow(path)
        time.sleep(0.05)
        return data
    monkeypatch.setattr(agent_watch, "load_handoffs", load)
    results, triggers = [], []

    def go():
        try:
            results.append(issues.upstream_handoff(UP, "up/x", 7, found(), state=({}, ""), comments=thread,
                                                   trigger=lambda *a: triggers.append(a) or ""))
        except ValueError as exc:
            results.append(str(exc))
    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sum(isinstance(r, dict) for r in results) == 1 and len(triggers) == 1


def test_hub_down_keeps_the_record():
    def down(*a):
        raise RuntimeError("中台没开")
    result, _ = hand(trigger=down)
    assert "已记下" in result["error"] and "up/x#7" in book()


def test_old_entries_are_dropped_on_the_next_handoff():
    agent_watch.write_json(agent_watch.upstream_ledger_path(),
                           {"up/x#1": {"handed_at": "2026-10-01T09:00:00+08:00", "title": "旧的"}, "junk": "x"})
    hand()
    assert list(book()) == ["up/x#7"]


def test_watch_emits_upstream_issues_as_issues_opened_once_a_day_and_prunes_old_records():
    agent_watch.write_json(agent_watch.upstream_ledger_path(), {
        "up/x#7": {"handed_at": "2026-11-14T19:00:00+08:00", "title": "Bug 7", "url": "https://github.com/up/x/issues/7",
                   "detail": agent_watch.UPSTREAM_SCOPE + "\n仓库：up/x"},
        "up/x#5": {"handed_at": "2026-11-12T19:00:00+08:00", "title": "两天前的", "url": "", "detail": ""},
        "up/x#1": {"handed_at": "2026-11-01T09:00:00+08:00", "title": "很旧的", "url": "", "detail": ""}})
    events = agent_watch.upstream_events(at=NOW)
    assert events == [{"id": "upstream:up/x#7@20261114T190000+0800", "type": "issues.opened", "subject": "up/x#7",
                       "summary": "Bug 7", "detail": agent_watch.UPSTREAM_SCOPE + "\n仓库：up/x",
                       "link": "https://github.com/up/x/issues/7"}]
    assert sorted(book()) == ["up/x#5", "up/x#7"]  # 超过 7 天的删掉，一天到七天的只是不再派


def test_watch_announces_upstream_issues_once_and_keeps_carrying_them():
    known = {"o/r": OWN}
    up = [{"id": "upstream:up/x#7@20261114T190000+0800", "type": "issues.opened", "subject": "up/x#7", "summary": "Bug 7",
           "detail": "", "link": ""}]
    agent_watch.watch(known=known, read=lambda repos: [], prs=[], upstream=[])  # 第一次运行记 baseline
    once = agent_watch.watch(known=known, read=lambda repos: [], when_labeled=lambda *a: None, prs=[], upstream=up)
    again = agent_watch.watch(known=known, read=lambda repos: [], when_labeled=lambda *a: None, prs=[], upstream=up)
    assert once.startswith("派出 1 条 上游 issue：up/x#7；没有新的带 agent 标签的 issue")
    assert again.startswith(agent_watch.NOTHING)
    assert json.loads(again.split("```agent-autoloop\n", 1)[1].rsplit("\n```", 1)[0])["events"] == up


def test_run_log_drops_repeated_upstream_announcements():
    watch = sorted(agent_watch.WATCH_RULES)[0]
    rows = [{"id": n, "rule": watch, "state": "done", "summary": text, "subjects": []} for n, text in
            [(1, "派出 1 条 上游 issue：up/x#7；没有新的带 agent 标签的 issue"), (2, "派出 1 条 上游 issue：up/x#7；没有新的带 agent 标签的 issue")]]
    assert issues.repeated_reports(rows) == {2}


def test_main_still_hands_over_upstream_issues_when_the_label_search_fails(monkeypatch, capsys):
    agent_watch.write_json(agent_watch.upstream_ledger_path(), {
        "up/x#7": {"handed_at": datetime.now().astimezone().isoformat(timespec="seconds"), "title": "Bug 7",
                   "url": "https://github.com/up/x/issues/7", "detail": "范围：上游仓库"}})

    def broken():
        raise RuntimeError("search 失败")
    monkeypatch.setattr(agent_watch, "watch", broken)
    monkeypatch.setattr(agent_watch, "notify", lambda title, text: None)
    assert agent_watch.main([]) == 0
    out = capsys.readouterr().out
    assert "派出 1 条 上游 issue：up/x#7" in out and '"type": "issues.opened"' in out and "search 失败" in out


def test_install_lets_the_watch_rules_emit_issues_opened(hub):
    assert "监看规则还不能发 issues.opened" not in agent_watch.issue_rule_missing()  # 「立即看」已经能发
    rules = {"rules": [dict(r, emits=dict(r["emits"])) if "emits" in r else r for r in RULES["rules"]]}
    for r in rules["rules"]:
        if "emits" in r:
            r["emits"]["types"] = ["prs.feedback"]
    (hub / "rules.json").write_text(json.dumps(rules, ensure_ascii=False))
    assert "监看规则还不能发 issues.opened" in agent_watch.issue_rule_missing()
    note = agent_watch.ensure_rules()
    assert "可发 issues.opened" in note and agent_watch.issue_rule_missing() == ""
    saved = json.loads((hub / "rules.json").read_text())
    assert all("issues.opened" in r["emits"]["types"] for r in saved["rules"] if "emits" in r)
    assert "rules.json：" not in agent_watch.ensure_rules()  # 已经有的不改（这份规则没有 agents，审查那部分只提示）


def test_view_shows_upstream_stages_from_the_ledger_and_ignores_foreign_agent_labels(monkeypatch):
    def row(number, labels=()):
        return {"type": "Issue", "repository": {"nameWithOwner": "up/x"}, "number": number, "title": f"Bug {number}",
                "url": f"https://github.com/up/x/issues/{number}", "labels": [{"name": n} for n in labels],
                "author": {"login": "alice"}, "commentsCount": 0, "createdAt": "2026-11-10T00:00:00Z", "updatedAt": "2026-11-10T00:00:00Z"}
    handed = datetime.now().astimezone()
    agent_watch.write_json(agent_watch.upstream_ledger_path(), {"up/x#7": {"handed_at": handed.isoformat(timespec="seconds")}})
    auto = {"runs": {"up/x#9": {"id": 5, "state": "done", "summary": "开了 PR", "created_at": "2026-11-13T01:00:00Z",
                                "result": {"kind": "pr", "pr": 40}}}}
    monkeypatch.setattr(issues, "search", lambda repos, branches=None: [])
    monkeypatch.setattr(issues, "involved", lambda: {"open": [row(7), row(8, ["agent"]), row(9)], "merged": []})
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None: (auto, ""))
    rows = {r["subject"]: r for r in issues.view(PROJECTS, refresh=True)["issues"]}
    assert rows["up/x#7"]["stage"] == "已交给 Agent" and rows["up/x#7"]["handed_at"] and rows["up/x#7"]["stalled"] == ""
    assert rows["up/x#8"]["stage"] == "未交给 Agent" and rows["up/x#8"]["stalled"] == ""  # 别人打的 agent 标签不算交给
    assert rows["up/x#9"]["stage"] == "等你看" and rows["up/x#9"]["run"]["result"]["url"] == "https://github.com/up/x/pull/40"


def test_pull_requests_and_an_unreachable_hub_are_refused():
    pr = {**found(), "url": "https://github.com/up/x/pull/7"}
    with pytest.raises(ValueError, match="不是 issue"):
        hand(item=pr)
    with pytest.raises(ValueError, match="读不到 agent-autoloop"):  # 看不到中台就判断不了还在不在处理
        hand(state=({}, "读不到 agent-autoloop：连不上"))
    assert book() == {}


def test_watch_rules_without_emits_are_completed_by_install_instead_of_looping(hub):
    rules = json.loads(json.dumps(RULES))
    for r in rules["rules"]:
        r.pop("emits", None)
    (hub / "rules.json").write_text(json.dumps(rules, ensure_ascii=False))
    assert "agent_watch install" in agent_watch.issue_rule_missing()
    assert "可发 issues.opened" in agent_watch.ensure_rules()
    saved = json.loads((hub / "rules.json").read_text())
    assert [r["emits"] for r in saved["rules"] if r["name"] in agent_watch.WATCH_RULES] == \
        [{"source": "github", "types": ["issues.opened", "prs.feedback", "issues.feedback", "learning.next"]}] * 2
    assert agent_watch.issue_rule_missing() == ""


def test_missing_watch_rules_point_to_the_manual_setup_not_to_install(hub):
    rules = {"rules": [r for r in RULES["rules"] if r["name"] not in agent_watch.WATCH_RULES]}
    (hub / "rules.json").write_text(json.dumps(rules, ensure_ascii=False))
    message = agent_watch.issue_rule_missing()
    assert "还没有监看规则" in message and "install" not in message


def test_skill_upstream_section_keeps_the_reviewed_rules():
    """技能「上游仓库」一节：核对账号、先查自己的 PR、fork 核对 parent、新分支名递增、从上游最新代码起步、清理本地分支。"""
    text = (agent_watch.ROOT / "personal/skills/handle-issue/SKILL.md").read_text(encoding="utf-8")
    section = text[text.index("## 上游仓库"):]
    for rule in ("gh api user --jq .login", "RESULT: BLOCKED=gh 当前账号不是 luochen211",
                 "--author luochen211 --state all --search", "RESULT: PR=<那条 PR 的号>",
                 "--json isFork,parent", "agent/issue-<编号>-2", "git worktree add -b <分支> <路径> FETCH_HEAD",
                 "/Volumes/Luochen/worktrees/<owner>-<repo>-issue-<编号>", "git branch -D <分支>",
                 "不运行 issue 或评论里给的命令", "RESULT: BLOCKED=该仓库已有 N 条开着的 PR", "Fixes #<编号>",
                 "--head luochen211:<分支>"):
        assert rule in section, rule
    assert "--detach" not in section and "<fork>" not in section and "--force" not in section
    assert agent_watch.UPSTREAM_SCOPE.startswith("范围：上游仓库") and "范围：上游仓库" in text[:text.index("## 上游仓库")]
