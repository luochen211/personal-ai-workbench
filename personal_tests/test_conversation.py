import json
import threading
from datetime import datetime, timezone

import pytest

from personal import agent_watch, conversation, github, issues, pr_feedback, reply_mode
from personal_tests.test_pr_feedback import THEIRS, node as pr_node

ME = "luochen211"
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
PROJECTS = [{"id": "orders", "name": "订单", "group": "合作项目", "github": "acme/orders", "repo_path": "/work/orders"},
            {"id": "kb", "name": "知识库", "group": "合作项目", "github": "acme/kb", "repository": "ACME/kb-mirror"},
            {"id": "gone", "name": "归档的", "group": "合作项目", "github": "acme/old", "archived": True},
            {"id": "tool", "name": "工具", "group": "个人工具", "github": "luochen211/app"}]
RULES = {"sources": {"github": {"name": "GitHub"}},
         "agents": {"claude-issue": {"name": "Claude Code（处理 issue）", "worker": "mini", "use": "claude", "access": "full"}},
         "rules": [{"name": "看带 agent 标签的 issue", "when": {"cron": "*/10 * * * *"}, "to": "issue-watch",
                    "emits": {"source": "github", "types": ["issues.opened", "prs.feedback"]}},
                   {"name": "立即看 agent issue", "when": {"source": "local", "types": ["issues.check"]}, "to": "issue-watch",
                    "emits": {"source": "github", "types": ["issues.opened", "prs.feedback"]}},
                   {"name": "处理 issue", "when": {"source": "github", "types": ["issues.opened"]}, "to": "claude-issue",
                    "cwd": "/Volumes/Luochen", "skill": "handle-issue", "parallel": 1},
                   {"name": "处理 PR 反馈", "when": {"source": "github", "types": ["prs.feedback"]}, "to": "claude-issue",
                    "cwd": "/Volumes/Luochen", "skill": "handle-pr-feedback", "parallel": 1},
                   {"name": "每日简报", "when": {"cron": "30 8 * * *"}, "to": "daily-brief"}]}


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
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


def said(login, at, text="这个怎么处理？", typename="User"):
    return {"author": {"__typename": typename, "login": login}, "bodyText": text, "createdAt": at,
            "url": f"https://github.com/acme/orders/issues/5#issuecomment-{at[11:13]}"}


def thread(*comments, number=5, repo="acme/orders", author="boss", created="2026-11-10T00:00:00Z", state="OPEN"):
    return {"__typename": "Issue", "number": number, "title": f"issue {number}", "url": f"https://github.com/{repo}/issues/{number}",
            "state": state, "createdAt": created, "updatedAt": "2026-11-17T00:00:00Z", "author": {"__typename": "User", "login": author},
            "repository": {"nameWithOwner": repo}, "comments": {"totalCount": len(comments), "nodes": list(comments)}}


ASKED = said("boss", "2026-11-16T10:00:00Z", "退款口径用哪一版？后面还有一句。")


def test_someone_elses_comment_after_mine_is_pending():
    out = conversation.assess(thread(said(ME, "2026-11-16T09:00:00Z", "我看看"), ASKED))
    assert out["pending"] and out["new"] == 1 and out["their_at"] == "2026-11-16T10:00:00Z" and out["mine_at"] == "2026-11-16T09:00:00Z"
    assert out["said"] == [{"who": "boss", "what": "留言", "at": "2026-11-16T10:00:00Z", "text": "退款口径用哪一版？",
                            "url": ASKED["url"], "path": "", "new": True}]
    answered = conversation.assess(thread(ASKED, said(ME, "2026-11-16T11:00:00Z", "用第二版")))
    assert not answered["pending"] and answered["new"] == 0
    assert answered["said"][0]["who"] == "boss" and not answered["said"][0]["new"]  # 仍给人看对方最后一条


