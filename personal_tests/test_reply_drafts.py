"""回复草稿：Agent 写草稿、页面确认发送（假的 gh，不真发）、发送前过时检查、失败保留草稿、批量、技能的草稿模式说明。"""
import importlib.util
import json

import pytest

from personal import agent_watch, github, pr_feedback, reply_drafts, conversation
from personal_tests.test_pr_feedback import THEIRS, fake_view as pr_fake_view, node, run_row, search_row
from personal_tests.test_pr_feedback import PROJECTS as PR_PROJECTS, NOW as PR_NOW

ME = "luochen211"


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch):
    def nope(args, kind, **kwargs):
        raise AssertionError(f"测试不该真的请求 GitHub：{args}")
    monkeypatch.setattr(github, "run", nope)
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path / "hub"))
    (tmp_path / "hub/issues").mkdir(parents=True)
    github.reset()
    pr_feedback.reset()
    yield
    github.reset()
    pr_feedback.reset()


def comment(who, at, cid, text="嗯", kind="User"):
    return json.dumps({"id": cid, "who": who, "type": kind, "at": at, "text": text, "url": f"https://github.com/o/r/issues/1#c{cid}"})


def reader(issue_comments=(), review_comments=(), reviews=()):
    """假的 gh api 读取：按路径给 JSON 行；记下读过的路径。"""
    calls = []

    def read(args):
        calls.append(args[3])
        if "/issues/" in args[3]:
            return "\n".join(issue_comments)
        if args[3].endswith("/comments"):
            return "\n".join(review_comments)
        return "\n".join(reviews)
    read.calls = calls
    return read


def draft(subject="o/r#1", kind="issue", replies=None, work="开了 PR #3"):
    return {"subject": subject, "kind": kind, "work": work,
            "replies": replies or [{"to": "boss", "body": "用第二版，见 PR #3"}]}


def saved(tmp_path, payload=None, read=None, at="2026-11-18T10:00:00+08:00"):
    read = read or reader([comment("boss", "2026-11-18T01:00:00Z", 101)])
    return reply_drafts.save(payload or draft(), data=tmp_path, read=read, at=agent_watch.parse_time(at))


@pytest.mark.parametrize("payload, words", [
    ({**draft(), "subject": "o/r"}, "owner/repo#编号"),
    ({**draft(), "kind": "discussion"}, "issue 或 pr"),
    ({**draft(), "replies": []}, "至少要有一条"),
    (draft(replies=[{"to": "boss", "body": "  "}]), "是空的"),
    (draft(replies=[{"to": "boss", "body": "好的\n\nGenerated with Claude Code"}]), "AI 工具"),
    (draft(replies=[{"to": "boss", "body": "好", "review_comment_id": 5}]), "只有 PR 的行内评审"),
    (draft(kind="pr", replies=[{"to": "boss", "body": "好", "review_comment_id": "abc"}]), "数字 id"),
])
def test_validate_rejects_bad_drafts_with_a_reason(payload, words):
    with pytest.raises(ValueError, match=words):
        reply_drafts.validate(payload)


def test_save_records_the_last_comment_by_others_and_replaces_the_old_draft(tmp_path):
    read = reader([comment("boss", "2026-11-18T01:00:00Z", 101), comment(ME, "2026-11-18T01:10:00Z", 102),
                   comment("github-actions[bot]", "2026-11-18T01:20:00Z", 103, kind="Bot")])
    first = saved(tmp_path, read=read)
    assert first["based_on"] == {"id": "101", "at": "2026-11-18T01:00:00Z", "who": "boss"}  # 你的、机器人的不算
    assert first["replies"] == [{"id": "r1", "to": "boss", "body": "用第二版，见 PR #3", "review_comment_id": None, "path": ""}]
    saved(tmp_path, draft(subject="O/R#1", replies=[{"to": "boss", "body": "新的一版"}]))
    drafts = reply_drafts.listing(tmp_path)["drafts"]
    assert len(drafts) == 1 and drafts[0]["replies"][0]["body"] == "新的一版"  # 同一条不分大小写，换掉旧的


def test_save_keeps_the_draft_when_the_thread_cannot_be_read(tmp_path):
    def broken(args):
        raise RuntimeError("HTTP 502")
    got = reply_drafts.save(draft(), data=tmp_path, read=broken)
    assert got["based_on"] is None and "没读到讨论" in got["based_error"]
    assert reply_drafts.pending(tmp_path) == {"o/r#1"}


