"""收件箱里 issue 按「下一步该谁做」归类（personal/next_step.py）：每一类、规则顺序、合并后的筛选、交给接口和批量交给的把关、和 PR 行用同一个判断。"""
import json
from datetime import datetime, timezone

import pytest

from personal import agent_watch, github, issues, next_step, pr_feedback, upstream, conversation

ME = "luochen211"
TEAM = {"id": "channel", "name": "通道", "group": "合作项目", "github": "acme/channel"}
OWN = {"id": "app", "name": "工具", "group": "个人工具", "github": "luochen211/app"}
UP = {"id": "up", "name": "上游", "github": "up/x", "issue_scope": "mine"}
PROJECTS = [TEAM, OWN, UP]


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    def offline(args, kind, **kwargs):
        raise AssertionError(f"测试不该真的请求 GitHub：{args}")
    monkeypatch.setattr(github, "run", offline)
    monkeypatch.setattr(next_step, "now", lambda: datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc))  # 只有正文的按开了几天判
    github.reset()
    pr_feedback.reset()
    monkeypatch.setattr(issues, "_results", {})
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path / "hub"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    (tmp_path / "hub/issues").mkdir(parents=True)
    yield
    github.reset()
    pr_feedback.reset()


def said(login, at, text, typename="User", url=None):
    return {"__typename": "IssueComment", "author": {"__typename": typename, "login": login}, "authorAssociation": "OWNER",
            "bodyText": text, "createdAt": at, "createdViaEmail": False, "url": url or f"https://github.com/c#{at[8:13]}"}


def ref(number, state="OPEN", closes=False):
    return {"__typename": "CrossReferencedEvent", "willCloseTarget": closes,
            "source": {"__typename": "PullRequest", "number": number, "title": f"PR {number}", "url": f"https://github.com/x/pull/{number}",
                       "state": state}}


def node(*items, number=64, author=ME, body="正文", created="2026-11-10T00:00:00Z", repo="acme/channel"):
    """列表查询里的 issue 节点（upstream.ISSUE 的形状）。"""
    return {"number": number, "title": f"issue {number}", "url": f"https://github.com/{repo}/issues/{number}", "createdAt": created,
            "updatedAt": "2026-11-18T00:00:00Z", "bodyText": body, "author": {"__typename": "User", "login": author},
            "labels": {"nodes": []}, "comments": {"totalCount": sum(i["__typename"] == "IssueComment" for i in items)},
            "timelineItems": {"nodes": list(items)}}


def classify(*items, repo="acme/channel", flow=None, involved=False, seen=None, **fields):
    row = upstream.item(node(*items, repo=repo, **fields), repo)
    return next_step.classify(repo=repo, author=row["author"]["login"], thread=row["thread"], refs=row["refs"], flow=flow,
                              upstream=repo == "up/x", involved=involved, seen=seen)


ASK_64 = said(ME, "2026-11-18T03:02:16Z", "#64 里有一处涉及复制件，本仓不改，要你定，回“1A”“1B”或“1C”就行：\n1. drift() 只核登记里列着的文件")


def test_list_node_keeps_comments_in_the_assess_shape_and_folds_pr_references():
    row = upstream.item(node(said("boss", "2026-11-17T00:00:00Z", "x" * 3000), ref(80), ref(80, closes=True), ref(81, "MERGED"),
                             {"__typename": "CrossReferencedEvent", "willCloseTarget": False, "source": {"__typename": "Issue"}}), "acme/channel")
    assert [c["author"]["login"] for c in row["thread"]["comments"]["nodes"]] == ["boss"]
    assert len(row["thread"]["comments"]["nodes"][0]["bodyText"]) == upstream.CLIP + 1  # 截断加「…」
    assert [(r["number"], r["state"], r["closes"]) for r in row["refs"]] == [(80, "OPEN", True), (81, "MERGED", False)]
    assert conversation.assess(row["thread"])["mine_at"] == "2026-11-10T00:00:00Z"  # 消息页的函数直接吃这个形状


def test_channel_64_waits_on_the_other_side_without_a_button():
    out = classify(ASK_64)
    assert out["key"] == "wait" and out["name"] == "等对方回"
    assert out["next"] == "等 acme 回“1A”“1B”或“1C”就行"  # 没 @ 人、issue 是我开的：等仓库那边
    assert out["button"] == "" and out["action"] == "" and out["url"] == ASK_64["url"]
    assert "回“1A”" in out["why"] and out["who"] == ME


