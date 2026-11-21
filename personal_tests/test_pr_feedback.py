import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from personal import agent_watch, github, issues, pr_feedback

ME = "luochen211"
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
PROJECTS = [{"id": "mine", "name": "我的", "github": "luochen211/app", "repo_path": "/work/app"},
            {"id": "up", "name": "上游", "github": "up/x", "issue_scope": "mine"},
            {"id": "old", "name": "归档的", "github": "up/old", "issue_scope": "mine", "archived": True}]


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
    (tmp_path / "hub/rules.json").write_text(json.dumps(INSTALLED, ensure_ascii=False))  # 已经 install 过的中台
    yield
    github.reset()
    pr_feedback.reset()


def actor(login, typename="User"):
    return {"__typename": typename, "login": login}


def comment(login, at, text="请改一下", typename="User", association="MEMBER"):
    return {"__typename": "IssueComment", "author": actor(login, typename), "authorAssociation": association, "bodyText": text, "createdAt": at,
            "url": f"https://github.com/up/x/pull/7#issuecomment-{at[-3:-1]}"}


def review(login, at, state="COMMENTED", text="", inline=(), association="MEMBER", published=None):
    return {"__typename": "PullRequestReview", "author": actor(login), "authorAssociation": association, "bodyText": text, "state": state,
            "submittedAt": at, "url": "https://github.com/up/x/pull/7#pullrequestreview-1",
            "comments": {"nodes": [{"author": actor(who), "authorAssociation": association, "bodyText": body, "createdAt": when,
                                    "publishedAt": published, "url": "", "path": "a.py"} for who, when, body in inline]}}


def commit(login, at):
    return {"__typename": "PullRequestCommit", "commit": {"oid": "abcdef1234", "committedDate": at,
                                                          "author": {"user": {"login": login}}, "committer": {"user": None}}}


def event(kind, login, at):
    return {"__typename": kind, "actor": actor(login), "createdAt": at}


def node(*items, number=7, repo="up/x", author=ME, state="OPEN", **fields):
    return {"number": number, "title": f"PR {number}", "url": f"https://github.com/{repo}/pull/{number}", "state": state,
            "isDraft": False, "reviewDecision": "CHANGES_REQUESTED", "updatedAt": "2026-11-13T00:00:00Z",
            "headRefName": "fix/thing", "baseRefName": "main", "isCrossRepository": True,
            "headRepository": {"nameWithOwner": f"{ME}/x"}, "repository": {"nameWithOwner": repo},
            "author": {"login": author}, "timelineItems": {"nodes": list(items)}, **fields}


THEIRS = comment("maintainer", "2026-11-12T10:00:00Z", "Please rename this.")


def test_a_reply_after_my_last_action_is_pending():
    out = pr_feedback.assess(node(comment(ME, "2026-11-12T09:00:00Z", "done"), THEIRS))
    assert out["pending"] and out["new"] == 1 and out["their_at"] == "2026-11-12T10:00:00Z"
    assert out["said"][0]["who"] == "maintainer" and out["said"][0]["text"] == "Please rename this."


@pytest.mark.parametrize("mine", [
    comment(ME, "2026-11-12T11:00:00Z", "改好了"),
    review(ME, "2026-11-12T11:00:00Z", text="见下"),
    review(ME, "2026-11-12T09:00:00Z", inline=[(ME, "2026-11-12T11:00:00Z", "已改")]),  # 行内线程里的回复
    commit(ME, "2026-11-12T11:00:00Z"),
    event("ConvertToDraftEvent", ME, "2026-11-12T11:00:00Z"),
    event("ReadyForReviewEvent", ME, "2026-11-12T11:00:00Z"),
], ids=["comment", "review", "inline-reply", "commit", "draft", "ready"])
def test_any_later_action_of_mine_means_i_already_answered(mine):
    out = pr_feedback.assess(node(THEIRS, mine))
    assert not out["pending"] and out["mine_at"] == "2026-11-12T11:00:00Z"
    assert out["said"][0]["who"] == "maintainer" and not out["said"][0]["new"]  # 仍给人看对方最后一句


def test_referencing_the_pr_elsewhere_is_not_an_answer():
    assert pr_feedback.assess(node(THEIRS, event("CrossReferencedEvent", ME, "2026-11-12T11:00:00Z")))["pending"]
    assert "CROSS_REFERENCED_EVENT" not in pr_feedback.PR_FIELDS


def test_inline_notes_count_from_when_the_review_was_submitted():
    # 评审人先起草行内意见（10:00），我 11:00 推送，评审人 12:00 交了一个空正文的 COMMENTED 评审
    drafted = review("m", "2026-11-12T12:00:00Z", inline=[("m", "2026-11-12T10:00:00Z", "rename this")])
    out = pr_feedback.assess(node(commit(ME, "2026-11-12T11:00:00Z"), drafted))
    assert out["pending"] and out["their_at"] == "2026-11-12T12:00:00Z"
    published = review("m", "2026-11-12T12:00:00Z", inline=[("m", "2026-11-12T10:00:00Z", "x")], published="2026-11-12T12:00:05Z")
    assert pr_feedback.assess(node(commit(ME, "2026-11-12T11:00:00Z"), published))["their_at"] == "2026-11-12T12:00:05Z"