def test_pr_drafts_read_review_comments_and_reviews_too(tmp_path):
    read = reader([comment("boss", "2026-11-18T01:00:00Z", 1)], [comment("boss", "2026-11-18T02:00:00Z", 2)],
                  [json.dumps({"id": 3, "who": "boss", "type": "User", "at": "2026-11-18T03:00:00Z", "text": "", "url": "", "state": "APPROVED"}),
                   json.dumps({"id": 4, "who": "boss", "type": "User", "at": "2026-11-18T02:30:00Z", "text": "", "url": "", "state": "CHANGES_REQUESTED"})])
    got = saved(tmp_path, draft(subject="o/r#12", kind="pr"), read=read)
    assert got["based_on"]["id"] == "4"  # 只点批准不写字的不算
    assert len(read.calls) == 3


def test_send_posts_each_reply_with_the_right_command_and_deletes_the_draft(tmp_path, monkeypatch):
    expired = []
    monkeypatch.setattr(github, "expire", lambda *prefix: expired.append(prefix))
    saved(tmp_path, draft(subject="o/r#12", kind="pr", replies=[
        {"to": "boss", "body": "R1、R2 都改了"}, {"to": "boss", "body": "改成常量，见 abc1234", "review_comment_id": 99, "path": "a.py"}]),
        read=reader([comment("boss", "2026-11-18T01:00:00Z", 101)]))
    posted = []
    out = reply_drafts.send("o/r#12", {"r1": "R1、R2 都改了，谢谢"}, data=tmp_path,
                            read=reader([comment("boss", "2026-11-18T01:00:00Z", 101)]), post=lambda args, body: posted.append((args, body)))
    assert out == {"ok": True, "sent": 2}
    assert posted[0] == (["gh", "pr", "comment", "12", "--repo", "o/r", "--body-file", "-"], "R1、R2 都改了，谢谢")  # 用改过的正文
    assert posted[1][0] == ["gh", "api", "--method", "POST", "repos/o/r/pulls/12/comments/99/replies", "--input", "-"]
    assert json.loads(posted[1][1]) == {"body": "改成常量，见 abc1234"}
    assert reply_drafts.pending(tmp_path) == set() and reply_drafts.load(tmp_path)["history"][0]["how"] == "sent"
    assert ("involved",) in expired


def test_issue_drafts_are_sent_as_issue_comments(tmp_path, monkeypatch):
    monkeypatch.setattr(github, "expire", lambda *prefix: None)
    saved(tmp_path)
    posted = []
    reply_drafts.send("o/r#1", data=tmp_path, read=reader([comment("boss", "2026-11-18T01:00:00Z", 101)]),
                      post=lambda args, body: posted.append(args))
    assert posted == [["gh", "issue", "comment", "1", "--repo", "o/r", "--body-file", "-"]]


def test_send_stops_when_they_replied_after_the_draft_and_keeps_the_edits(tmp_path):
    saved(tmp_path)
    later = reader([comment("boss", "2026-11-18T01:00:00Z", 101), comment("boss", "2026-11-18T03:00:00Z", 105, "等等，换个方案"),
                    comment("pm", "2026-11-18T03:05:00Z", 106, "同意")])
    posted = []
    out = reply_drafts.send("o/r#1", {"r1": "我改过的话"}, data=tmp_path, read=later, post=lambda args, body: posted.append(args))
    assert not out["ok"] and out["stale"] == 2 and out["error"] == "对方又回了 2 条，草稿可能过时"
    assert [c["who"] for c in out["newer"]] == ["pm", "boss"] and posted == []
    assert reply_drafts.load(tmp_path)["drafts"]["o/r#1"]["replies"][0]["body"] == "我改过的话"  # 改过的正文存回草稿


def test_force_sends_without_rereading(tmp_path, monkeypatch):
    monkeypatch.setattr(github, "expire", lambda *prefix: None)
    saved(tmp_path)

    def must_not_read(args):
        raise AssertionError("仍发送不再重读")
    out = reply_drafts.send("o/r#1", force=True, data=tmp_path, read=must_not_read, post=lambda args, body: None)
    assert out == {"ok": True, "sent": 1}