def test_each_class_and_its_button():
    flow = {"stage": "等你看", "label": "等你看", "kind": "issue", "at": "2026-11-12T16:06:01+08:00"}
    assert classify(ASK_64, flow=flow)["key"] == "flow"
    pr = classify(ASK_64, ref(80))
    assert pr["key"] == "pr" and pr["prs"][0]["number"] == 80 and pr["button"] == "" and pr["next"] == "等 PR #80 评审"
    merged = classify(ref(81, "MERGED", closes=True))
    assert merged["key"] == "close" and merged["next"] == "请验收并关闭"
    asked_to_close = classify(said(ME, "2026-11-05T05:36:00Z", "@boss 本卡已完成：PR #22 合入。请验收并关闭。"), author="boss")
    assert asked_to_close["key"] == "close" and asked_to_close["next"] == "等 boss 验收后关闭"
    body_only = classify(author="boss", body="@luochen211 这是 #28 分工里说的需求。")
    assert body_only["key"] == "reply" and body_only["button"] == "帮我回复" and body_only["action"] == "feedback"
    mine = classify(said(ME, "2026-11-09T07:03:00Z", "进展：名单下好了，下一步整理。"), repo="luochen211/app")
    assert mine["key"] == "todo" and mine["button"] == "交给 Agent" and not mine["drawer_only"] and mine["action"] == "issue"
    other = classify(said("someone", "2026-10-22T00:14:00Z", "Stage 1 PR map"), repo="luochen211/app", author="someone")
    assert other["key"] == "look" and other["button"] == "仍交给 Agent" and other["drawer_only"]


def test_rules_stop_at_the_first_match():
    flow = {"stage": "处理中", "label": "处理中", "kind": "issue", "at": None}
    assert classify(ASK_64, ref(80), flow=flow)["key"] == "flow"  # 流程中先于 PR
    assert classify(ref(80), ref(81, "MERGED", closes=True))["key"] == "pr"  # open PR 先于已合并
    theirs = said("boss", "2026-11-18T05:00:00Z", "@luochen211 1A")
    assert classify(ASK_64, ref(81, "MERGED", closes=True))["key"] == "close"  # 已合并先于等对方回
    assert classify(ASK_64, theirs, ref(81, "MERGED", closes=True))["key"] == "reply"  # 2026-11-20 起：对方在你之后说了话先于待关闭
    assert classify(ASK_64, theirs)["key"] == "reply"  # 对方回了：转待你回
    done_and_ask = said(ME, "2026-11-18T06:00:00Z", "@boss 已修好，请验收并关闭；另外回“1A”就行")
    assert classify(done_and_ask)["key"] == "close"  # 待关闭先于等对方回


def test_reply_gets_the_feedback_button_in_every_repo_and_upstream_replies_to_you_go_to_reply():
    """2026-11-18 起回复改成草稿、你确认后才发：待你回不分自己的仓库、上游仓库，都出「帮我回复」（详情里并列「处理并回复」）。"""
    theirs = said("zjucindy-ops", "2026-10-28T04:03:00Z", "补充 DNS 的检查结果")
    own = classify(said(ME, "2026-10-27T00:00:00Z", "开工"), theirs, repo="luochen211/app")
    assert own["key"] == "reply" and own["button"] == "帮我回复" and own["action"] == "feedback" and not own["drawer_only"]
    assert "去 GitHub" not in own["next"]
    team = classify(said(ME, "2026-10-27T00:00:00Z", "开工"), theirs)
    assert (team["button"], team["action"]) == ("帮我回复", "feedback")
    body_only = classify(repo="up/x", author="someone")  # 上游仓库别人开的、只有正文：待你回，同样出按钮
    assert body_only["key"] == "reply" and (body_only["button"], body_only["action"]) == ("帮我回复", "feedback")
    assert next_step.allowed(own) == ("feedback", "") and next_step.allowed(body_only) == ("feedback", "")
    up = classify(said("Hmbown", "2026-11-16T13:48:00Z", "Thank you @luochen211: all nine items are now fixed on main"), repo="up/x")
    assert up["key"] == "reply" and not up["drawer_only"]  # 2026-11-20 起：上游仓库你开的、对方在你之后说了话也归待你回
    assert up["line"].startswith(f"Hmbown {next_step.when('2026-11-16T13:48:00Z')} @了你：Thank you @luochen211:")
    aside = classify(said("Hmbown", "2026-11-16T13:48:00Z", "Fixed on main"), repo="up/x", author="someone")
    assert aside["key"] == "look" and aside["drawer_only"]  # 上游别人开的、你没说过话、没 @你：仍是待你看
    assert classify(said(ME, "2026-10-27T02:57:00Z", "Opened this"), repo="up/x")["key"] == "wait"  # 上游你开的没人回
    stranger = classify(said("someone", "2026-10-22T00:14:00Z", "PR map"), repo="luochen211/app", author="someone", involved=True)
    assert stranger["key"] == "reply"  # 与我有关（在 involves 搜索里）


