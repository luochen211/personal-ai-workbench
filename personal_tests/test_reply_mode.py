"""「待你回」的两种交给：帮我回复（只写回复草稿）和处理并回复；每行的建议（只需回复 / 需要处理）。"""
from pathlib import Path

import pytest

from personal import agent_watch, issues, pr_feedback, reply_drafts, reply_mode, conversation
from personal_tests.test_pr_feedback import comment, node as pr_node, review
from personal_tests.test_conversation import NOW, PROJECTS, fresh, said, thread  # noqa: F401  fresh 是自动用的夹具

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("text, kind, mode", [
    ("这个接口返回的金额是分还是元？", "", "reply"),  # 提问
    ("退款口径用哪一版？回 1A 或 1B", "", "reply"),  # 让你选
    ("改成按天算还是按月算？", "", "reply"),  # 带「改成」，但在问选哪个
    ("为什么这里要改成同步调用？", "", "reply"),  # 问为什么，要个说法
    ("登录页点了没反应，控制台报错 500", "", "handle"),  # 修 bug
    ("麻烦把导出按钮加上，再补一个测试", "", "handle"),  # 补功能
    ("Please rename this.", "", "handle"),
    ("Could you add a test for the empty case?", "", "handle"),
    ("Looks fine overall", "changes_requested", "handle"),  # 评审要求修改
    ("Suggested change\n-  x = 1\n+  x = 2", "inline", "handle"),  # 带代码建议的行内评论
    ("这里用常量", "inline", "handle"),  # 行内修改意见
    ("这里为什么是 3？", "inline", "reply"),  # 行内只是在问
    ("补充 Cloudflare DNS 的检查结果：A 记录指向现有服务器", "", "reply"),  # 「补充」在汇报，不是要你补
    ("麻烦把导出新增一列金额", "", "handle"),
    ("辛苦了，我们这边周五看一下", "", "reply"),  # 判不准：默认只需回复
    ("", "", "reply"),
])
def test_each_comment_gets_a_suggestion(text, kind, mode):
    assert reply_mode.judge(text, kind)[0] == mode


def test_one_request_to_change_makes_the_row_need_handling():
    asked = reply_mode.hint([{"who": "boss", "text": "这个怎么处理？"}])
    assert asked == {"mode": "reply", "label": "只需回复", "why": "boss 在提问"}
    both = reply_mode.hint([{"who": "boss", "text": "这个怎么处理？"}, {"who": "alice", "text": "顺便把链接修一下"}])
    assert both["mode"] == "handle" and both["label"] == "需要处理" and both["why"].startswith("alice 要求改动")
    assert reply_mode.hint([])["mode"] == "reply"


def test_pr_rows_suggest_from_the_new_comments_and_reviews():
    asked = pr_feedback.assess(pr_node(comment("boss", "2026-11-12T10:00:00Z", "这个默认值是怎么定的？")))
    assert asked["hint"]["mode"] == "reply"
    changes = pr_feedback.assess(pr_node(review("boss", "2026-11-12T10:00:00Z", state="CHANGES_REQUESTED", text="见行内")))
    assert changes["hint"] == {"mode": "handle", "label": "需要处理", "why": "boss 评审要求修改"}
    inline = pr_feedback.assess(pr_node(review("boss", "2026-11-12T10:00:00Z", inline=[("boss", "2026-11-12T10:00:00Z", "这里抽成函数")])))
    assert inline["hint"]["mode"] == "handle" and "行内修改意见" in inline["hint"]["why"]
    # 你在那之后回过：只看你最后一次动作之后的；没有新的看对方最后一句
    old = pr_feedback.assess(pr_node(review("boss", "2026-11-12T10:00:00Z", state="CHANGES_REQUESTED", text="改"),
                                     comment("luochen211", "2026-11-12T11:00:00Z", "改好了"),
                                     comment("boss", "2026-11-12T12:00:00Z", "这个默认值是怎么定的？")))
    assert old["hint"]["mode"] == "reply"


def test_issue_rows_suggest_from_new_comments_or_the_body():
    assert conversation.assess(thread(said("boss", "2026-11-16T10:00:00Z", "退款口径用哪一版？")))["hint"]["mode"] == "reply"
    assert conversation.assess(thread(said("boss", "2026-11-16T10:00:00Z", "导出的表少了一列，补上")))["hint"]["mode"] == "handle"
    body = {**thread(), "bodyText": "订单页打开就崩溃"}  # 别人开的、只有正文
    assert conversation.assess(body)["hint"] == {"mode": "handle", "label": "需要处理", "why": "boss 要求改动：「崩溃」"}