def test_replies_carry_who_is_a_maintainer():
    passer = comment("passerby", "2026-11-12T12:00:00Z", "Please close this.", association="NONE")
    asked = comment("guest", "2026-11-12T12:30:00Z", "One question.", association="CONTRIBUTOR")
    pr = node(THEIRS, passer, asked, reviewRequests={"nodes": [{"requestedReviewer": {"__typename": "User", "login": "guest"}}]})
    out = pr_feedback.assess(pr)
    assert out["pending"] and out["new"] == 3  # 路人的话照样列出
    roles = {s["who"]: (s["maintainer"], s["role"]) for s in out["said"]}
    assert roles == {"maintainer": (True, "组织成员"), "passerby": (False, "路人"), "guest": (True, "贡献者，被请求的评审人")}
    detail = pr_feedback.build_detail(pr, out, "")
    assert "passerby（非维护者：路人）" in detail and "maintainer（维护者：组织成员）" in detail
    assert "gh api --paginate repos/up/x/pulls/7/comments" in detail


@pytest.mark.parametrize("bot", [
    comment("coderabbitai", "2026-11-12T12:00:00Z", "Summary", typename="Bot"),
    comment("netlify[bot]", "2026-11-12T12:00:00Z", "Deploy preview"),
    comment("codecov", "2026-11-12T12:00:00Z", "Coverage"),
    comment("github-actions", "2026-11-12T12:00:00Z", "Lint"),
    comment("vercel", "2026-11-12T12:00:00Z", "Preview"),
    comment("claude", "2026-11-12T12:00:00Z", "Review"),
])
def test_bot_comments_are_not_replies(bot):
    out = pr_feedback.assess(node(THEIRS, comment(ME, "2026-11-12T11:00:00Z"), bot))
    assert not out["pending"] and out["their_at"] == "2026-11-12T10:00:00Z"


def test_mailed_comments_from_outsiders_are_not_replies_but_a_maintainer_mail_is():
    mine = comment(ME, "2026-11-12T11:00:00Z")
    auto = {**comment("subscriber", "2026-11-12T12:00:00Z", "这是来自QQ邮箱的假期自动回复邮件。", association="NONE"), "createdViaEmail": True}
    out = pr_feedback.assess(node(THEIRS, mine, auto))
    assert not out["pending"] and out["their_at"] == "2026-11-12T10:00:00Z"
    typed = comment("subscriber", "2026-11-12T12:00:00Z", "这里有个问题", association="NONE")
    assert pr_feedback.assess(node(mine, typed))["pending"]  # 网页上写的照常算
    mailed = {**comment("m", "2026-11-12T12:00:00Z", "请改一下"), "createdViaEmail": True}
    assert pr_feedback.assess(node(mine, mailed))["pending"]  # 维护者用邮件回的照常算


def test_bare_approval_is_not_a_reply_but_requested_changes_and_inline_notes_are():
    assert not pr_feedback.assess(node(review("m", "2026-11-12T12:00:00Z", "APPROVED")))["pending"]
    assert pr_feedback.assess(node(review("m", "2026-11-12T12:00:00Z", "APPROVED", "LGTM, one nit")))["pending"]
    assert pr_feedback.assess(node(review("m", "2026-11-12T12:00:00Z", "CHANGES_REQUESTED")))["pending"]
    inline = pr_feedback.assess(node(review("m", "2026-11-12T12:00:00Z", inline=[("m", "2026-11-12T12:00:00Z", "typo")])))
    assert inline["pending"] and inline["said"][0]["what"] == "行内意见" and inline["said"][0]["path"] == "a.py"
    pending = review("m", None, "PENDING", "draft")
    assert not pr_feedback.assess(node(pending))["pending"]


@pytest.mark.parametrize("text, expected", [
    ("结论\n```agent-autoloop\n{}\n```\nRESULT: PUSHED=abc1234", {"kind": "pushed", "commit": "abc1234"}),
    ("结论\nRESULT: CLOSED\n\n", {"kind": "closed"}),
    ("结论\nRESULT: REPLIED", {"kind": "replied"}),
    ("结论\nRESULT: NO_ACTION=Hmbown 说他们来改再合并", {"kind": "no_action", "reason": "Hmbown 说他们来改再合并"}),
    ("结论\nRESULT: BLOCKED=推送被拒，分支受保护", {"kind": "blocked", "reason": "推送被拒，分支受保护"}),
    ("结论\nRESULT: PUSHED=abc", None),  # 太短
    ("结论\nRESULT: PUSHED=ABC1234", None),
    ("结论\nRESULT: NO_ACTION=", None),
    ("结论\nRESULT: PR=34", None),  # issue 的结果行不算
    ("RESULT: CLOSED\n后面又写了一句", None),
    ("结论\n`RESULT: REPLIED`", None),
    ("", None),
])
def test_parse_result_only_trusts_an_exact_last_line(text, expected):
    assert pr_feedback.parse_result(text) == expected