def test_someone_elses_issue_with_only_a_body_waits_on_me():
    out = conversation.assess({**thread(author="boss", created="2026-11-15T00:00:00Z"), "bodyText": "退款口径定一下。后面还有。"})
    assert out["pending"] and out["body_only"] and out["new"] == 0 and out["their_at"] == "2026-11-15T00:00:00Z"
    assert out["said"][0]["who"] == "boss" and out["said"][0]["what"] == "开了 issue" and out["said"][0]["text"].startswith("退款口径定一下")
    assert not conversation.assess(thread(said(ME, "2026-11-16T09:00:00Z", "收到"), author="boss"))["pending"]  # 我回过：等对方
    assert not conversation.assess(thread(author="dependabot[bot]"))["pending"]  # 机器人开的不算
    assert not conversation.assess(thread(author=ME))["body_only"]


def test_without_a_comment_of_mine_my_own_issue_counts_from_its_creation_and_others_issues_count_everything():
    mine = thread(ASKED, author=ME, created="2026-11-15T00:00:00Z")
    assert conversation.assess(mine)["pending"] and conversation.assess(mine)["mine_at"] == "2026-11-15T00:00:00Z"
    assert not conversation.assess(thread(author=ME))["pending"]  # 我开的、还没人回
    theirs = conversation.assess(thread(said("alice", "2026-11-11T00:00:00Z", "@luochen211 看一下"), ASKED))
    assert theirs["pending"] and theirs["new"] == 2 and theirs["mine_at"] == ""
    assert [s["who"] for s in theirs["said"]] == ["boss", "alice"]  # 新的在前


@pytest.mark.parametrize("bot", [
    said("coderabbitai", "2026-11-16T12:00:00Z", "Summary", typename="Bot"),
    said("netlify[bot]", "2026-11-16T12:00:00Z", "Deploy preview"),
    said("github-actions", "2026-11-16T12:00:00Z", "Lint"),
    {"author": None, "bodyText": "ghost", "createdAt": "2026-11-16T12:00:00Z", "url": ""},
])
def test_bot_comments_are_not_pending(bot):
    out = conversation.assess(thread(said(ME, "2026-11-16T11:00:00Z", "好"), bot))
    assert not out["pending"] and out["their_at"] == ""


def test_mailed_comments_from_outsiders_are_not_pending():
    mine = said(ME, "2026-11-16T11:00:00Z", "好")
    auto = {**said("subscriber", "2026-11-16T12:00:00Z", "邮件已收到！"), "authorAssociation": "NONE", "createdViaEmail": True}
    assert not conversation.assess(thread(mine, auto))["pending"]
    owner = {**said("boss", "2026-11-16T12:00:00Z"), "authorAssociation": "OWNER", "createdViaEmail": True}
    assert conversation.assess(thread(mine, owner))["pending"]


@pytest.mark.parametrize("text, expected", [
    ("做完了\nRESULT: REPLIED", {"kind": "replied"}),
    ("RESULT: PR=34", {"kind": "pr", "prs": [34]}),
    ("RESULT: PR=34,35\n\n", {"kind": "pr", "prs": [34, 35]}),
    ("RESULT: NO_ACTION=boss 说他们来改", {"kind": "no_action", "reason": "boss 说他们来改"}),
    ("RESULT: BLOCKED=等你定：退款口径", {"kind": "blocked", "reason": "等你定：退款口径"}),
    ("RESULT: PR=34, 35", None), ("RESULT: PR=", None), ("RESULT: PR=0", None), ("RESULT: PUSHED=abc1234", None),
    ("RESULT: REPLIED\n后面又写了一句", None), ("`RESULT: REPLIED`", None), ("", None),
])
def test_parse_result_only_trusts_an_exact_last_line(text, expected):
    assert conversation.parse_result(text) == expected