@pytest.mark.parametrize("text, key", [
    ("2026-11-09 定的：暂时不做。", "look"),
    ("全国范围的行业收录先不跑，等客户到一定规模再开。", "look"),
    ("这里是不是该改成按行业拉？", "look"),
    ("收到", "look"),
    ("@octocat 开工前对一下口径：下面 7 处要你们定，回“1是 2B 3是 …”这样就行。", "wait"),
    ("@boss 进展同步一下", "wait"),
    ("用 @Transactional 包一下 save()，下一步补测试。", "todo"),  # 代码注解不算 @ 人
    ("进展：已核对接口，下一步写导入。", "todo"),
])
def test_my_last_words_decide_between_wait_todo_and_look(text, key):
    assert classify(said(ME, "2026-11-10T00:00:00Z", text), repo="luochen211/app")["key"] == key


def test_bot_reminders_and_unread_threads_are_not_work_for_the_agent():
    bot = classify(said(ME, "2026-11-10T00:00:00Z", "进展"), said("github-actions", "2026-11-11T00:00:00Z", "stale", typename="Bot"),
                   repo="luochen211/app")
    assert bot["key"] == "look" and "机器人" in bot["why"]
    unread = next_step.classify(repo="luochen211/app", author=ME, thread=None, refs=[])
    assert unread["key"] == "look"
    assert classify(repo="luochen211/app")["key"] == "todo"  # 你开的、只有正文
    assert classify(repo="luochen211/app", author="someone")["key"] == "reply"  # 别人开的、只有正文：待你回
    assert classify(repo="luochen211/app", author="dependabot[bot]")["key"] == "look"  # 机器人开的


def test_handoff_gate_routes_or_refuses_with_a_reason():
    def step(key, action=""):
        return {"key": key, "action": action}
    assert next_step.allowed(step("todo", "issue")) == ("issue", "")
    assert next_step.allowed(step("look", "issue")) == ("issue", "")
    assert next_step.allowed(step("reply", "feedback")) == ("feedback", "")
    assert next_step.allowed(step("flow", "feedback")) == ("feedback", "")
    assert next_step.allowed(step("flow", "issue")) == ("issue", "")
    for key, words in [("pr", "open PR"), ("close", "等验收关闭"), ("wait", "等对方回")]:
        route, reason = next_step.allowed(step(key))
        assert route == "" and words in reason
        assert "卡" not in reason and "仓库主人" not in reason and "维护者" not in reason


def list_row(*items, number=64, repo="acme/channel", **fields):
    return upstream.item(node(*items, number=number, repo=repo, **fields), repo)


def fake_lists(monkeypatch, own, involved=()):
    monkeypatch.setattr(issues, "search", lambda repos, branches=None: own)
    monkeypatch.setattr(issues, "involved", lambda: {"open": list(involved), "merged": []})
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None, rule=None, parse=None: ({}, ""))
    monkeypatch.setattr(issues, "feedback_state", lambda data=None: ({"runs": {}, "history": {}, "waiting": {}, "handed": {}}, ""))


def test_view_puts_the_next_step_on_every_row(monkeypatch):
    fake_lists(monkeypatch, [list_row(ASK_64), list_row(number=70, author="boss", body="@luochen211 接口说明"),
                             list_row(number=3, repo="luochen211/app")])
    out = {r["subject"]: r["next"] for r in issues.view(PROJECTS)["issues"]}
    assert {k: v["key"] for k, v in out.items()} == {"acme/channel#64": "wait", "acme/channel#70": "reply", "luochen211/app#3": "todo"}