def test_reply_mode_goes_from_the_handoff_to_the_ledger_and_the_dispatch():
    nodes = {"acme/orders#5": thread(said("boss", "2026-11-16T10:00:00Z", "退款口径用哪一版？")),
             "acme/orders#7": {"__typename": "PullRequest", "number": 7}}
    how = dict(read=lambda keys: {k: nodes.get(k.lower()) for k in keys},
               read_prs=lambda keys: {k: pr_node(comment("boss", "2026-11-12T10:00:00Z", "为什么用这个库？"), repo="acme/orders") for k in keys},
               trigger=lambda *a: "routed", state={"pr": ({}, ""), "issue": ({}, "")})
    out = conversation.handoff(PROJECTS, ["acme/orders#5", "acme/orders#7"], mode="reply", at=NOW, **how)
    assert len(out["handed"]) == 2
    issue_book = agent_watch.load_handoffs(agent_watch.issue_feedback_ledger_path())
    pr_book = agent_watch.load_handoffs(agent_watch.pr_ledger_path())
    assert issue_book["acme/orders#5"]["mode"] == "reply" and pr_book["acme/orders#7"]["mode"] == "reply"
    assert len(issue_book["acme/orders#5"]["detail"]) <= agent_watch.FEEDBACK_DETAIL_LIMIT
    for event in agent_watch.issue_feedback_events(at=NOW) + agent_watch.pr_events(at=NOW):
        first = event["detail"].split("\n", 1)[0]
        assert first == reply_mode.LINES["reply"] and "不改代码" in first and "不推送" in first
        assert len(event["detail"]) <= agent_watch.DETAIL_LIMIT
    with pytest.raises(ValueError, match="模式写法不对"):
        conversation.handoff(PROJECTS, ["acme/orders#5"], mode="ship", at=NOW, **how)


def test_no_mode_means_handle_and_old_records_keep_their_old_dispatch():
    assert reply_mode.mode_of(None) == reply_mode.mode_of("") == "handle"
    stamp = NOW.isoformat(timespec="seconds")
    events = agent_watch.ledger_events({"o/r#1": {"handed_at": stamp, "detail": "仓库：o/r"},
                                        "o/r#2": {"handed_at": stamp, "detail": "仓库：o/r", "mode": "handle"}}, "pr:", "prs.feedback", NOW)
    assert events[0]["detail"] == "仓库：o/r"  # 旧记录没写模式：不加这一行，技能按处理并回复走
    assert events[1]["detail"] == reply_mode.LINES["handle"] + "\n仓库：o/r"


def test_reply_only_refuses_issues_on_the_code_flow(monkeypatch):
    rows = [{"subject": "acme/orders#5", "repo": "acme/orders", "number": 5, "title": "t", "url": "u", "stage": "未交给 Agent", "stalled": "",
             "next": {"key": "reply", "action": "feedback", "bucket": "reply"}},
            {"subject": "luochen211/app#3", "repo": "luochen211/app", "number": 3, "title": "t", "url": "u", "stage": "未交给 Agent", "stalled": "",
             "next": {"key": "todo", "action": "issue", "bucket": "todo"}}]
    monkeypatch.setattr(issues, "view", lambda projects, data=None: {"issues": rows})
    monkeypatch.setattr(agent_watch, "handoff", lambda *a, **k: pytest.fail("只回复不该打标签"))
    fed = []
    monkeypatch.setattr(conversation, "handoff", lambda projects, items, mode=None, **_: fed.append((items, mode)) or {
        "handed": [{"subject": s, "kind": "issue", "handed_at": "x"} for s in items], "skipped": [], "routed": "", "error": ""})
    out = {r["subject"]: r for r in issues.batch_handoff(PROJECTS, ["acme/orders#5", "luochen211/app#3"], mode="reply")["results"]}
    assert out["acme/orders#5"]["ok"] and fed == [(["acme/orders#5"], "reply")]
    assert not out["luochen211/app#3"]["ok"] and "处理并回复" in out["luochen211/app#3"]["error"]
    with pytest.raises(ValueError, match="处理并回复"):
        issues.handoff(PROJECTS, "luochen211/app", 3, mode="reply")


def test_regenerate_hands_off_again_in_the_same_mode(tmp_path, monkeypatch):
    with agent_watch.handoff_ledger(agent_watch.pr_ledger_path()) as book:
        book["o/r#1"] = {"handed_at": NOW.isoformat(), "mode": "reply"}
    assert reply_drafts.last_mode(pr_feedback.ledger(), "O/R#1") == "reply"
    assert reply_drafts.last_mode({}, "o/r#1") is None
    seen = []
    monkeypatch.setattr(pr_feedback, "handoff", lambda projects, keys, mode=None, **_: seen.append(mode) or {"handed": keys})
    reply_drafts.save({"subject": "o/r#1", "kind": "pr", "work": "", "replies": [{"to": "boss", "body": "回答"}]},
                      data=tmp_path, read=lambda *a: "")
    reply_drafts.regenerate([], "o/r#1", data=tmp_path)
    assert seen == ["reply"]


@pytest.mark.parametrize("skill", ["handle-issue-feedback", "handle-pr-feedback"])
def test_skills_spell_out_the_reply_only_mode(skill):
    text = (ROOT / "personal/skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    assert "模式：只回复" in text and "处理并回复" in text
    for banned in ("不改代码", "不建工作区", "不推送", "不开 PR", "不关"):
        assert banned in text, banned
    assert "没有这一行" in text  # 旧记录没写模式：按处理并回复