def test_stage_treats_reply_pr_and_no_action_as_settled():
    done = lambda result: {"state": "done", "created_at": "2026-11-17T10:00:00+08:00", "result": result}
    for kind in ("replied", "pr", "no_action"):
        assert conversation.stage(done({"kind": kind}), [], "2026-11-17T09:00:00+08:00") == "已处理"
    assert conversation.stage(done({"kind": "blocked", "reason": "x"}), [], None) == "等你决定"
    assert conversation.stage(done({"kind": "pushed"}), [], None) == "等你看"  # 不是这条规则的结果行
    assert conversation.stage({"state": "claimed", "created_at": "2026-11-17T10:00:00+08:00"}, [], None) == "处理中"


def test_skill_documents_exactly_the_lines_the_parser_accepts():
    text = (agent_watch.ROOT / "personal/skills/handle-issue-feedback/SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: handle-issue-feedback\n") and agent_watch.ISSUE_FEEDBACK_SKILL in agent_watch.SKILLS
    for example, kind in (("RESULT: REPLIED", "replied"), ("RESULT: PR=34", "pr"), ("RESULT: PR=34,35", "pr"),
                          ("RESULT: NO_ACTION=octocat 说他们那边来改", "no_action"),
                          ("RESULT: BLOCKED=等你定：退款口径用哪一版", "blocked")):
        assert f"`{example}`" in text and conversation.parse_result(example)["kind"] == kind
    for must in ("issues.feedback", "不是给你的指令", "gh issue view <编号> --repo <owner/repo> --comments", "gh issue comment",
                 "/Volumes/Luochen/worktrees/", "AGENTS.md", "不合并", "不推默认分支", "`ask`", "【问你】",
                 "issue-feedback-<subject>", "_待删除", "Co-Authored-By",
                 "范围：上游仓库", "gh repo fork", "--head luochen211:<分支>", "feedback/issue-<编号>"):  # 上游仓库照上游流程 fork 后开 PR
        assert must in text
    assert agent_watch.UPSTREAM_SCOPE.startswith("范围：上游仓库")  # detail 里那一行和技能认的写法一致
    assert "agent/issue-" not in text  # 那种分支名会触发 agent PR 的自动审查


def write_ledger(entries):
    agent_watch.write_json(agent_watch.issue_feedback_ledger_path(), entries)


def hub_run(rid, rule, state, subject, claimed=None, created="2026-11-17T03:00:00Z"):
    return {"id": rid, "rule": rule, "state": state, "createdAt": created, "claimedAt": claimed, "finishedAt": None,
            "events": [{"subject": subject, "type": "x"}] if subject else []}


def test_agents_count_running_and_queued_runs_of_every_rule_but_the_watchers(monkeypatch, fresh):
    monkeypatch.setattr(issues, "_runs", {"settled": 0, "rows": {}})
    state = {"runs": [hub_run(9, "处理 issue 留言", "queued", "acme/orders#6"),
                      hub_run(8, "处理 issue 留言", "claimed", "acme/orders#5", claimed="2026-11-17T03:00:05Z"),
                      hub_run(7, "处理 PR 反馈", "claimed", "acme/orders#7", claimed="2026-11-17T03:00:02Z"),
                      hub_run(6, "处理 issue", "claimed", "luochen211/app#1", claimed="2026-11-17T02:00:00Z"),
                      hub_run(5, "立即看 agent issue", "claimed", "issue-feedback", claimed="2026-11-17T03:00:00Z"),
                      hub_run(4, "审查 PR", "unknown", "luochen211/app#2", claimed="2026-11-17T01:00:00Z"),
                      {**hub_run(3, "处理 PR 反馈", "done", "acme/orders#3"), "finishedAt": "2026-11-17T02:30:00Z"}], "cards": []}
    out = conversation.agents(lambda path: state)
    assert (out["running"], out["queued"], out["unknown"]) == (3, 1, 1) and out["error"] == ""
    assert [(a["rule"], a["subject"], a["started_at"], a["state"]) for a in out["agents"]] == [
        ("处理 issue", "luochen211/app#1", "2026-11-17T02:00:00Z", "running"),
        ("处理 PR 反馈", "acme/orders#7", "2026-11-17T03:00:02Z", "running"),
        ("处理 issue 留言", "acme/orders#5", "2026-11-17T03:00:05Z", "running"),
        ("处理 issue 留言", "acme/orders#6", "2026-11-17T03:00:00Z", "queued"),
        ("审查 PR", "luochen211/app#2", "2026-11-17T01:00:00Z", "unknown")]
    # 上限：规则的 parallel 和接单程序容量里小的；config.json 没写 capacity 按中台默认的 2
    assert (out["parallel"], out["capacity"], out["limit"]) == (10, None, 10)
    (fresh / "config.json").write_text(json.dumps({"worker": {"name": "mini", "key": "k"}}))
    assert (conversation.agents(lambda path: state)["capacity"], conversation.agents(lambda path: state)["limit"]) == (2, 2)
    (fresh / "config.json").write_text(json.dumps({"worker": {"name": "mini", "key": "k", "capacity": 12}}))
    assert conversation.agents(lambda path: state)["limit"] == 10

    def down(path):
        raise OSError("连不上")
    broken = conversation.agents(down)
    assert broken["running"] == 0 and broken["agents"] == [] and "读不到 agent-autoloop" in broken["error"]