def test_repo_skill_documents_exactly_the_lines_the_parser_accepts():
    text = (agent_watch.ROOT / "personal/skills/handle-pr-feedback/SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: handle-pr-feedback\n")
    for example, kind in (("RESULT: PUSHED=abc1234", "pushed"), ("RESULT: CLOSED", "closed"), ("RESULT: REPLIED", "replied"),
                          ("RESULT: NO_ACTION=Hmbown 说他们来改再合并", "no_action"), ("RESULT: BLOCKED=推送被拒，分支受保护", "blocked")):
        assert f"`{example}`" in text and pr_feedback.parse_result(example)["kind"] == kind
    assert "pr-<subject>" in text and "_待删除" in text and "--force-with-lease" in text and "review_ledger.py check" in text
    for must in ("author_association", "git worktree add --detach", "merge --ff-only FETCH_HEAD", "HEAD:<分支>",
                 "in_reply_to_id", "BLOCKED=头仓库读不到", "不是维护者，要求"):
        assert must in text
    assert text.count("gh api --paginate") >= 3 and "git worktree add /Volumes" not in text  # 不在本地分支上开工作区


def search_row(repo, number, author=ME, kind="PullRequest", updated="2026-11-13T00:00:00Z"):
    return {"type": kind, "repository": {"nameWithOwner": repo}, "number": number, "title": f"PR {number}",
            "url": f"https://github.com/{repo}/pull/{number}", "author": {"login": author}, "updatedAt": updated,
            "isDraft": False, "reviewDecision": "CHANGES_REQUESTED", "state": "OPEN"}


def run_row(rid, subject, state="done", created="2026-11-13T10:00:00+08:00", result=None):
    return {"id": rid, "rule": pr_feedback.RULE, "state": state, "created_at": created, "finished_at": created,
            "summary": "", "error": "", "result": result}


def fake_view(monkeypatch, rows, timelines, auto=None):
    monkeypatch.setattr(issues, "involved", lambda: {"open": rows, "merged": []})
    reads = []

    def read(keys):
        reads.append(sorted(keys))
        return {k: timelines.get(k) for k in keys}
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None, rule=None, parse=None: (auto or {}, ""))
    return read, reads


def test_view_lists_all_my_open_prs_with_who_spoke_last(monkeypatch):
    rows = [search_row("up/x", 7), search_row("up/x", 8), search_row("luochen211/app", 3), search_row("up/old", 1),
            search_row("up/x", 9, author="someone"), search_row("up/x", 10, kind="Issue"), search_row("else/y", 4)]
    timelines = {"up/x#7": node(THEIRS), "up/x#8": node(THEIRS, commit(ME, "2026-11-12T11:00:00Z"), number=8),
                 "luochen211/app#3": node(THEIRS, number=3, repo="luochen211/app"), "else/y#4": node(THEIRS, number=4, repo="else/y")}
    read, reads = fake_view(monkeypatch, rows, timelines)
    out = pr_feedback.view(PROJECTS, read=read, now=NOW)
    assert sorted(r["subject"] for r in out["prs"]) == ["else/y#4", "luochen211/app#3", "up/x#7", "up/x#8"]
    assert reads == [["else/y#4", "luochen211/app#3", "up/x#7", "up/x#8"]]  # 归档仓库、别人的 PR、issue 都不读
    row = next(r for r in out["prs"] if r["subject"] == "luochen211/app#3")
    assert row["stage"] == "未交给 Agent" and row["pending"] and row["local"] == "/work/app" and row["section"] == "active"
    assert row["state"] == "reply" and row["new"] == 1 and row["author"] == ME
    answered = next(r for r in out["prs"] if r["subject"] == "up/x#8")  # 我最后动的：不丢，归等对方
    assert answered["state"] == "wait" and not answered["pending"] and answered["new"] == 0 and answered["mine_at"] == "2026-11-12T11:00:00Z"
    assert next(r for r in out["prs"] if r["subject"] == "else/y#4")["project_name"] == ""  # 没登记的仓库照列

    pr_feedback.view(PROJECTS, read=read, now=NOW)
    assert len(reads) == 1  # PR 没有新动态：时间线不重读
    rows[0] = search_row("up/x", 7, updated="2026-11-13T01:00:00Z")
    github.expire("involved")
    pr_feedback.view(PROJECTS, read=read, now=NOW)
    assert reads[-1] == ["up/x#7"]


def write_ledger(entries):
    agent_watch.write_json(agent_watch.pr_ledger_path(), entries)