def test_a_failed_send_keeps_the_unsent_replies_with_a_chinese_reason(tmp_path):
    saved(tmp_path, draft(subject="o/r#12", kind="pr", replies=[{"to": "a", "body": "第一条"}, {"to": "b", "body": "第二条", "review_comment_id": 7}]),
          read=reader())
    calls = []

    def post(args, body):
        calls.append(body)
        if len(calls) == 2:
            raise RuntimeError("gh: Validation Failed (HTTP 422)")
    out = reply_drafts.send("o/r#12", data=tmp_path, read=reader(), post=post)
    assert out["ok"] is False and out["sent"] == 1
    assert out["error"] == "第 2 条发送失败：GitHub 拒收：要回复的评审意见不存在或已不能回复（前 1 条已发出，已从草稿里去掉），草稿还在"
    left = reply_drafts.load(tmp_path)["drafts"]["o/r#12"]
    assert [r["body"] for r in left["replies"]] == ["第二条"] and "422" not in left["error"] and left["error"]


@pytest.mark.parametrize("message, reason", [
    ("HTTP 404: Not Found", "找不到这条"), ("HTTP 403: Resource not accessible", "没有权限"),
    ("To get started with GitHub CLI, please run:  gh auth login", "gh 没登录"), ("connection reset by peer", "连不上 GitHub"),
    ("something odd", "GitHub 返回：something odd"),
])
def test_why_explains_gh_errors_in_chinese(message, reason):
    assert reason in reply_drafts.why(message)


def test_send_refuses_missing_drafts_and_ai_mentions_in_edits(tmp_path):
    with pytest.raises(ValueError, match="没有待确认的回复草稿"):
        reply_drafts.send("o/r#1", data=tmp_path, read=reader(), post=lambda a, b: None)
    saved(tmp_path)
    with pytest.raises(ValueError, match="AI 工具"):
        reply_drafts.send("o/r#1", {"r1": "Co-Authored-By: someone"}, data=tmp_path, read=reader(), post=lambda a, b: None)
    with pytest.raises(ValueError, match="是空的"):
        reply_drafts.send("o/r#1", {"r1": ""}, data=tmp_path, read=reader(), post=lambda a, b: None)


def test_batch_sends_one_by_one_and_reports_each(tmp_path, monkeypatch):
    """页面「全部确认发送」逐条调 send：一条过时、一条失败，不影响别的。"""
    monkeypatch.setattr(github, "expire", lambda *prefix: None)
    for n in (1, 2, 3):
        saved(tmp_path, draft(subject=f"o/r#{n}", replies=[{"to": "boss", "body": f"回复 {n}"}]), read=reader())
    reads = {"o/r#2": reader([comment("boss", "2026-11-18T05:00:00Z", 9)])}

    def post(args, body):
        if body == "回复 3":
            raise RuntimeError("HTTP 403")
    results = {s: reply_drafts.send(s, data=tmp_path, read=reads.get(s, reader()), post=post) for s in ("o/r#1", "o/r#2", "o/r#3")}
    assert results["o/r#1"]["ok"] and results["o/r#2"].get("stale") == 1 and "没有权限" in results["o/r#3"]["error"]
    assert reply_drafts.pending(tmp_path) == {"o/r#2", "o/r#3"}


def test_discard_and_regenerate(tmp_path):
    saved(tmp_path)
    reply_drafts.discard("o/r#1", data=tmp_path)
    assert reply_drafts.pending(tmp_path) == set() and reply_drafts.load(tmp_path)["history"][0]["how"] == "discarded"

    saved(tmp_path)

    def refused(subject):
        raise ValueError("中台还没有「处理 issue 留言」规则")
    with pytest.raises(ValueError):
        reply_drafts.regenerate([], "o/r#1", data=tmp_path, handoff=refused)
    assert reply_drafts.pending(tmp_path) == {"o/r#1"}  # 交不出去就留着草稿
    handed = []
    out = reply_drafts.regenerate([], "o/r#1", data=tmp_path, handoff=lambda s: handed.append(s) or {"handed": [{"subject": s}], "routed": "", "error": ""})
    assert out["ok"] and handed == ["o/r#1"] and reply_drafts.pending(tmp_path) == set()
    assert reply_drafts.load(tmp_path)["history"][0]["how"] == "regenerated"