def test_batch_handoff_records_prs_and_issues_once_and_triggers_once():
    nodes = {"acme/orders#5": thread(ASKED), "acme/kb#2": thread(said("alice", "2026-11-16T08:00:00Z", "链接挂了"), number=2, repo="acme/kb"),
             "acme/orders#4": thread(number=4, state="CLOSED"), "acme/orders#7": {"__typename": "PullRequest", "number": 7}}
    reads, pr_reads, triggers = [], [], []

    def read(keys):
        reads.append(list(keys))
        return {k: nodes.get(k.lower()) for k in keys}

    def read_prs(keys):
        pr_reads.append(list(keys))
        return {k: pr_node(THEIRS, repo="acme/orders") for k in keys}
    how = dict(read=read, read_prs=read_prs, trigger=lambda *a: triggers.append(a) or "routed",
               state={"pr": ({}, ""), "issue": ({}, "")})
    out = conversation.handoff(PROJECTS, ["acme/orders#5", "ACME/Orders#5", "acme/kb#2", "acme/orders#7", "acme/orders#4", "acme/orders#99",
                                     "else/x#3", "acme/old#1"], at=NOW, **how)
    assert reads == [["acme/orders#5", "acme/kb#2", "acme/orders#7", "acme/orders#4", "acme/orders#99"]] and pr_reads == [["acme/orders#7"]]
    assert [(h["kind"], h["subject"]) for h in out["handed"]] == [("pr", "acme/orders#7"), ("issue", "acme/orders#5"), ("issue", "acme/kb#2")]
    assert {s["subject"]: s["reason"] for s in out["skipped"]} == {
        "else/x#3": "不在登记的项目仓库里", "acme/old#1": "不在登记的项目仓库里",  # 没登记、归档的项目都不收
        "acme/orders#4": "issue 已经关闭", "acme/orders#99": "读不到这条 issue 或 PR"}
    assert len(triggers) == 1 and triggers[0][0] == "issue-feedback" and out["routed"] == "routed"
    assert sorted(agent_watch.load_handoffs(agent_watch.pr_ledger_path())) == ["acme/orders#7"]
    book = agent_watch.load_handoffs(agent_watch.issue_feedback_ledger_path())
    assert sorted(book) == ["acme/kb#2", "acme/orders#5"] and book["acme/orders#5"]["their_at"] == "2026-11-16T10:00:00Z"
    detail = book["acme/orders#5"]["detail"]
    assert "仓库：acme/orders" in detail and "issue：https://github.com/acme/orders/issues/5" in detail and "标题：issue 5" in detail
    assert "本地目录：/work/orders" in detail and "【新】boss 2026-11-16 10:00：退款口径用哪一版？" in detail
    assert "gh issue view 5 --repo acme/orders --comments" in detail and len(detail) <= agent_watch.DETAIL_LIMIT
    assert "本地目录：\n" in book["acme/kb#2"]["detail"]

    with pytest.raises(ValueError, match="已经交给 Agent"):  # 再点一次：都还在派，一条不记、不触发
        conversation.handoff(PROJECTS, ["acme/orders#5", "acme/orders#7"], at=NOW, **how)
    assert len(triggers) == 1 and book == agent_watch.load_handoffs(agent_watch.issue_feedback_ledger_path())

    events = agent_watch.issue_feedback_events(at=NOW)  # 监看把它们作为 issues.feedback 交给中台，不读 GitHub
    assert [(e["id"], e["type"], e["subject"]) for e in events] == [
        ("issue-feedback:acme/kb#2@20261117T120000+0000", "issues.feedback", "acme/kb#2"),
        ("issue-feedback:acme/orders#5@20261117T120000+0000", "issues.feedback", "acme/orders#5")]
    receipt = agent_watch.with_prs(agent_watch.NOTHING + agent_watch.IDLE, [], agent_watch.pr_events(at=NOW), feedback=events)
    assert receipt.startswith("派出 1 条 PR 反馈：acme/orders#7；派出 2 条 issue 留言：acme/kb#2、acme/orders#5；")
    assert receipt.count('"type": "issues.feedback"') == 2 and receipt.count('"type": "prs.feedback"') == 1