def test_no_action_keeps_their_word_on_you_but_does_not_dispatch_again(monkeypatch):
    """2026-11-20 起：对方在你之后说了话就是待你回；Agent 看过、说不用动，只是不再派（settled），对方又说了才放开。"""
    write_ledger({"up/x#7": {"handed_at": "2026-11-13T09:00:00+08:00", "title": "PR 7", "url": "https://github.com/up/x/pull/7",
                             "their_at": "2026-11-12T10:00:00Z", "detail": "…"}})
    auto = {"runs": {"up/x#7": run_row(5, "up/x#7", result={"kind": "no_action", "reason": "维护者说他们来改"})}}
    read, _ = fake_view(monkeypatch, [search_row("up/x", 7)], {"up/x#7": node(THEIRS)}, auto)
    row = pr_feedback.view(PROJECTS, read=read, now=NOW)["prs"][0]
    assert row["stage"] == "已处理" and row["pending"] and row["state"] == "reply" and row["new"] == 1
    assert row["settled"]["kind"] == "nothing" and row["settled"]["line"] == "维护者说他们来改"
    assert row["run"]["result"] == {"kind": "no_action", "reason": "维护者说他们来改"}

    pr_feedback.reset()
    later = comment("maintainer", "2026-11-13T05:00:00Z", "Actually, please rebase.")
    read, _ = fake_view(monkeypatch, [search_row("up/x", 7)], {"up/x#7": node(THEIRS, later)}, auto)
    row = pr_feedback.view(PROJECTS, read=read, now=NOW)["prs"][0]
    assert row["pending"] and row["section"] == "active" and row["said"][0]["text"] == "Actually, please rebase."
    assert row["settled"] is None  # 对方又说了：放开，能再交


def test_handed_pr_shows_progress_and_pushed_result_links_the_commit(monkeypatch):
    write_ledger({"up/x#7": {"handed_at": "2026-11-13T09:00:00+08:00", "title": "PR 7", "url": "https://github.com/up/x/pull/7",
                             "their_at": "2026-11-12T10:00:00Z", "detail": "…"},
                  "up/x#8": {"handed_at": "2026-11-13T09:00:00+08:00", "title": "PR 8", "url": "https://github.com/up/x/pull/8",
                             "their_at": "", "detail": "…"}})
    auto = {"runs": {"up/x#7": run_row(5, "up/x#7", state="claimed"),
                     "up/x#8": run_row(6, "up/x#8", result={"kind": "pushed", "commit": "abc1234"})}}
    read, _ = fake_view(monkeypatch, [search_row("up/x", 7)], {"up/x#7": node(THEIRS)}, auto)
    rows = {r["subject"]: r for r in pr_feedback.view(PROJECTS, read=read, now=NOW)["prs"]}
    assert rows["up/x#7"]["stage"] == "处理中" and rows["up/x#7"]["section"] == "active"
    assert not rows["up/x#8"]["open"] and rows["up/x#8"]["section"] == "recent"  # 已经不在开着的列表里，照样留在最近处理过
    assert rows["up/x#8"]["run"]["result"]["url"] == "https://github.com/up/x/pull/8/commits/abc1234"


def test_stage_follows_the_result_line():
    handed = "2026-11-13T09:00:00+08:00"
    done = lambda result: {"state": "done", "created_at": "2026-11-13T10:00:00+08:00", "result": result}
    assert pr_feedback.stage(None, [], None) == "未交给 Agent"
    assert pr_feedback.stage(None, [], handed) == "已交给 Agent"
    assert pr_feedback.stage({"state": "queued", "created_at": handed}, [], handed) == "处理中"
    for kind in ("pushed", "closed", "replied", "no_action"):
        assert pr_feedback.stage(done({"kind": kind}), [{"kind": "notice"}], handed) == "已处理"  # 通知不压着阶段
    assert pr_feedback.stage(done({"kind": "blocked", "reason": "x"}), [], handed) == "等你决定"
    assert pr_feedback.stage(done({"kind": "pushed"}), [{"kind": "ask"}], handed) == "等你决定"
    assert pr_feedback.stage(done({"kind": "unknown"}), [], handed) == "等你看"
    assert pr_feedback.stage(done({"kind": "pushed"}), [], "2026-11-13T11:00:00+08:00") == "已交给 Agent"  # 再交一次


def test_handoff_records_each_pr_once_without_labels_and_triggers_once(monkeypatch):
    nodes = {"up/x#7": node(THEIRS), "luochen211/app#3": node(THEIRS, number=3, repo="luochen211/app"),
             "up/x#9": node(THEIRS, number=9, author="someone"), "up/x#10": node(number=10, state="MERGED")}
    reads, triggers = [], []

    def read(keys):
        reads.append(list(keys))
        return {k: nodes.get(k.lower()) or nodes.get(k) for k in keys}
    result = pr_feedback.handoff(PROJECTS, ["up/x#7", "UP/X#7", "luochen211/app#3", "up/x#9", "up/x#10", "up/x#11"], read=read,
                                 trigger=lambda *a: triggers.append(a) or "routed", state=({}, ""),
                                 at=datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc))
    assert reads == [["up/x#7", "luochen211/app#3", "up/x#9", "up/x#10", "up/x#11"]]  # 去重，一次读完
    assert [h["subject"] for h in result["handed"]] == ["up/x#7", "luochen211/app#3"]
    assert {s["subject"]: s["reason"] for s in result["skipped"]} == {
        "up/x#9": "不是你开的 PR", "up/x#10": "PR 已合并或关闭", "up/x#11": "读不到这个 PR"}
    assert len(triggers) == 1 and triggers[0][0] == "pr-feedback"
    book = agent_watch.load_handoffs(agent_watch.pr_ledger_path())
    assert sorted(book) == ["luochen211/app#3", "up/x#7"]
    detail = book["luochen211/app#3"]["detail"]
    assert "PR 分支：fix/thing" in detail and "头仓库：luochen211/x（fork）" in detail and "本地目录：/work/app" in detail
    assert "本地目录：\n" in book["up/x#7"]["detail"]  # 上游仓库没有本地检出：写空，也不打标签
    assert "Please rename this." in detail and len(detail) <= agent_watch.DETAIL_LIMIT
    assert book["up/x#7"]["their_at"] == "2026-11-12T10:00:00Z"