def test_marks_cover_drafts_and_their_outcomes(tmp_path):
    saved(tmp_path, at="2026-11-18T10:00:00+08:00")
    assert reply_drafts.marks(tmp_path) == {"o/r#1": "2026-11-18T10:00:00+08:00"}
    reply_drafts.discard("o/r#1", data=tmp_path, at=agent_watch.parse_time("2026-11-18T11:00:00+08:00"))
    assert reply_drafts.marks(tmp_path) == {"o/r#1": "2026-11-18T11:00:00+08:00"}


def test_a_replied_pr_with_a_draft_is_not_flagged_unseen(tmp_path, monkeypatch):
    """回执说回复了、GitHub 上还没有你的评论：这一轮写了草稿就不是「去看一眼」。"""
    agent_watch.write_json(agent_watch.pr_ledger_path(), {
        "up/x#7": {"handed_at": "2026-11-13T09:00:00+08:00", "title": "PR 7", "url": "", "their_at": "2026-11-12T10:00:00Z"}})
    auto = {"runs": {"up/x#7": run_row(5, "up/x#7", result={"kind": "replied"})}}  # 这一轮 10:00+08:00 开始
    read, _ = pr_fake_view(monkeypatch, [search_row("up/x", 7)], {"up/x#7": node(THEIRS)}, auto)
    row = pr_feedback.view(PR_PROJECTS, tmp_path, read=read, now=PR_NOW)["prs"][0]
    assert row["stage"] == "等你看" and row["unseen"]
    reply_drafts.save(draft(subject="up/x#7", kind="pr"), data=tmp_path, read=reader(),
                      at=agent_watch.parse_time("2026-11-13T10:20:00+08:00"))
    pr_feedback.reset()
    row = pr_feedback.view(PR_PROJECTS, tmp_path, read=read, now=PR_NOW)["prs"][0]
    assert row["stage"] == "已处理" and not row["unseen"]


def test_gh_api_posts_are_writes_and_never_retried():
    assert github.writes(["gh", "api", "--method", "POST", "repos/o/r/pulls/1/comments/2/replies", "--input", "-"])
    assert github.writes(["gh", "issue", "comment", "1", "--repo", "o/r", "--body-file", "-"])
    assert not github.writes(["gh", "api", "--paginate", "repos/o/r/issues/1/comments"])


def test_skill_scripts_hand_over_to_the_workbench_module():
    for skill in ("handle-pr-feedback", "handle-issue-feedback"):
        path = agent_watch.ROOT / f"personal/skills/{skill}/scripts/reply_draft.py"
        spec = importlib.util.spec_from_file_location(f"reply_draft_{skill.replace('-', '_')}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.main is reply_drafts.main


def test_cli_saves_a_draft_without_posting(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(reply_drafts, "ROOT", tmp_path)
    monkeypatch.setattr(reply_drafts, "gh_read", reader([comment("boss", "2026-11-18T01:00:00Z", 101)]))
    file = tmp_path / "草稿.json"
    file.write_text(json.dumps(draft(), ensure_ascii=False), encoding="utf-8")
    assert reply_drafts.main(["save", str(file)]) == 0
    assert "已存草稿 o/r#1：1 条回复" in capsys.readouterr().out
    assert (tmp_path / ".personal/reply-drafts.json").exists()
    file.write_text(json.dumps(draft(replies=[{"to": "x", "body": "Generated with Claude Code"}])), encoding="utf-8")
    assert reply_drafts.main(["save", str(file)]) == 1 and "没存上" in capsys.readouterr().err


@pytest.mark.parametrize("skill, kind", [("handle-pr-feedback", "pr"), ("handle-issue-feedback", "issue")])
def test_skills_write_drafts_and_never_post_comments(skill, kind):
    text = (agent_watch.ROOT / f"personal/skills/{skill}/SKILL.md").read_text(encoding="utf-8")
    assert f"~/.claude/skills/{skill}/scripts/reply_draft.py save" in text and f'"kind": "{kind}"' in text
    assert "草稿待确认" in text and "确认发送" in text and "2026-11-18" in text
    for gone in ("用用户账号在 issue 下回复", "用用户账号回复评审人", "gh issue comment <编号>", "gh pr comment <编号>",
                 "/replies -f body", "再用用户账号在 issue 下逐条回复", "并用用户账号逐条回复评审人"):
        assert gone not in text
    if kind == "pr":
        assert "review_comment_id" in text and "in_reply_to_id" in text