def test_feedback_flow_folds_in_and_settled_replies_do_not(monkeypatch):
    fake_lists(monkeypatch, [list_row(ASK_64), list_row(ASK_64, number=65)])
    running = {"id": 1906, "rule": conversation.RULE, "state": "claimed", "created_at": "2026-11-18T10:00:00+08:00", "finished_at": None,
               "summary": "", "error": "", "result": None}
    replied = {**running, "id": 1907, "state": "done", "finished_at": "2026-11-18T10:30:00+08:00", "result": {"kind": "replied"}}
    monkeypatch.setattr(issues, "feedback_state", lambda data=None: (
        {"runs": {"acme/channel#64": running, "acme/channel#65": replied}, "history": {"acme/channel#64": [running]}, "waiting": {},
         "handed": {}}, ""))
    out = {r["subject"]: r for r in issues.view(PROJECTS)["issues"]}
    assert out["acme/channel#64"]["next"]["key"] == "flow" and out["acme/channel#64"]["flow"] == "feedback"
    assert out["acme/channel#64"]["stage"] == "处理中" and out["acme/channel#64"]["next"]["action"] == "feedback"
    assert out["acme/channel#65"]["next"]["key"] == "wait"  # 已回复、在等对方：不算流程中


def test_handoff_endpoint_refuses_classes_without_a_button(monkeypatch):
    fake_lists(monkeypatch, [list_row(ASK_64), list_row(ASK_64, ref(80), number=63),
                             list_row(number=70, author="boss", body="@luochen211 接口说明"),
                             list_row(said(ME, "2026-10-01T00:00:00Z", "开工"), said("cindy", "2026-10-28T00:00:00Z", "DNS 查了"),
                                      number=2, repo="luochen211/app")])
    monkeypatch.setattr(agent_watch, "handoff", lambda *a, **k: pytest.fail("不该打标签"))
    for number, repo, words in [(64, "acme/channel", "等对方回"), (63, "acme/channel", "open PR")]:
        with pytest.raises(ValueError, match=words):
            issues.handoff(PROJECTS, repo, number)
    with pytest.raises(ValueError, match="不在收件箱的 issue 里"):
        issues.handoff(PROJECTS, "acme/channel", 99)
    seen = []
    monkeypatch.setattr(conversation, "handoff", lambda projects, items, mode=None, **_: seen.append((items, mode)) or {
        "handed": [{"subject": items[0], "kind": "issue", "handed_at": "2026-11-18T12:00:00+08:00"}], "skipped": [], "routed": "", "error": ""})
    out = issues.handoff(PROJECTS, "acme/channel", 70)  # 待你回：走 issue 留言流程，不带模式是处理并回复
    assert seen == [(["acme/channel#70"], "handle")] and out["subject"] == "acme/channel#70" and out["flow"] == "feedback"
    issues.handoff(PROJECTS, "acme/channel", 70, mode="reply")  # 「帮我回复」：模式传给 issue 留言流程
    assert seen[-1] == (["acme/channel#70"], "reply")
    with pytest.raises(ValueError, match="模式写法不对"):
        issues.handoff(PROJECTS, "acme/channel", 70, mode="push")
    out = issues.handoff(PROJECTS, "luochen211/app", 2, mode="reply")  # 自己仓库的待你回：同样走 issue 留言流程，不打标签
    assert seen[-1] == (["luochen211/app#2"], "reply") and out["flow"] == "feedback"


def test_issue_page_follows_who_spoke_last(monkeypatch):
    """同一组留言：我在问人的归等对方回，对方回了归待你回，纯自记是待动手（谁最后说话用 conversation.assess）。"""
    items = {64: (ASK_64,), 70: (ASK_64, said("boss", "2026-11-18T05:00:00Z", "@luochen211 1A")), 7: (said(ME, "2026-11-10T00:00:00Z", "自记一笔"),)}
    own = [list_row(*v, number=k) for k, v in items.items()]
    involved = [{**r, "type": "Issue"} for r in own]
    fake_lists(monkeypatch, own, involved)
    page = {r["subject"]: r["next"]["key"] for r in issues.view(PROJECTS)["issues"]}
    assert page["acme/channel#64"] == "wait" and not conversation.assess(list_row(*items[64], number=64)["thread"])["pending"]
    assert page["acme/channel#70"] == "reply" and conversation.assess(list_row(*items[70], number=70)["thread"])["pending"]
    assert page["acme/channel#7"] == "todo"