def test_handoff_skips_prs_already_on_their_way_and_reports_when_nothing_left(monkeypatch):
    write_ledger({"up/x#7": {"handed_at": "2026-11-13T11:55:00+00:00", "title": "PR 7", "url": "", "detail": ""}})
    read = lambda keys: {k: node(THEIRS) for k in keys}
    with pytest.raises(ValueError, match="已经交给 Agent"):
        pr_feedback.handoff(PROJECTS, ["up/x#7"], read=read, trigger=lambda *a: "", state=({}, ""),
                            at=datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc))
    # 交出去超过 15 分钟还没有新的一轮：派发可能丢了，可以再交
    out = pr_feedback.handoff(PROJECTS, ["up/x#7"], read=read, trigger=lambda *a: "", state=({}, ""),
                              at=datetime(2026, 11, 13, 12, 30, tzinfo=timezone.utc))
    assert out["handed"][0]["handed_at"] == "2026-11-13T12:30:00+00:00"


@pytest.mark.parametrize("keys", [[], "up/x#7", ["up/x"], ["up/x#0"], [f"up/x#{n}" for n in range(1, 22)], ["a b/c#1"]])
def test_handoff_rejects_bad_input(keys):
    with pytest.raises(ValueError):
        pr_feedback.handoff(PROJECTS, keys, read=lambda k: pytest.fail("不该读 GitHub"), trigger=lambda *a: "", state=({}, ""))


def test_handoff_keeps_the_record_when_the_hub_is_down():
    def down(*args):
        raise RuntimeError("连不上中台")
    out = pr_feedback.handoff(PROJECTS, ["up/x#7"], read=lambda keys: {k: node(THEIRS) for k in keys}, trigger=down, state=({}, ""))
    assert "监看闸门" in out["error"] and "up/x#7" in agent_watch.load_handoffs(agent_watch.pr_ledger_path())


def test_concurrent_batches_keep_every_record(monkeypatch):
    slow = agent_watch.load_handoffs

    def load(path=None):
        data = slow(path)
        time.sleep(0.01)  # 拉大读和写之间的窗口：不加锁时会互相覆盖
        return data
    monkeypatch.setattr(agent_watch, "load_handoffs", load)
    read = lambda keys: {k: node(THEIRS, number=int(k.rpartition("#")[2])) for k in keys}
    threads = [threading.Thread(target=pr_feedback.handoff, args=(PROJECTS, [f"up/x#{n}", f"up/x#{n + 100}"]),
                                kwargs={"read": read, "trigger": lambda *a: "", "state": ({}, "")}) for n in range(1, 9)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(slow(agent_watch.pr_ledger_path())) == 16


def test_rate_limit_stops_the_handoff_and_the_timeline_reads():
    github.strike("API rate limit exceeded")
    found, note = pr_feedback.timelines({"up/x#7": "t"}, read=lambda keys: pytest.fail("限流时不该发请求"))
    assert found == {} and "限流" in note

    def limited(keys):
        raise github.RateLimited(time.time() + 60)
    with pytest.raises(ValueError, match="这次没交给"):
        pr_feedback.handoff(PROJECTS, ["up/x#7"], read=limited, trigger=lambda *a: "", state=({}, ""))
    assert agent_watch.load_handoffs(agent_watch.pr_ledger_path()) == {}


def test_read_timelines_batches_repositories_through_the_shared_exit(monkeypatch):
    calls = []

    def graphql(query, kind="graphql", **kwargs):
        calls.append((kind, query, kwargs))
        return {"r0": {"p7": node(THEIRS), "p8": None}, "r1": {"p3": node(number=3, repo="luochen211/app")}}
    monkeypatch.setattr(github, "graphql", graphql)
    out = pr_feedback.read_timelines(["up/x#7", "up/x#8", "luochen211/app#3"])
    assert len(calls) == 1 and calls[0][0] == "graphql:pr-feedback" and calls[0][2] == {"partial": True}
    for word in ("CONVERT_TO_DRAFT_EVENT", "PULL_REQUEST_COMMIT", "headRefName", "headRepository", "authorAssociation", "publishedAt", "reviewRequests"):
        assert word in calls[0][1]
    assert out["up/x#7"]["number"] == 7 and out["up/x#8"] is None and out["luochen211/app#3"]["number"] == 3


def test_detail_keeps_the_newest_replies_within_the_hub_limit():
    many = [comment("m", f"2026-11-12T{h:02d}:00:00Z", "很长的意见" * 200) for h in range(10)]
    info = pr_feedback.assess(node(*many))
    detail = pr_feedback.build_detail(node(*many), info, "")
    assert len(detail) <= agent_watch.DETAIL_LIMIT and "共 10 条" in detail and "2026-11-12 09:00" in detail
    assert "2026-11-12 00:00" not in detail


def test_watch_emits_pr_feedback_from_the_ledger_without_reading_github():
    write_ledger({"up/x#7": {"handed_at": "2026-11-13T09:00:00+08:00", "title": "PR 7", "url": "https://github.com/up/x/pull/7",
                             "detail": "仓库：up/x"},
                  "up/x#1": {"handed_at": "2026-11-01T09:00:00+08:00", "title": "旧的", "url": "", "detail": ""}})
    events = agent_watch.pr_events(at=datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc))
    assert events == [{"id": "pr:up/x#7@20261113T090000+0800", "type": "prs.feedback", "subject": "up/x#7", "summary": "PR 7",
                       "detail": "仓库：up/x", "link": "https://github.com/up/x/pull/7"}]
    receipt = agent_watch.with_prs(agent_watch.NOTHING + agent_watch.IDLE, [], events)
    assert receipt.startswith("派出 1 条 PR 反馈：up/x#7；没有新的带 agent 标签的 issue")  # 有事可做，不再以「没事可做」开头
    assert '"type": "prs.feedback"' in receipt