def test_handoff_takes_every_registered_repo_and_marks_upstream_ones():
    """回复写成草稿、你确认后才发：「帮我回复」「处理并回复」自己的仓库和上游仓库都收；
    上游仓库的 detail 第一行写明范围，技能照它走上游流程（fork 后开 PR，不合并）。"""
    projects = PROJECTS + [{"id": "up", "name": "上游", "group": "开源", "github": "up/x", "issue_scope": "mine"}]
    nodes = {"luochen211/app#3": thread(ASKED, number=3, repo="luochen211/app"),
             "up/x#9": thread(said("dev", "2026-11-16T08:00:00Z", "Could you check this?"), number=9, repo="up/x")}
    triggers = []
    out = conversation.handoff(projects, ["luochen211/app#3", "up/x#9"], mode="reply", at=NOW,
                          read=lambda keys: {k: nodes.get(k.lower()) for k in keys}, read_prs=lambda keys: {},
                          trigger=lambda *a: triggers.append(a) or "routed", state={"issue": ({}, "")})
    assert [(h["kind"], h["subject"]) for h in out["handed"]] == [("issue", "luochen211/app#3"), ("issue", "up/x#9")]
    assert out["skipped"] == [] and len(triggers) == 1
    book = agent_watch.load_handoffs(agent_watch.issue_feedback_ledger_path())
    assert book["luochen211/app#3"]["mode"] == "reply" and book["up/x#9"]["mode"] == "reply"
    assert not book["luochen211/app#3"]["detail"].startswith("范围：")  # 自己的仓库：在原仓库开分支开 PR
    assert book["up/x#9"]["detail"].startswith(agent_watch.UPSTREAM_SCOPE)  # 上游仓库：按上游流程
    events = {e["subject"]: e for e in agent_watch.issue_feedback_events(at=NOW)}
    assert events["up/x#9"]["detail"].splitlines()[:2] == [reply_mode.line("reply"), agent_watch.UPSTREAM_SCOPE]  # 模式在先，范围紧跟


@pytest.mark.parametrize("items", [[], "acme/orders#5", ["acme/orders"], ["acme/orders#0"], [f"acme/orders#{n}" for n in range(1, 42)], [5]])
def test_handoff_rejects_bad_input(items):
    with pytest.raises(ValueError):
        conversation.handoff(PROJECTS, items, read=lambda keys: pytest.fail("不该读 GitHub"), trigger=lambda *a: pytest.fail("不该触发"))