def test_a_feedback_round_that_already_saw_their_last_word_waits_like_the_messages_page():
    theirs = said("boss", "2026-10-07T09:54:40Z", "总头：已建卡。")
    mine_before = said(ME, "2026-10-07T08:13:10Z", "建议这样拆")
    seen = {"their_at": "2026-10-07T09:54:40Z", "run": 1782, "result": {"kind": "no_action", "reason": "只是通知"},
            "created_at": "2026-11-17T16:00:00+08:00"}
    out = classify(mine_before, theirs, seen=seen)  # 2026-11-20 起：对方的话你还没回，仍归待你回（不再派由 settle 管）
    assert out["key"] == "reply" and out["new"] == 1 and "1782" in out["why"] and "只是通知" in out["why"]
    assert classify(mine_before, theirs, said("boss", "2026-11-18T00:00:00Z", "还有一件"), seen=seen)["key"] == "reply"  # 对方又说了
    claimed = {**seen, "result": {"kind": "replied"}}
    assert classify(mine_before, theirs, seen=claimed)["key"] == "reply"  # 说回复了，GitHub 上没看到：照算待你回


def test_inbox_buckets_merge_the_waiting_classes_and_send_agent_questions_to_reply():
    def step(key):
        return {"key": key}
    assert [next_step.bucket(step(k)) for k in ("pr", "close", "wait")] == ["wait", "wait", "wait"]
    assert [next_step.bucket(step(k)) for k in ("reply", "todo", "look")] == ["reply", "todo", "look"]
    assert next_step.bucket(step("flow"), stage="等你决定") == "reply"  # 原「待你处理」的提问
    assert next_step.bucket(step("flow"), stage="等你看") == "reply"
    assert next_step.bucket(step("flow"), stage="已交给 Agent", stalled="lost") == "reply"  # 派发可能丢了，要你再交
    assert next_step.bucket(step("flow"), stage="处理中") == "agent"
    assert next_step.bucket(step("flow"), stage="已交给 Agent") == "agent"
    assert set(next_step.BUCKETS) == {"reply", "todo", "agent", "wait", "look", "hold", "record"}


def test_view_buckets_cover_the_rows_the_messages_page_missed(monkeypatch):
    """原消息页漏掉的：不在 involves 搜索里的待你回、别的仓库的待你回、Agent 等你决定的 issue。"""
    asks = {"id": 163, "kind": "ask", "title": "【问你】用哪个方案", "text": "", "options": [], "recommend": None, "created_at": ""}
    run_ = {"id": 1900, "rule": issues.HANDLE_RULE, "state": "done", "created_at": "2026-11-18T09:00:00+08:00",
            "finished_at": "2026-11-18T09:30:00+08:00", "summary": "", "error": "", "result": {"kind": "blocked", "reason": "要你定"}}
    fake_lists(monkeypatch, [
        list_row(number=70, author="boss", body="@luochen211 接口说明"),  # 不在 involved 里
        list_row(said(ME, "2026-10-01T00:00:00Z", "开工"), said("cindy", "2026-10-28T00:00:00Z", "DNS 查了"), number=2, repo="luochen211/app"),
        list_row(said(ME, "2026-11-10T00:00:00Z", "进展：下一步写导入。"), number=163, repo="luochen211/app"),
        list_row(ASK_64, ref(80), number=63), list_row(ASK_64),
    ])
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None, rule=None, parse=None: (
        {"runs": {"luochen211/app#163": run_}, "history": {}, "waiting": {"luochen211/app#163": [asks]}}, ""))
    out = {r["subject"]: r["next"] for r in issues.view(PROJECTS)["issues"]}
    assert out["acme/channel#70"]["bucket"] == "reply" and out["acme/channel#70"]["button"] == "帮我回复"
    assert out["luochen211/app#2"]["bucket"] == "reply" and out["luochen211/app#2"]["button"] == "帮我回复"  # 别的仓库同样出按钮
    # Agent 要你定、之后没有新动静：仍在待你回，但改判成待你定，只在详情里留「仍交给 Agent」（personal/settled.py）
    assert out["luochen211/app#163"]["key"] == "decide" and out["luochen211/app#163"]["bucket"] == "reply"
    assert out["luochen211/app#163"]["drawer_only"] and out["luochen211/app#163"]["line"] == "Agent 要你定：用哪个方案"
    assert out["acme/channel#63"]["bucket"] == "wait" and out["acme/channel#63"]["line"] == "PR #80 开着，等评审"
    assert out["acme/channel#64"]["bucket"] == "wait" and out["acme/channel#64"]["button"] == ""