def test_watch_still_hands_over_prs_when_issue_search_fails(monkeypatch, capsys):
    write_ledger({"up/x#7": {"handed_at": datetime.now().astimezone().isoformat(timespec="seconds"), "title": "PR 7",
                             "url": "https://github.com/up/x/pull/7", "detail": ""}})

    def broken():
        raise RuntimeError("search 失败")
    monkeypatch.setattr(agent_watch, "watch", broken)
    monkeypatch.setattr(agent_watch, "notify", lambda title, text: None)
    assert agent_watch.main([]) == 0
    out = capsys.readouterr().out
    assert "prs.feedback" in out and "search 失败" in out


RULES = {"sources": {"github": {"name": "GitHub"}}, "agents": {"claude-issue": {"use": "claude", "access": "full"}},
         "rules": [{"name": "看带 agent 标签的 issue", "when": {"cron": "*/10 * * * *"}, "to": "issue-watch",
                    "emits": {"source": "github", "types": ["issues.opened"]}},
                   {"name": "立即看 agent issue", "when": {"source": "local", "types": ["issues.check"]}, "to": "issue-watch",
                    "emits": {"source": "github", "types": ["issues.opened"]}},
                   {"name": "处理 issue", "when": {"source": "github", "types": ["issues.opened"]}, "to": "claude-issue",
                    "cwd": "/Volumes/Luochen", "skill": "handle-issue", "parallel": 1},
                   {"name": "每日简报", "when": {"cron": "30 8 * * *"}, "to": "daily-brief"}]}


def test_install_adds_the_pr_rule_once_and_links_both_skills(tmp_path):
    rules = tmp_path / "hub/rules.json"
    rules.write_text(json.dumps(RULES, ensure_ascii=False))
    message = agent_watch.install(agent_watch.ROOT)
    data = json.loads(rules.read_text())
    names = [r["name"] for r in data["rules"]]
    assert names == ["看带 agent 标签的 issue", "立即看 agent issue", "处理 issue", "处理 PR 反馈", "处理 issue 留言", "每日简报", "写学习文章", "录音拟 issue"]
    assert data["rules"][3] == {"name": "处理 PR 反馈", "when": {"source": "github", "types": ["prs.feedback"]},
                                "to": "claude-feedback", "cwd": "/Volumes/Luochen", "skill": "handle-pr-feedback",
                                "parallel": agent_watch.FEEDBACK_PARALLEL}
    assert all(r["emits"]["types"] == ["issues.opened", "prs.feedback", "issues.feedback", "learning.next"] for r in data["rules"][:2])
    assert len(list(rules.parent.glob("rules.json.bak-*"))) == 1 and "新增规则「处理 PR 反馈」" in message
    for name in ("handle-issue", "handle-pr-feedback", "handle-issue-feedback"):
        link = tmp_path / "user/.claude/skills" / name
        assert link.is_symlink() and link.resolve() == (agent_watch.ROOT / "personal/skills" / name).resolve()
    again = agent_watch.install(agent_watch.ROOT)
    assert "rules.json：" not in again and "已链到仓库" not in again  # 第二次没有改动（这份规则缺监看 Agent，审查那部分只提示）
    assert len(list(rules.parent.glob("rules.json.bak-*"))) == 1