def test_double_click_from_two_requests_records_one_handoff():
    gate, results, triggers = threading.Barrier(2), [], []

    def read(keys):
        gate.wait(timeout=5)  # 两个请求都读完 GitHub 再去抢账本
        return {k: thread(ASKED) for k in keys}

    def click():
        try:
            results.append(conversation.handoff(PROJECTS, ["acme/orders#5"], read=read, trigger=lambda *a: triggers.append(a) or "",
                                           state={"issue": ({}, "")}, at=NOW))
        except ValueError as exc:
            results.append(str(exc))
    threads = [threading.Thread(target=click) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(type(r).__name__ for r in results) == ["dict", "str"] and len(triggers) == 1
    assert "已经交给 Agent" in next(r for r in results if isinstance(r, str))


def test_handoff_prunes_week_old_records_keeps_them_when_the_hub_is_down_and_stops_on_rate_limit(fresh):
    write_ledger({"acme/orders#1": {"handed_at": "2026-11-01T09:00:00+08:00", "title": "旧的", "url": "", "detail": ""}})

    def down(*args):
        raise RuntimeError("中台没开")
    out = conversation.handoff(PROJECTS, ["acme/orders#5"], read=lambda keys: {k: thread(ASKED) for k in keys}, trigger=down,
                          state={"issue": ({}, "")}, at=NOW)
    assert "监看闸门" in out["error"] and sorted(agent_watch.load_handoffs(agent_watch.issue_feedback_ledger_path())) == ["acme/orders#5"]

    def limited(keys):
        raise github.RateLimited(9e9, "secondary rate limit")
    with pytest.raises(ValueError, match="这次没交给"):
        conversation.handoff(PROJECTS, ["acme/kb#2"], read=limited, trigger=lambda *a: pytest.fail("不该触发"))

    (fresh / "rules.json").write_text(json.dumps(RULES, ensure_ascii=False))  # 还没 install：没有「处理 issue 留言」
    with pytest.raises(ValueError, match="agent_watch install"):
        conversation.handoff(PROJECTS, ["acme/kb#2"], read=lambda keys: {k: thread(ASKED, number=2, repo="acme/kb") for k in keys},
                        trigger=lambda *a: pytest.fail("不该触发"), state={"issue": ({}, "")}, at=NOW)


def test_read_threads_batches_repositories_through_the_shared_exit(monkeypatch):
    calls = []

    def graphql(query, kind="graphql", partial=False, **variables):
        calls.append((query, kind, partial))
        return {"r0": {"i5": thread(ASKED), "i7": {"__typename": "PullRequest", "number": 7}}, "r1": None}
    monkeypatch.setattr(github, "graphql", graphql)
    out = conversation.read_threads(["acme/orders#5", "acme/orders#7", "acme/kb#2", "不是编号"])
    assert out["acme/orders#5"]["title"] == "issue 5" and out["acme/orders#7"]["__typename"] == "PullRequest" and out["acme/kb#2"] is None
    assert len(calls) == 1 and calls[0][1:] == ("graphql:issue-feedback", True)
    assert 'repository(owner: "acme", name: "orders")' in calls[0][0] and "i5: issueOrPullRequest(number: 5)" in calls[0][0]
    assert "comments(last: 40)" in calls[0][0] and "bodyText" in calls[0][0]


def test_install_adds_the_rule_the_agent_with_its_model_and_raises_parallel_once(fresh, tmp_path):
    rules = fresh / "rules.json"
    rules.write_text(json.dumps(RULES, ensure_ascii=False))
    for old in fresh.glob("rules.json.bak-*"):
        old.unlink()
    message = agent_watch.install(agent_watch.ROOT)
    data = json.loads(rules.read_text())
    named = {r["name"]: r for r in data["rules"]}
    assert [r["name"] for r in data["rules"]][2:5] == ["处理 issue", "处理 PR 反馈", "处理 issue 留言"]
    assert named["处理 issue 留言"] == {"name": "处理 issue 留言", "when": {"source": "github", "types": ["issues.feedback"]},
                                     "to": "claude-feedback", "cwd": "/Volumes/Luochen", "skill": "handle-issue-feedback", "parallel": 10}
    assert named["处理 PR 反馈"]["parallel"] == 10 and named["处理 PR 反馈"]["to"] == "claude-feedback"
    assert named["处理 issue"]["parallel"] == 1 and named["处理 issue"]["to"] == "claude-issue"  # 别的规则不动
    assert data["agents"]["claude-feedback"] == {"name": "Claude Code（处理 issue）（反馈）", "worker": "mini", "use": "claude",
                                                 "access": "full", "model": "claude-opus-5-5"}
    assert "model" not in data["agents"]["claude-issue"]
    for name in agent_watch.WATCH_RULES:
        assert named[name]["emits"]["types"] == ["issues.opened", "prs.feedback", "issues.feedback", "learning.next"]
    for said_ in ("新增规则「处理 issue 留言」", "「处理 PR 反馈」并行 1 → 10", "「处理 PR 反馈」改用 Agent claude-feedback",
                  "加了 Agent claude-feedback（模型 claude-opus-5-5）", "可发 issues.feedback", "原文件备份在"):
        assert said_ in message
    backups = list(fresh.glob("rules.json.bak-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == RULES
    link = tmp_path / "user/.claude/skills/handle-issue-feedback"
    assert link.is_symlink() and link.resolve() == (agent_watch.ROOT / "personal/skills/handle-issue-feedback").resolve()
    assert agent_watch.issue_feedback_rule_missing() == "" and agent_watch.pr_rule_missing() == ""

    again = agent_watch.install(agent_watch.ROOT)
    assert "rules.json：" not in again and len(list(fresh.glob("rules.json.bak-*"))) == 1  # 第二次没有改动

    # 已经更高的 parallel、改过的模型、另外指定的 Agent 都不动；调低了的再调回来
    data["agents"]["claude-feedback"]["model"] = "claude-other"
    data["agents"]["mine"] = {"use": "claude"}
    named = {r["name"]: r for r in data["rules"]}
    named["处理 PR 反馈"].update(parallel=12, to="mine")
    named["处理 issue 留言"]["parallel"] = 2
    rules.write_text(json.dumps(data, ensure_ascii=False))
    note = agent_watch.ensure_rules()
    named = {r["name"]: r for r in json.loads(rules.read_text())["rules"]}
    assert named["处理 PR 反馈"]["parallel"] == 12 and named["处理 PR 反馈"]["to"] == "mine"
    assert named["处理 issue 留言"]["parallel"] == 10 and "「处理 issue 留言」并行 2 → 10" in note and "处理 PR 反馈" not in note
    assert json.loads(rules.read_text())["agents"]["claude-feedback"]["model"] == "claude-other"


def test_parallel_is_left_alone_when_the_rule_shares_a_group_and_capacity_is_reported(fresh):
    data = json.loads(json.dumps(RULES))
    data["rules"][3]["group"] = "处理 issue"  # 和「处理 issue」同组：同组的 parallel 要一致，不单改一条
    (fresh / "rules.json").write_text(json.dumps(data, ensure_ascii=False))
    note = agent_watch.ensure_rules()
    named = {r["name"]: r for r in json.loads((fresh / "rules.json").read_text())["rules"]}
    assert named["处理 PR 反馈"]["parallel"] == 1 and "「处理 PR 反馈」和别的规则同组" in note
    assert named["处理 issue 留言"]["parallel"] == 10

    assert agent_watch.capacity_note() == "" and agent_watch.worker_capacity() is None  # 本机没有接单程序的配置
    (fresh / "config.json").write_text(json.dumps({"worker": {"name": "mini", "key": "k"}}))
    assert agent_watch.worker_capacity() == 2
    assert "worker.capacity" in agent_watch.capacity_note() and "最多同时跑 2 轮" in agent_watch.install(agent_watch.ROOT)
    (fresh / "config.json").write_text(json.dumps({"worker": {"name": "mini", "key": "k", "capacity": 10}}))
    assert agent_watch.capacity_note() == ""
    assert json.loads((fresh / "config.json").read_text())["worker"] == {"name": "mini", "key": "k", "capacity": 10}  # install 不改它