def test_batch_handoff_routes_each_issue_by_its_bucket_and_triggers_once(monkeypatch):
    fake_lists(monkeypatch, [
        list_row(number=70, author="boss", body="@luochen211 接口说明"),  # 待你回：issue 留言流程
        list_row(said(ME, "2026-11-09T07:03:00Z", "进展：名单下好了，下一步整理。"), number=3, repo="luochen211/app"),  # 待动手
        list_row(said(ME, "2026-11-09T07:03:00Z", "进展：下一步写导入。"), number=4, repo="luochen211/app"),  # 待动手，打标签失败
        list_row(said(ME, "2026-10-01T00:00:00Z", "开工"), said("cindy", "2026-10-28T00:00:00Z", "DNS 查了"), number=2, repo="luochen211/app"),
        list_row(ASK_64),  # 等对方
        list_row(said("someone", "2026-10-22T00:14:00Z", "PR map"), number=9, repo="luochen211/app", author="someone"),  # 待你看
    ])
    labelled, fed, triggered = [], [], []

    def label(repo, number, title="", link="", *, trigger=None, **kwargs):
        if number == 4:
            raise RuntimeError("gh 失败")
        assert trigger and trigger("x", None) == ""  # 逐条不触发中台
        labelled.append(f"{repo}#{number}")
        return {"subject": f"{repo}#{number}", "handed_at": "2026-11-18T12:00:00+08:00", "routed": "", "error": ""}
    monkeypatch.setattr(agent_watch, "handoff", label)
    monkeypatch.setattr(conversation, "handoff", lambda projects, items, mode=None, **_: fed.append(items) or {
        "handed": [{"subject": s, "kind": "issue", "handed_at": "2026-11-18T12:00:00+08:00"} for s in items],
        "skipped": [], "routed": "ok", "error": ""})
    out = issues.batch_handoff(PROJECTS, ["acme/channel#70", "luochen211/app#3", "luochen211/app#4", "luochen211/app#2",
                                          "acme/channel#64", "luochen211/app#9", "acme/channel#99", "luochen211/app#3"],
                               trigger=lambda *a: triggered.append(a) or "routed")
    got = {r["subject"]: r for r in out["results"]}
    assert len(out["results"]) == 7 and out["handed"] == 3  # 70、3、2 交出；4 打标签失败，其余被把关挡下
    assert got["acme/channel#70"]["ok"] and got["acme/channel#70"]["flow"] == "feedback"
    assert got["luochen211/app#3"]["ok"] and got["luochen211/app#3"]["flow"] == "issue"
    assert not got["luochen211/app#4"]["ok"] and "gh 失败" in got["luochen211/app#4"]["error"]
    assert got["luochen211/app#2"]["ok"] and got["luochen211/app#2"]["flow"] == "feedback"  # 自己仓库的待你回：issue 留言流程
    assert not got["acme/channel#64"]["ok"] and "等对方" in got["acme/channel#64"]["error"]
    assert not got["luochen211/app#9"]["ok"] and "待你看" in got["luochen211/app#9"]["error"]
    assert not got["acme/channel#99"]["ok"] and "不在收件箱" in got["acme/channel#99"]["error"]
    assert fed == [["acme/channel#70", "luochen211/app#2"]] and labelled == ["luochen211/app#3"] and len(triggered) == 1
    for bad in ([], "acme/channel#70", ["acme/channel"], ["x"] * (issues.BATCH_MAX + 1)):
        with pytest.raises(ValueError):
            issues.batch_handoff(PROJECTS, bad)