def test_install_leaves_rules_alone_without_a_template(tmp_path):
    rules = tmp_path / "hub/rules.json"
    rules.write_text(json.dumps({"rules": []}))
    assert "没有「处理 issue」规则" in agent_watch.install(agent_watch.ROOT)
    assert json.loads(rules.read_text()) == {"rules": []} and not list(rules.parent.glob("rules.json.bak-*"))
    rules.unlink()
    assert "没找到 rules.json" in agent_watch.install(agent_watch.ROOT)


def test_answer_only_takes_pr_feedback_questions(monkeypatch):
    calls = []
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None, rule=None, parse=None:
                        ({"waiting": {"up/x#7": [{"id": 31, "kind": "ask"}]}}, "") if rule == pr_feedback.RULE else pytest.fail(rule))
    out = pr_feedback.answer("31", {"choice": 1}, send=lambda path, body: calls.append((path, body)) or {"changed": True})
    assert calls == [("/v1/cards/31/answer", {"choice": 0})] and out["message"]
    with pytest.raises(ValueError):
        pr_feedback.answer("32", {"choice": 1}, send=lambda *a: pytest.fail("不该送"))


INSTALLED = {"rules": [{"name": "立即看 agent issue", "to": "issue-watch", "emits": {"source": "github", "types": ["issues.opened", "prs.feedback"]}},
                       {"name": "处理 PR 反馈", "when": {"source": "github", "types": ["prs.feedback"]}, "to": "claude-issue"}]}


def test_handoff_refuses_before_the_rule_is_installed(tmp_path):
    (tmp_path / "hub/rules.json").write_text(json.dumps({"rules": RULES["rules"]}, ensure_ascii=False))
    with pytest.raises(ValueError, match="agent_watch install"):
        pr_feedback.handoff(PROJECTS, ["up/x#7"], read=lambda keys: pytest.fail("没装规则时不该读 GitHub"),
                            trigger=lambda *a: pytest.fail("不该触发"), state=({}, ""))
    (tmp_path / "hub/rules.json").unlink()
    assert "rules.json" in agent_watch.pr_rule_missing()
    (tmp_path / "hub/rules.json").write_text(json.dumps(RULES, ensure_ascii=False))
    agent_watch.ensure_rules()
    assert agent_watch.pr_rule_missing() == ""


def test_double_click_from_two_requests_records_one_handoff(monkeypatch):
    slow = agent_watch.load_handoffs

    def load(path=None):
        data = slow(path)
        time.sleep(0.05)  # 两个请求都已经读完 GitHub、同时进来记账
        return data
    monkeypatch.setattr(agent_watch, "load_handoffs", load)
    results, triggers = [], []

    def hand():
        try:
            results.append(pr_feedback.handoff(PROJECTS, ["up/x#7"], read=lambda keys: {k: node(THEIRS) for k in keys},
                                               trigger=lambda *a: triggers.append(a) or "", state=({}, "")))
        except ValueError as exc:
            results.append(str(exc))
    threads = [threading.Thread(target=hand) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sum(isinstance(r, dict) for r in results) == 1 and len(triggers) == 1
    assert any(isinstance(r, str) and "已经交给 Agent" in r for r in results)


def test_old_ledger_entries_are_dropped_on_the_next_handoff():
    write_ledger({"up/x#1": {"handed_at": "2026-10-01T09:00:00+08:00", "title": "旧的", "url": "", "detail": ""}, "junk": "x"})
    pr_feedback.handoff(PROJECTS, ["up/x#7"], read=lambda keys: {k: node(THEIRS) for k in keys}, trigger=lambda *a: "", state=({}, ""),
                        at=datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc))
    assert list(agent_watch.load_handoffs(agent_watch.pr_ledger_path())) == ["up/x#7"]


def test_my_pr_nobody_answered_waits_on_them_and_one_without_a_timeline_is_left_out(monkeypatch):
    read, _ = fake_view(monkeypatch, [search_row("up/x", 7), search_row("up/x", 8)],
                        {"up/x#7": node(commit(ME, "2026-11-12T09:00:00Z"))})
    out = pr_feedback.view(PROJECTS, read=read, now=NOW)
    assert [(r["subject"], r["state"], r["their_at"]) for r in out["prs"]] == [("up/x#7", "wait", "")]
    assert "1 条开着的 PR 还没读到时间线" in out["note"]


@pytest.mark.parametrize("run, stage, state", [
    ({"state": "claimed"}, "处理中", "agent"),
    (None, "已交给 Agent", "agent"),
    ({"result": {"kind": "blocked", "reason": "缺决定"}}, "等你决定", "reply"),
    ({"result": {"kind": "unknown"}}, "等你看", "reply"),
    ({"result": {"kind": "no_action", "reason": "维护者说他们来改"}}, "已处理", "reply"),  # 对方的话你还没回（不再派，见 settled）
])
def test_state_follows_the_agent_round(monkeypatch, run, stage, state):
    write_ledger({"up/x#7": {"handed_at": "2026-11-13T19:50:00+08:00", "title": "PR 7", "url": "", "their_at": "2026-11-12T10:00:00Z"}})
    auto = {"runs": {"up/x#7": {**run_row(5, "up/x#7", created="2026-11-13T19:51:00+08:00"), **run}}} if run else {}
    read, _ = fake_view(monkeypatch, [search_row("up/x", 7)], {"up/x#7": node(THEIRS)}, auto)
    row = pr_feedback.view(PROJECTS, read=read, now=NOW)["prs"][0]
    assert (row["stage"], row["state"]) == (stage, state)
    assert row["new"] == (1 if state != "wait" else 0)  # +N 只在待你回和 Agent 处理中的行上


def test_a_lost_handoff_is_back_on_you_and_a_merged_one_is_done(monkeypatch):
    write_ledger({"up/x#7": {"handed_at": "2026-11-13T09:00:00+08:00", "title": "PR 7", "url": "", "their_at": "2026-11-12T10:00:00Z"},
                  "up/x#8": {"handed_at": "2026-11-13T09:00:00+08:00", "title": "PR 8", "url": "", "their_at": ""}})
    auto = {"runs": {"up/x#8": run_row(6, "up/x#8", result={"kind": "replied"})}}
    read, _ = fake_view(monkeypatch, [search_row("up/x", 7)], {"up/x#7": node(THEIRS)}, auto)
    rows = {r["subject"]: r for r in pr_feedback.view(PROJECTS, read=read, now=NOW)["prs"]}
    assert rows["up/x#7"]["stalled"] == "lost" and rows["up/x#7"]["state"] == "reply"
    assert not rows["up/x#8"]["open"] and rows["up/x#8"]["state"] == "done"


def test_rows_waiting_on_you_settle_once_you_act_yourself(monkeypatch):
    write_ledger({"up/x#7": {"handed_at": "2026-11-13T09:00:00+08:00", "title": "PR 7", "url": "", "their_at": "2026-11-12T10:00:00Z"},
                  "up/x#8": {"handed_at": "2026-11-13T09:00:00+08:00", "title": "PR 8", "url": "", "their_at": "2026-11-12T10:00:00Z"}})
    blocked = {"kind": "blocked", "reason": "缺决定"}
    auto = {"runs": {"up/x#7": {**run_row(5, "up/x#7", result=blocked), "finished_at": "2026-11-13T10:30:00+08:00"},
                     "up/x#8": {**run_row(6, "up/x#8", result=blocked), "finished_at": "2026-11-13T10:30:00+08:00"}}}
    timelines = {"up/x#7": node(THEIRS, comment(ME, "2026-11-13T03:00:00Z", "我来定")),  # 晚于这一轮结束（02:30Z）
                 "up/x#8": node(THEIRS, comment(ME, "2026-11-12T11:00:00Z", "早先回过"), number=8)}
    read, _ = fake_view(monkeypatch, [search_row("up/x", 7), search_row("up/x", 8)], timelines, auto)
    rows = {r["subject"]: r for r in pr_feedback.view(PROJECTS, read=read, now=NOW)["prs"]}
    assert rows["up/x#7"]["stage"] == "等你决定" and rows["up/x#7"]["by_me"] and rows["up/x#7"]["section"] == "recent"
    assert not rows["up/x#8"]["by_me"] and rows["up/x#8"]["section"] == "active"


def test_pushed_without_a_matching_action_in_the_timeline_needs_a_look(monkeypatch):
    write_ledger({"up/x#7": {"handed_at": "2026-11-13T09:00:00+08:00", "title": "PR 7", "url": "", "their_at": "2026-11-12T10:00:00Z"},
                  "up/x#8": {"handed_at": "2026-11-13T09:00:00+08:00", "title": "PR 8", "url": "", "their_at": "2026-11-12T10:00:00Z"}})
    pushed = {"kind": "pushed", "commit": "abc1234"}
    auto = {"runs": {"up/x#7": run_row(5, "up/x#7", result=pushed), "up/x#8": run_row(6, "up/x#8", result=pushed)}}
    timelines = {"up/x#7": node(THEIRS), "up/x#8": node(THEIRS, commit(ME, "2026-11-13T02:10:00Z"), number=8)}  # 这一轮 02:00Z 开始
    read, _ = fake_view(monkeypatch, [search_row("up/x", 7), search_row("up/x", 8)], timelines, auto)
    rows = {r["subject"]: r for r in pr_feedback.view(PROJECTS, read=read, now=NOW)["prs"]}
    assert rows["up/x#7"]["stage"] == "等你看" and rows["up/x#7"]["unseen"] and rows["up/x#7"]["section"] == "active"
    assert rows["up/x#8"]["stage"] == "已处理" and rows["up/x#8"]["section"] == "recent"


def test_rules_are_untouched_without_the_issue_rule_to_copy(tmp_path):
    rules = tmp_path / "hub/rules.json"
    rules.write_text(json.dumps({"rules": RULES["rules"][:2] + ["奇怪的一行"]}, ensure_ascii=False))
    before = rules.read_text()
    assert "都没加" in agent_watch.ensure_rules()
    assert rules.read_text() == before  # 没人接就不让监看发 prs.feedback
