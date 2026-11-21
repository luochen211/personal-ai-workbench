import json
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from personal import agent_watch, watch_entry

KNOWN = {"o/r": {"github": "o/r", "name": "演示", "repo_path": "/work/r"}}
T0 = datetime(2026, 11, 6, 7, 0, tzinfo=timezone.utc)
READ_COMMENTS = agent_watch.issue_comments  # 夹具把模块里的换成空列表，这里留一份真的
READ_PRS = agent_watch.agent_prs
NOTICES = []


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    def offline(args, kind, **kwargs):
        raise AssertionError(f"测试不该真的请求 GitHub：{args}")
    monkeypatch.setattr(agent_watch.github, "run", offline)
    monkeypatch.setattr(agent_watch, "issue_comments", lambda repo, number: [])
    monkeypatch.setattr(agent_watch, "agent_prs", lambda repos: {"viewer": "me", "open": [], "merged": []})  # 审查另有测试
    monkeypatch.setattr(agent_watch, "busy_subjects", lambda read=None: set())
    monkeypatch.setattr(agent_watch, "notify", lambda title, text: NOTICES.append(title))
    NOTICES.clear()
    agent_watch.github.reset()
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path / "hub"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))  # install 会装 ~/.claude/skills，不碰真的家目录
    (tmp_path / "hub/issues").mkdir(parents=True)
    return tmp_path / "hub/issues"


def item(number):
    return {"repository": {"nameWithOwner": "o/r"}, "number": number, "title": f"需求 {number}",
            "url": f"https://github.com/o/r/issues/{number}", "body": "正文"}


def events_of(receipt):
    return json.loads(receipt.split("```agent-autoloop\n", 1)[1].rsplit("\n```", 1)[0])["events"]


def test_watch_skips_upstream_and_archived_projects(tmp_path):
    config = tmp_path / "projects.toml"
    config.write_text('''
[[projects]]
id = "live"
name = "在做的"
github = "o/live"

[[projects]]
id = "theirs"
name = "上游"
github = "u/theirs"
issue_scope = "mine"

[[projects]]
id = "done"
name = "归档的"
github = "o/done"
archived = true
archived_at = "2026-11-12"

[[projects]]
id = "kept"
name = "写了 false 的"
github = "o/kept"
archived = false
''', encoding="utf-8")
    assert sorted(agent_watch.projects(config)) == ["o/kept", "o/live"]


def test_first_run_records_baseline_with_time_and_dispatches_nothing(home):
    receipt = agent_watch.watch(known=KNOWN, read=lambda repos: [item(1)])
    assert events_of(receipt) == []
    saved = json.loads((home / "baseline.json").read_text())
    assert saved["issues"] == ["o/r#1"] and saved["recorded_at"]


def test_baseline_issue_released_only_when_labeled_after_baseline(home):
    (home / "baseline.json").write_text(json.dumps({"recorded_at": T0.isoformat(), "issues": ["o/r#1", "o/r#2"]}))
    labeled = {1: T0 - timedelta(days=1), 2: T0 + timedelta(days=1)}
    receipt = agent_watch.watch(known=KNOWN, read=lambda repos: [item(1), item(2), item(3)],
                                when_labeled=lambda repo, n: labeled[n])
    assert [e["id"] for e in events_of(receipt)] == ["o/r#2", "o/r#3"]
    assert "1 条不派" in receipt


def test_round_without_dispatch_reports_nothing_to_the_hub(home):
    (home / "baseline.json").write_text(json.dumps({"recorded_at": T0.isoformat(), "issues": ["o/r#1"]}))
    receipt = agent_watch.watch(known=KNOWN, read=lambda repos: [item(1)], when_labeled=lambda repo, n: T0 - timedelta(days=1))
    # 中台按第一行开头的「没事可做」把这一轮记成 nothing，工作台的运行记录不列
    assert receipt.splitlines()[0] == "没事可做：没有新的带 agent 标签的 issue；启用前已带标签、没有重新交给的 1 条不派"
    assert events_of(receipt) == []


def test_legacy_list_baseline_uses_file_time(home):
    path = home / "baseline.json"
    path.write_text(json.dumps(["o/r#1"]))
    recorded, issues = agent_watch.load_baseline(path)
    assert issues == {"o/r#1"} and abs((datetime.now(timezone.utc) - recorded).total_seconds()) < 60


def test_explicit_handoff_releases_baseline_and_gets_its_own_event_id(home):
    (home / "baseline.json").write_text(json.dumps({"recorded_at": T0.isoformat(), "issues": ["o/r#1"]}))
    at = T0 + timedelta(days=2)
    calls = []
    result = agent_watch.handoff("o/r", 1, "需求 1", "https://github.com/o/r/issues/1", at=at,
                                 label=lambda repo, n: calls.append(("label", repo, n)),
                                 trigger=lambda key, when, title, link: calls.append(("emit", key)) or "交给了：立即看 agent issue")
    assert calls == [("label", "o/r", 1), ("emit", "o/r#1")]
    assert result["routed"] == "交给了：立即看 agent issue" and not result["error"]
    receipt = agent_watch.watch(known=KNOWN, read=lambda repos: [item(1)], when_labeled=lambda repo, n: None)
    [event] = events_of(receipt)
    assert event["subject"] == "o/r#1" and event["id"] == agent_watch.event_id("o/r#1", at.isoformat())
    assert event["id"] != "o/r#1" and "本地目录：/work/r" in event["detail"]


def test_dispatched_event_carries_comments_newest_kept_within_limit(home, monkeypatch):
    thread = [{"author": "boss", "at": f"2026-10-{d:02d}", "body": f"第 {d} 条" + "字" * 900} for d in range(1, 21)]
    thread.append({"author": "me", "at": "2026-10-28", "body": "回本块进包条件的预计日期：10-30"})
    monkeypatch.setattr(agent_watch, "issue_comments", lambda repo, number: thread)
    (home / "baseline.json").write_text(json.dumps({"recorded_at": T0.isoformat(), "issues": []}))
    long_body = dict(item(1), body="正" * 5000)
    [event] = events_of(agent_watch.watch(known=KNOWN, read=lambda repos: [long_body]))
    detail = event["detail"]
    assert len(detail) <= agent_watch.DETAIL_LIMIT and "本地目录：/work/r" in detail
    assert "me 2026-10-28：回本块进包条件的预计日期：10-30" in detail  # 最近的回复一定在
    assert "第 1 条" not in detail and "评论共 21 条" in detail and "gh issue view 1 --repo o/r --comments" in detail


def test_short_thread_is_shown_in_full_and_oldest_first(home):
    detail = agent_watch.build_detail("o/r", KNOWN["o/r"], item(1), [
        {"author": "a", "at": "2026-10-01", "body": "先"}, {"author": "b", "at": "2026-10-02", "body": "后"}])
    assert "评论共 2 条：" in detail and detail.index("a 2026-10-01：先") < detail.index("b 2026-10-02：后")


def test_comment_read_failure_still_dispatches_with_a_note(home, monkeypatch):
    def broken(repo, number):
        raise RuntimeError("HTTP 502")
    monkeypatch.setattr(agent_watch, "issue_comments", broken)
    (home / "baseline.json").write_text(json.dumps({"recorded_at": T0.isoformat(), "issues": []}))
    [event] = events_of(agent_watch.watch(known=KNOWN, read=lambda repos: [item(1)]))
    assert "评论没读到（HTTP 502）" in event["detail"] and "--comments" in event["detail"]


def test_issue_comments_reads_author_date_and_body(monkeypatch):
    out = json.dumps({"comments": [{"author": {"login": "boss"}, "createdAt": "2026-10-28T06:14:13Z", "body": " 好 "}]})
    monkeypatch.setattr(agent_watch.github, "run", lambda args, kind, **kw: out)
    assert READ_COMMENTS("o/r", 6) == [{"author": "boss", "at": "2026-10-28", "time": "2026-10-28T06:14:13Z", "body": "好"}]


def test_failed_label_rolls_back_the_handoff_record(home):
    def fail(repo, number):
        raise RuntimeError("没权限")
    with pytest.raises(RuntimeError):
        agent_watch.handoff("o/r", 1, label=fail, trigger=lambda *a: "")
    assert agent_watch.load_handoffs() == {}


def test_handoff_keeps_label_when_hub_is_down(home):
    def down(*args):
        raise RuntimeError("连不上中台")
    result = agent_watch.handoff("o/r", 1, label=lambda repo, n: None, trigger=down)
    assert "监看闸门" in result["error"] and "o/r#1" in agent_watch.load_handoffs()


def test_add_label_creates_missing_label_once(monkeypatch):
    calls = []
    def run(args, kind, **kwargs):
        calls.append(args[:3])
        if args[:3] == ["gh", "issue", "edit"] and len(calls) == 1:
            raise RuntimeError("'agent' not found")
        return ""
    monkeypatch.setattr(agent_watch.github, "run", run)
    agent_watch.add_label("o/r", 5)
    assert calls == [["gh", "issue", "edit"], ["gh", "label", "create"], ["gh", "issue", "edit"]]


def test_consecutive_failures_notify_once_and_reset_on_success(home):
    alerts = []
    for _ in range(5):
        agent_watch.record_result("FileNotFoundError", alert=lambda title, text: alerts.append(text))
    assert len(alerts) == 1 and "连续 3 次" in alerts[0]
    agent_watch.record_result("")
    assert json.loads((home / "watch-state.json").read_text())["failures"] == 0
    for _ in range(3):
        agent_watch.record_result("又坏了", alert=lambda title, text: alerts.append(text))
    assert len(alerts) == 2


def test_main_counts_failures(home, monkeypatch, capsys):
    def broken():
        raise FileNotFoundError("projects.toml")
    monkeypatch.setattr(agent_watch, "watch", broken)
    monkeypatch.setattr(agent_watch, "notify", lambda title, text: None)
    assert agent_watch.main([]) == 1
    assert json.loads((home / "watch-state.json").read_text())["failures"] == 1
    assert "没读到 GitHub" in capsys.readouterr().out


def test_pointer_is_healed_only_when_stale(home, tmp_path):
    other = tmp_path / "other"
    (other / "personal").mkdir(parents=True)
    (other / "personal/agent_watch.py").write_text("")
    agent_watch.pointer().write_text(str(other))
    assert agent_watch.ensure_pointer(tmp_path / "new") is False
    agent_watch.pointer().write_text("/gone/workbench")
    assert agent_watch.ensure_pointer(tmp_path / "new") is True
    assert agent_watch.pointer().read_text().strip() == str(tmp_path / "new")


def test_install_backs_up_old_watch_and_writes_thin_entry(home):
    (home / "watch.py").write_text("PROJECTS = '/Volumes/old/projects.toml'\n")
    agent_watch.install(agent_watch.ROOT)
    assert (home / "watch.py").read_text() == agent_watch.ENTRY.read_text()
    assert list(home.glob("watch.py.bak-*"))
    assert agent_watch.pointer().read_text().strip() == str(agent_watch.ROOT)
    channel = home.parent / "plugins/desktop/channel.ts"
    assert channel.read_text() == agent_watch.CHANNEL.read_text()
    assert "kickstart" not in agent_watch.install(agent_watch.ROOT)  # 第二次没有改动，不用重启


def test_install_links_the_handle_issue_skill_to_the_repository(home, tmp_path):
    skill = tmp_path / "user/.claude/skills/handle-issue"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("旧的手改版本\n")
    message = agent_watch.install(agent_watch.ROOT)
    source = agent_watch.ROOT / "personal/skills/handle-issue"
    assert skill.is_symlink() and skill.resolve() == source.resolve() and "已链到仓库" in message
    assert (skill / "SKILL.md").read_text(encoding="utf-8") == (source / "SKILL.md").read_text(encoding="utf-8")
    backups = list((tmp_path / "user/.claude/skill-backups").glob("handle-issue.bak-*"))
    assert len(backups) == 1 and (backups[0] / "SKILL.md").read_text() == "旧的手改版本\n"
    assert not list(skill.parent.glob("handle-issue.bak-*"))  # 备份不留在技能目录，免得成了第二个同名技能
    assert "已链到仓库" not in agent_watch.install(agent_watch.ROOT)  # 第二次没有改动

    other = tmp_path / "moved/personal/skills/handle-issue"
    other.mkdir(parents=True)
    agent_watch.install(tmp_path / "moved")  # 仓库搬家：旧链接换成新位置，不备份链接
    assert skill.resolve() == other.resolve() and len(list(backups[0].parent.iterdir())) == 1


def test_startup_relinks_a_dangling_skill_link_but_never_touches_a_real_directory(home, tmp_path):
    def repo(name):
        path = tmp_path / name
        for skill_name in agent_watch.SKILLS:
            (path / "personal/skills" / skill_name).mkdir(parents=True)
            (path / "personal/skills" / skill_name / "SKILL.md").write_text(f"{name} {skill_name}")
        (path / "personal/agent_watch.py").write_text("")
        return path
    old, new = repo("old"), repo("new")
    skill = tmp_path / "user/.claude/skills/handle-issue"
    review_skill = tmp_path / "user/.claude/skills/review-pr"
    agent_watch.install(old)
    shutil.rmtree(old)  # 仓库搬走：paihuo-root 和技能链接都悬空
    assert agent_watch.ensure_pointer(new) is True
    assert skill.is_symlink() and (skill / "SKILL.md").read_text() == "new handle-issue"
    assert review_skill.is_symlink() and (review_skill / "SKILL.md").read_text() == "new review-pr"  # 两份技能都修

    other = repo("worktree")
    agent_watch.link_skill(other / "personal/skills/handle-issue", skill, tmp_path / "unused")
    assert agent_watch.ensure_pointer(other) is False  # paihuo-root 有效，以它为准，从别的检出启动也不改它
    assert skill.resolve() == (new / "personal/skills/handle-issue").resolve()

    skill.unlink()
    skill.mkdir()
    (skill / "SKILL.md").write_text("手改的真目录")
    agent_watch.ensure_pointer(new)
    assert not skill.is_symlink() and (skill / "SKILL.md").read_text() == "手改的真目录"
    assert not (tmp_path / "user/.claude/skill-backups").exists()


def test_thin_entry_reports_missing_repository(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(watch_entry, "POINTER", tmp_path / "paihuo-root")
    monkeypatch.setattr(watch_entry, "ALERTED", tmp_path / "entry-alerted")
    monkeypatch.setenv("PAIHUO_ROOT", str(tmp_path / "gone"))
    sent = []
    monkeypatch.setattr(watch_entry.subprocess, "run", lambda *a, **k: sent.append(a))
    assert watch_entry.main() == 1 and watch_entry.main() == 1
    assert len(sent) == 1 and "agent_watch install" in capsys.readouterr().out


def test_recent_handoff_is_read_directly_when_search_lags(home):
    (home / "baseline.json").write_text(json.dumps({"recorded_at": T0.isoformat(), "issues": []}))
    agent_watch.handoff("o/r", 4, label=lambda repo, n: None, trigger=lambda *a: "")
    (home / "handoffs.json").write_text(json.dumps({**agent_watch.load_handoffs(), "o/r#9": T0.isoformat()}))
    asked = []
    receipt = agent_watch.watch(known=KNOWN, read=lambda repos: [],
                                fetch=lambda repo, n: asked.append(n) or item(int(n)))
    assert asked == ["4"]  # 超过一天的交给记录不再逐条读
    assert [e["subject"] for e in events_of(receipt)] == ["o/r#4"]


def test_first_run_still_dispatches_an_explicit_handoff(home):
    agent_watch.handoff("o/r", 2, label=lambda repo, n: None, trigger=lambda *a: "")
    receipt = agent_watch.watch(known=KNOWN, read=lambda repos: [item(1), item(2)])
    assert [e["subject"] for e in events_of(receipt)] == ["o/r#2"] and "显式交给的照派" in receipt
    assert json.loads((home / "baseline.json").read_text())["issues"] == ["o/r#1", "o/r#2"]


def test_held_verdict_is_cached_until_the_issue_leaves_the_search(home):
    (home / "baseline.json").write_text(json.dumps({"recorded_at": T0.isoformat(), "issues": ["o/r#1", "o/r#2"]}))
    asked = []
    def when(repo, n):
        asked.append(n)
        if n == 2:
            raise RuntimeError("网络断了")
        return T0 - timedelta(days=1)
    for _ in range(3):
        agent_watch.watch(known=KNOWN, read=lambda repos: [item(1), item(2)], when_labeled=when)
    assert asked == [1, 2, 2, 2]  # 确认不派的不再翻 events；没读到的不记结论
    agent_watch.watch(known=KNOWN, read=lambda repos: [item(2)], when_labeled=when)  # o/r#1 标签被摘掉
    asked.clear()
    receipt = agent_watch.watch(known=KNOWN, read=lambda repos: [item(1)], when_labeled=lambda repo, n: asked.append(n) or T0 + timedelta(hours=1))
    assert asked == [1] and [e["subject"] for e in events_of(receipt)] == ["o/r#1"]  # 重新打标签后再核对


def test_archived_repo_keeps_its_held_verdicts_until_unarchived(home):
    (home / "baseline.json").write_text(json.dumps({"recorded_at": T0.isoformat(), "issues": ["o/r#1"]}))
    asked = []
    def when(repo, n):
        asked.append(n)
        return T0 - timedelta(days=1)
    agent_watch.watch(known=KNOWN, read=lambda repos: [item(1)], when_labeled=when)
    # 归档期间 o/r 不在监看范围：不读它、不派它，它的结论也不删
    agent_watch.watch(known={}, read=lambda repos: [], when_labeled=when)
    assert "o/r#1" in json.loads((home / "held.json").read_text())["issues"]
    # 取消归档：照旧用原来的结论，不重新翻 events，也不误派
    receipt = agent_watch.watch(known=KNOWN, read=lambda repos: [item(1)], when_labeled=when)
    assert asked == [1] and ("```agent-autoloop" not in receipt or events_of(receipt) == [])


def test_github_reads_go_through_the_shared_exit(home, monkeypatch):
    kinds = []
    def run(args, kind, **kwargs):
        kinds.append(kind)
        if kind == "graphql:agent-watch":
            node = {**item(1), "comments": {"totalCount": 0, "nodes": []}}
            return json.dumps({"data": {"viewer": {"login": "me"}, "issues": {"nodes": [node]}}})
        if kind == "rest:events":
            return json.dumps([[{"event": "labeled", "label": {"name": "agent"}, "created_at": "2026-11-05T00:00:00Z"}]])
        return json.dumps({"number": 1, "title": "t", "url": "u", "labels": [{"name": "agent"}], "state": "OPEN"})
    monkeypatch.setattr(agent_watch.github, "run", run)
    assert agent_watch.search(["o/r"]) == [{**item(1), "comments": []}]
    assert agent_watch.labeled_at("o/r", 1) == datetime(2026, 11, 5, tzinfo=timezone.utc)
    assert agent_watch.view_one("o/r", 1)["number"] == 1
    assert kinds == ["graphql:agent-watch", "rest:events", "issue"]


def test_rate_limit_while_labeling_does_not_try_to_create_the_label(monkeypatch):
    calls = []
    def run(args, kind, **kwargs):
        calls.append(args[:3])
        raise agent_watch.github.RateLimited(time.time() + 60, "rate limit")
    monkeypatch.setattr(agent_watch.github, "run", run)
    with pytest.raises(agent_watch.github.RateLimited):
        agent_watch.add_label("o/r", 5)
    assert calls == [["gh", "issue", "edit"]]


def test_concurrent_handoffs_keep_every_record(home, monkeypatch):
    slow = agent_watch.load_handoffs
    def load(path=None):
        data = slow(path)
        time.sleep(0.01)  # 拉大读和写之间的窗口：不加锁时会互相覆盖
        return data
    monkeypatch.setattr(agent_watch, "load_handoffs", load)
    threads = [threading.Thread(target=agent_watch.handoff, args=("o/r", n),
                                kwargs={"label": lambda repo, n: None, "trigger": lambda *a: ""}) for n in range(1, 13)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(slow()) == sorted(f"o/r#{n}" for n in range(1, 13))


def test_failed_label_only_rolls_back_its_own_record(home):
    def fail(repo, number):
        with agent_watch.handoff_ledger() as ledger:  # 打标签期间别处又交了一次
            ledger["o/r#1"] = "2026-11-12T10:00:00+08:00"
        raise RuntimeError("没权限")
    with pytest.raises(RuntimeError):
        agent_watch.handoff("o/r", 1, label=fail, trigger=lambda *a: "")
    assert agent_watch.load_handoffs() == {"o/r#1": "2026-11-12T10:00:00+08:00"}


SHA = "a" * 40
NEW = "b" * 40
REVIEWED = {"o/r": {**KNOWN["o/r"], "review_prs": True}}  # o/ 不是本人账号：要显式打开才审
PR_URL = "https://github.com/o/r/pull/34"


def pr(sha=SHA, comments=(), state="SUCCESS", draft=False, committed="2026-11-06T06:00:00Z", branch="agent/issue-7",
       author="me", fork=False, repo="o/r"):
    return {"repo": repo, "number": 34, "title": "做需求 7", "url": f"https://github.com/{repo}/pull/34", "isDraft": draft,
            "isCrossRepository": fork, "headRefName": branch, "headRefOid": sha, "baseRefName": "main", "author": {"login": author},
            "commits": {"nodes": [{"commit": {"oid": sha, "committedDate": committed,
                                              "statusCheckRollup": {"state": state} if state else None}}]},
            "comments": {"nodes": list(comments)}}


def review(verdict, sha=SHA, at="2026-11-06T06:30:00Z", author="me", n=1):
    body = f"## 审查\n\n1. `a.py:1`：改\n\n<!-- paihuo-review head={sha} -->\nVERDICT: {verdict}"
    return {"body": body, "url": f"{PR_URL}#issuecomment-{n}", "createdAt": at, "author": {"login": author}}


def blank():
    return {"sent": {}, "told": {}, "holds": {}}


def run_pass(prs, ledger=None, at=T0, busy=(), known=REVIEWED, merged=(), viewer="me"):
    """一轮审查判断：返回 (事件, 提醒标题, 说明)。ledger 传进来就跨轮沿用。"""
    ledger = blank() if ledger is None else ledger
    def busy_now():
        if busy is None:
            raise OSError("中台没开")
        return set(busy)
    events, notices, note = agent_watch.review_pass({"viewer": viewer, "open": list(prs), "merged": list(merged)},
                                                    known, ledger, busy_now, at)
    return events, [title for title, _ in notices], note


def steps(prs, **kwargs):
    return run_pass(prs, **kwargs)[0]


def test_unreviewed_head_with_green_ci_gets_one_review_bound_to_the_commit():
    ledger = blank()
    [event] = steps([pr()], ledger=ledger)
    assert event["type"] == agent_watch.REVIEW and event["subject"] == "o/r#7"
    assert event["id"] == f"review:o/r#34@{SHA[:12]}" and event["link"] == f"{PR_URL}/commits/{SHA}"
    assert f"审查的提交：{SHA}" in event["detail"] and "本地目录：/work/r" in event["detail"]
    assert ledger["sent"][event["id"]]["head"] == SHA and ledger["sent"][event["id"]]["kind"] == "review"
    assert steps([pr()], ledger=ledger, at=T0 + timedelta(minutes=10)) == []  # 派过了：不再放进事件
    # 审过了就不再派；有新提交（head 变了）要重新审
    assert steps([pr(comments=[review("APPROVE")])], ledger=ledger) == []
    [again] = steps([pr(NEW, comments=[review("APPROVE")])], ledger=ledger)
    assert again["id"] == f"review:o/r#34@{NEW[:12]}" and "第 2 次审查" in again["detail"]


def test_only_the_owners_own_prs_in_owned_or_opted_in_repos_are_reviewed():
    assert steps([pr(author="stranger")]) == []  # 别人开的 PR：不在本机跑别人的代码
    assert steps([pr(fork=True)]) == []  # fork 来的 PR
    assert steps([{k: v for k, v in pr().items() if k != "isCrossRepository"}]) == []  # 读不到是不是 fork：不审
    assert steps([pr()], viewer="") == []  # 读不到当前账号：不审
    assert steps([pr(author="ME")]) != []  # 账号不分大小写
    assert steps([pr()], known=KNOWN) == []  # 不是本人账号名下的仓库，没写 review_prs
    owned = {"luochen211/r": {**KNOWN["o/r"], "github": "luochen211/r"}}
    assert len(steps([pr(repo="luochen211/r")], known=owned)) == 1  # 本人仓库默认审

    def no_request(repos):
        raise AssertionError(f"没有能审的仓库，不该读 PR：{repos}")
    assert agent_watch.reviews_due(KNOWN, no_request) == ([], "")


def test_review_waits_for_ci_skips_drafts_other_branches_and_fresh_commits_without_ci():
    ledger = blank()
    events, told, _ = run_pass([pr(state="PENDING")], ledger=ledger)
    assert events == [] and told == [] and ledger["holds"]["o/r#34"]["why"] == "CI 还在跑，未审查"
    events, told, _ = run_pass([pr(state="PENDING")], ledger=ledger, at=T0 + timedelta(hours=1))
    assert events == [] and told == ["o/r#7 的 PR #34 CI 一直没跑完"]  # 等了一小时：提醒一次
    assert run_pass([pr(state="PENDING")], ledger=ledger, at=T0 + timedelta(hours=3))[1] == []
    events, told, _ = run_pass([pr(state="FAILURE")], ledger=ledger)
    assert events == [] and told == ["o/r#7 的 PR #34 CI 没过，未审查"]
    assert ledger["holds"]["o/r#34"]["why"] == "CI 没过，未审查"
    assert run_pass([pr(state="FAILURE")], ledger=ledger)[1] == []  # 同一提交只提醒一次
    assert steps([pr(draft=True)]) == []
    assert steps([{**pr(), "repo": "x/y"}]) == []  # 不在监看范围
    assert steps([pr(state=None, committed="2026-11-06T06:59:00Z")]) == []
    assert len(steps([pr(state=None)])) == 1  # 没有 CI、提交放了一阵


def test_no_review_while_the_issue_is_being_handled():
    ledger = blank()
    events, _, note = run_pass([pr()], ledger=ledger, busy={"o/r#7"})
    assert events == [] and ledger["holds"]["o/r#34"]["why"].startswith("「处理 issue」那一轮还没跑完")
    events, _, note = run_pass([pr()], ledger=ledger, busy=None)
    assert events == [] and "没读到中台的轮次" in note and ledger["sent"] == {}
    assert len(steps([pr()], ledger=ledger)) == 1  # 跑完了再审


def test_changes_required_sends_bounded_rework_rounds_counted_from_the_ledger():
    ledger = blank()
    first = [review("CHANGES_REQUIRED")]
    [event] = steps([pr(comments=first)], ledger=ledger)
    assert event["type"] == agent_watch.REWORK and event["subject"] == "o/r#7" and event["id"] == "rework:o/r#34@1"
    assert "返工第 1 轮（最多 3 轮）" in event["detail"] and "VERDICT: CHANGES_REQUIRED" in event["detail"]
    assert event["summary"].startswith("返工第 1 轮") and event["link"].endswith("#issuecomment-1")
    assert steps([pr(comments=first)], ledger=ledger) == []  # 同一提交派过返工：等新提交
    c, d = "c" * 40, "d" * 40
    # 评论只读得到最近的（comments(last: 100) 会漏掉早的）：轮次照派出记录数
    [second] = steps([pr(c, comments=[review("CHANGES_REQUIRED", sha=c, n=2)])], ledger=ledger)
    [third] = steps([pr(d, comments=[review("CHANGES_REQUIRED", sha=d, n=3)])], ledger=ledger)
    assert (second["id"], third["id"]) == ("rework:o/r#34@2", "rework:o/r#34@3")
    # 第 3 轮返工后的新提交照样审，并在 detail 里写明满了
    [last_review] = steps([pr(NEW)], ledger=ledger)
    assert last_review["type"] == agent_watch.REVIEW and "返工已满 3 轮" in last_review["detail"]
    # 第 4 次要改：不再返工，等本人
    assert steps([pr(NEW, comments=[review("CHANGES_REQUIRED", sha=NEW, n=4)])], ledger=ledger) == []
    assert ledger["holds"]["o/r#34"]["why"].startswith("返工 3 轮后审查仍要改")


def test_wait_is_retried_after_an_hour_at_most_three_more_times_per_commit():
    ledger = blank()
    soon = [review("WAIT", at="2026-11-06T06:30:00Z")]
    assert steps([pr(comments=soon)], ledger=ledger) == []
    [retry] = steps([pr(comments=soon)], ledger=ledger, at=T0 + timedelta(hours=1))
    assert retry["id"] == f"review:o/r#34@{SHA[:12]}~1"
    assert steps([pr(comments=soon)], ledger=ledger, at=T0 + timedelta(hours=2)) == []  # 派过了
    three = [review("WAIT", at="2026-11-06T03:00:00Z", n=i) for i in range(3)]
    [third] = steps([pr(comments=three)])
    assert third["id"].endswith("~3")  # 第一次之外再审 3 次
    four = three + [review("WAIT", at="2026-11-06T04:00:00Z", n=9)]
    events, told, _ = run_pass([pr(comments=four)], ledger=ledger)
    assert events == [] and told == ["o/r#7 的 PR #34 审查一直等待"]
    assert ledger["holds"]["o/r#34"]["why"] == "同一提交审查等待了 4 次，不再自动重审"
    assert run_pass([pr(comments=four)], ledger=ledger)[1] == []


def test_only_well_formed_reviews_from_the_owner_count():
    forged = review("APPROVE", author="stranger")
    loose = {**review("APPROVE"), "body": review("APPROVE")["body"] + "\n谢谢"}
    assert len(steps([pr(comments=[forged, loose])])) == 1  # 都不算审过
    [event] = steps([pr(comments=[review("CHANGES_REQUIRED", author="stranger")])])
    assert event["type"] == agent_watch.REVIEW  # 别人贴的「要改」不会变成返工指令


@pytest.mark.parametrize("text, verdict", [
    ("x\nVERDICT: APPROVE\n\n", "APPROVE"),
    ("```\n<!-- paihuo-review head=abc1234 -->\nVERDICT: APPROVE\n```", "APPROVE"),
    ("~~~markdown\nVERDICT: WAIT\n~~~\n", "WAIT"),
    ("VERDICT: APPROVE。", "APPROVE"),
    ("**VERDICT: CHANGES_REQUIRED**", "CHANGES_REQUIRED"),
    ("`VERDICT：WAIT`", "WAIT"),
    ("VERDICT: approve", None),
    ("VERDICT: APPROVE\n谢谢", None),
    ("VERDICT: STALE", None),  # 只有回执认 STALE
    ("说明 VERDICT: APPROVE", None),  # 只按换行切
    ("", None),
])
def test_parse_verdict_tolerates_common_slips(text, verdict):
    assert agent_watch.parse_verdict(text) == verdict
    assert agent_watch.parse_verdict("没审\nVERDICT: STALE", agent_watch.VERDICTS + (agent_watch.STALE,)) == "STALE"


def test_stuck_review_and_rework_notify_once_and_are_not_redispatched():
    ledger = blank()
    [event] = steps([pr()], ledger=ledger)
    assert run_pass([pr()], ledger=ledger, at=T0 + timedelta(hours=1))[:2] == ([], [])
    events, told, _ = run_pass([pr()], ledger=ledger, at=T0 + timedelta(hours=2))
    assert events == [] and told == ["o/r#7 的 PR #34 审查没有结论"]
    assert run_pass([pr()], ledger=ledger, at=T0 + timedelta(hours=5))[:2] == ([], [])  # 只提醒一次，不重派

    ledger = blank()
    asked = [review("CHANGES_REQUIRED", at="2026-11-06T06:30:00Z")]
    [rework] = steps([pr(comments=asked)], ledger=ledger)
    events, told, _ = run_pass([pr(comments=asked)], ledger=ledger, at=T0 + timedelta(hours=2))
    assert events == [] and told == ["o/r#7 返工第 1 轮没有新提交"]
    assert run_pass([pr(comments=asked)], ledger=ledger, at=T0 + timedelta(hours=3))[1] == []


def test_head_moving_during_a_review_and_a_foreign_merge_notify_once():
    ledger = blank()
    steps([pr()], ledger=ledger)
    moved = pr(NEW, committed="2026-11-06T07:10:00Z")
    _, told, _ = run_pass([moved], ledger=ledger, at=T0 + timedelta(minutes=20))
    assert "o/r#7 的 PR #34 在审查期间有新提交" in told
    assert "o/r#7 的 PR #34 在审查期间有新提交" not in run_pass([moved], ledger=ledger, at=T0 + timedelta(minutes=30))[1]

    merged = {"repo": "o/r", "number": 34, "url": PR_URL, "headRefName": "agent/issue-7", "mergedBy": {"login": "bot"},
              "mergedAt": "2026-11-06T08:00:00Z"}
    _, told, _ = run_pass([], ledger=ledger, merged=[merged])
    assert told == ["o/r#34 不是你的账号合并的", "o/r#34 在审查出结论前就合并了"]
    assert run_pass([], ledger=ledger, merged=[merged])[1] == []
    reviewed = {**merged, "number": 35, "mergedBy": {"login": "me"}, "headRefOid": SHA,
                "comments": {"nodes": [review("APPROVE", at="2026-11-06T07:30:00Z")]}}
    ledger["sent"]["review:o/r#35@x"] = {"at": T0.isoformat(), "kind": "review", "pr": "o/r#35", "head": SHA}
    assert run_pass([], ledger=ledger, merged=[reviewed])[1] == []  # 本人账号、审完才合并：不提醒


def test_a_merge_before_the_review_or_rework_settles_notifies_once():
    # Agent 用的就是本人账号，「谁合并的」查不出来；按先后判断：结论还没出来就合并了
    ledger = blank()
    ledger["sent"]["review:o/r#34@a"] = {"at": T0.isoformat(), "kind": "review", "pr": "o/r#34", "head": SHA}
    merged = {"repo": "o/r", "number": 34, "url": PR_URL, "headRefName": "agent/issue-7", "headRefOid": SHA,
              "mergedBy": {"login": "me"}, "mergedAt": "2026-11-06T07:20:00Z", "comments": {"nodes": []}}
    assert run_pass([], ledger=ledger, merged=[merged])[1] == ["o/r#34 在审查出结论前就合并了"]
    assert run_pass([], ledger=ledger, merged=[merged])[1] == []
    late = {**merged, "comments": {"nodes": [review("APPROVE", at="2026-11-06T07:30:00Z")]}}  # 评论在合并之后
    assert run_pass([], ledger=blank() | {"sent": dict(ledger["sent"])}, merged=[late])[1] == ["o/r#34 在审查出结论前就合并了"]

    ledger = blank()
    ledger["sent"]["rework:o/r#34@1"] = {"at": T0.isoformat(), "kind": "rework", "pr": "o/r#34", "head": SHA, "round": 1}
    assert run_pass([], ledger=ledger, merged=[merged])[1] == ["o/r#34 在返工第 1 轮出结论前就合并了"]
    ledger = blank()
    ledger["sent"]["rework:o/r#34@1"] = {"at": T0.isoformat(), "kind": "rework", "pr": "o/r#34", "head": SHA, "round": 1}
    assert run_pass([], ledger=ledger, merged=[{**merged, "headRefOid": NEW}])[1] == []  # 返工推了新提交才合并


def patched_rules(home):
    path = realistic_rules(home)
    agent_watch.ensure_rules(path)
    return path


def test_two_consecutive_watch_passes_do_not_emit_the_same_event(home):
    agent_watch.write_json(home / "baseline.json", {"recorded_at": T0.isoformat(), "issues": []})
    patched_rules(home)
    found = lambda repos: {"viewer": "me", "open": [pr(state=None, committed="2026-11-01T00:00:00Z")], "merged": []}
    receipt = agent_watch.watch(known=REVIEWED, read=lambda repos: [], pulls=found)
    assert receipt.startswith("派出 审查 o/r#7") and [e["type"] for e in events_of(receipt)] == [agent_watch.REVIEW]
    again = agent_watch.watch(known=REVIEWED, read=lambda repos: [], pulls=found)
    assert again.startswith(agent_watch.NOTHING) and events_of(again) == []
    assert list(agent_watch.load_ledger()["sent"]) == [f"review:o/r#34@{SHA[:12]}"]


def test_watch_appends_reviews_and_keeps_dispatching_issues_when_pr_read_fails(home):
    agent_watch.write_json(home / "baseline.json", {"recorded_at": T0.isoformat(), "issues": []})
    patched_rules(home)

    def broken(repos):
        raise RuntimeError("GitHub 限流中")
    receipt = agent_watch.watch(known=REVIEWED, read=lambda repos: [item(9)], pulls=broken)
    assert "没读到 agent PR" in receipt.splitlines()[0] and [e["subject"] for e in events_of(receipt)] == ["o/r#9"]
    # 这一轮刚派出的 issue 马上要处理：它的 PR 先不审
    found = lambda repos: {"viewer": "me", "open": [pr(branch="agent/issue-9", state=None, committed="2026-11-01T00:00:00Z")],
                           "merged": []}
    agent_watch.write_json(home / "handoffs.json", {"o/r#9": T0.isoformat()})
    receipt = agent_watch.watch(known=REVIEWED, read=lambda repos: [item(9)], pulls=found)
    assert [e["type"] for e in events_of(receipt)] == [agent_watch.OPENED]


def test_agent_prs_reads_viewer_and_agent_branches_in_one_search_request(monkeypatch):
    calls = []
    def graphql(query, kind="graphql", **variables):
        calls.append((kind, sorted(variables), "viewer { login }" in query and "isCrossRepository" in query
                      and "mergedBy { login }" in query))
        node = {**pr(), "repository": {"nameWithOwner": "o/r"}}
        return {"viewer": {"login": "me"},
                "open": {"nodes": [node, {**node, "headRefName": "feat/x"}, {**node, "repository": {"nameWithOwner": "x/y"}}]},
                "merged": {"nodes": [{"number": 30, "headRefName": "agent/issue-3", "mergedBy": {"login": "me"},
                                      "repository": {"nameWithOwner": "O/R"}}]}}
    monkeypatch.setattr(agent_watch.github, "graphql", graphql)
    found = READ_PRS(["o/r"])
    assert found["viewer"] == "me" and [r["headRefName"] for r in found["open"]] == ["agent/issue-7"]
    assert found["open"][0]["repo"] == "o/r" and "repository" not in found["open"][0]
    assert [(r["repo"], r["number"]) for r in found["merged"]] == [("O/R", 30)]  # 仓库名大小写不同也认
    assert calls == [("graphql:agent-watch", ["merged", "open"], True)]
    assert READ_PRS(["o/r"]) == found and len(calls) == 1  # 快照还新：不再请求


def realistic_rules(home):
    """照真实 ~/.agent-autoloop/rules.json 的形状（路径换成通用的，不含密钥）。"""
    path = home.parent / "rules.json"
    path.write_text(json.dumps({
        "sources": {"local": {"name": "本机"}, "github": {"name": "GitHub"}},
        "agents": {
            "claude": {"name": "Claude Code", "worker": "mini", "use": "claude"},
            "issue-watch": {"name": "看 GitHub issue", "worker": "mini", "use": "command",
                            "command": ["python3", "/Users/me/.agent-autoloop/issues/watch.py"]},
            "claude-issue": {"name": "Claude Code（处理 issue，可提交推送）", "worker": "mini", "use": "claude", "access": "full"}},
        "channels": {"desktop": {"name": "桌面通知"}},
        "rules": [
            {"name": "试一下", "when": {"source": "local", "types": ["try.*"]}, "to": "claude", "cwd": "/Users/me/.agent-autoloop/try",
             "prompt": "试跑"},
            {"name": "看带 agent 标签的 issue", "when": {"cron": "*/10 * * * *"}, "to": "issue-watch", "cwd": "/Volumes/Luochen",
             "tell": "never", "emits": {"source": "github", "types": ["issues.opened"]}},
            {"name": "立即看 agent issue", "when": {"source": "local", "types": ["issues.check"]}, "to": "issue-watch",
             "cwd": "/Volumes/Luochen", "tell": "never", "emits": {"source": "github", "types": ["issues.opened"]}},
            {"name": "处理 issue", "when": {"source": "github", "types": ["issues.opened"]}, "to": "claude-issue",
             "cwd": "/Volumes/Luochen", "skill": "handle-issue", "parallel": 1}],
        "elsewhere": []}, ensure_ascii=False, indent=1), encoding="utf-8")
    path.chmod(0o644)
    return path


def test_ensure_rules_adds_review_pieces_once_and_keeps_edits_and_mode(home):
    path = realistic_rules(home)
    note = agent_watch.ensure_rules(path)
    assert "加了规则「审查 PR」" in note and list(path.parent.glob("rules.json.bak-*"))
    assert path.stat().st_mode & 0o777 == 0o644  # 沿用原文件权限，不变成 0600
    data = json.loads(path.read_text())
    assert data["agents"]["claude-review"] == {"worker": "mini", "name": "Claude Code（审查 PR）", "use": "claude", "access": "full"}
    rules = {r["name"]: r for r in data["rules"]}
    assert rules["审查 PR"] == {"name": "审查 PR", "when": {"source": "github", "types": ["pr.review"]}, "to": "claude-review",
                              "cwd": "/Volumes/Luochen", "skill": "review-pr", "parallel": 1}
    assert rules["处理 issue"]["when"]["types"] == ["issues.opened", "issues.rework"]
    for name in agent_watch.WATCH_RULES:
        assert rules[name]["emits"] == {"source": "github", "types": ["issues.opened", "prs.feedback", "issues.feedback", "pr.review", "issues.rework", "learning.next"]}
    assert rules["试一下"]["prompt"] == "试跑" and data["elsewhere"] == []  # 别的照留
    data["agents"]["claude-review"]["model"] = "opus"  # 本人改过的模型留着
    agent_watch.write_json(path, data)
    assert agent_watch.ensure_rules(path) == ""
    assert json.loads(path.read_text())["agents"]["claude-review"]["model"] == "opus"
    assert "手动加" in agent_watch.ensure_rules(home / "missing.json")


@pytest.mark.parametrize("break_it, why", [
    (lambda d: d.update(rules={"处理 issue": {}}), "rules 不是对象的列表"),
    (lambda d: d["rules"].append("看带 agent 标签的 issue"), "rules 不是对象的列表"),
    (lambda d: d.update(agents=[]), "agents 不是对象"),
    (lambda d: d["sources"].pop("github"), "没有 github 来源"),
    (lambda d: d["rules"][3].update(when={"cron": "*/5 * * * *"}), "when 不是 github 来源的事件"),
    (lambda d: d["rules"][3]["when"].update(types="issues.opened"), "when 不是 github 来源的事件"),
    (lambda d: d["agents"].pop("issue-watch"), "Agent issue-watch 没登记"),
    (lambda d: d["rules"].pop(2), "没有「立即看 agent issue」规则"),
    (lambda d: d["rules"][1].update(emits=["issues.opened"]), "emits 不是对象"),
])
def test_ensure_rules_leaves_an_unexpected_file_alone(home, break_it, why):
    path = realistic_rules(home)
    data = json.loads(path.read_text())
    break_it(data)
    path.write_text(json.dumps(data, ensure_ascii=False))
    note = agent_watch.ensure_rules(path)
    assert why in note and "审查规则没有加" in note and "手动加" in note
    after = path.read_text()  # 审查和返工的配置一处不加；PR 反馈那部分自己判断，能加的照加
    assert "claude-review" not in after and "审查 PR" not in after and "issues.rework" not in after


def test_install_links_the_review_skill_too(home, tmp_path):
    message = agent_watch.install(agent_watch.ROOT)
    skill = tmp_path / "user/.claude/skills/review-pr"
    assert skill.is_symlink() and skill.resolve() == (agent_watch.ROOT / "personal/skills/review-pr").resolve()
    assert str(skill) in message


def test_review_skill_documents_the_formats_the_watcher_parses():
    text = (agent_watch.ROOT / "personal/skills/review-pr/SKILL.md").read_text(encoding="utf-8")
    sample = text.split("   ~~~\n", 2)[1]
    marks = agent_watch.REVIEW_MARK.findall(sample.replace("<完整 SHA>", SHA))
    assert marks == [SHA] and agent_watch.parse_verdict(sample) == "CHANGES_REQUIRED"
    for verdict in ("APPROVE", "CHANGES_REQUIRED", "WAIT", "STALE"):
        assert f"`VERDICT: {verdict}`" in text
    assert f"最多再审 {agent_watch.MAX_WAITS} 次" in text
    handle = (agent_watch.ROOT / "personal/skills/handle-issue/SKILL.md").read_text(encoding="utf-8")
    assert "## 返工" in handle and agent_watch.REWORK in handle and "不新开 PR" in handle


def test_a_damaged_ledger_does_not_stop_the_watch(home):
    (home / "dispatched.json").write_text(json.dumps({"sent": {"a": "坏的", "b": {"at": "不是时间", "pr": "o/r#34", "head": SHA},
                                                              "c": {"at": T0.isoformat(), "kind": "review", "pr": "o/r#34", "head": SHA}},
                                                     "told": {"x": 3}, "holds": []}))
    ledger = agent_watch.load_ledger()
    assert list(ledger["sent"]) == ["c"] and ledger["told"] == {} and ledger["holds"] == {}


def test_nothing_is_dispatched_or_recorded_until_the_hub_rules_are_patched(home):
    agent_watch.write_json(home / "baseline.json", {"recorded_at": T0.isoformat(), "issues": []})
    found = lambda repos: {"viewer": "me", "open": [pr(state=None, committed="2026-11-01T00:00:00Z")], "merged": []}
    path = realistic_rules(home)  # 合并后、install 前：监看规则还不能发 pr.review
    receipt = agent_watch.watch(known=REVIEWED, read=lambda repos: [item(9)], pulls=found)
    assert "中台规则还没补，先运行 install" in receipt.splitlines()[0]
    assert [e["type"] for e in events_of(receipt)] == [agent_watch.OPENED]  # issue 照派
    assert agent_watch.load_ledger()["sent"] == {}  # 没派就不记
    agent_watch.ensure_rules(path)
    receipt = agent_watch.watch(known=REVIEWED, read=lambda repos: [], pulls=found)
    assert [e["type"] for e in events_of(receipt)] == [agent_watch.REVIEW] and agent_watch.load_ledger()["sent"]


def test_rules_missing_checks_the_four_pieces_and_accepts_wildcards(home):
    path = patched_rules(home)
    assert agent_watch.rules_missing(path) == ""
    good = json.loads(path.read_text())

    def broken(change):
        data = json.loads(json.dumps(good))
        change({r["name"]: r for r in data["rules"]}, data)
        path.write_text(json.dumps(data, ensure_ascii=False))
        return agent_watch.rules_missing(path)
    assert "不能发" in broken(lambda r, d: r["看带 agent 标签的 issue"]["emits"].update(types=["issues.opened", "pr.review"]))
    assert "不能发" in broken(lambda r, d: r["立即看 agent issue"].pop("emits"))
    assert "没有规则接 pr.review" in broken(lambda r, d: d["rules"].remove(r["审查 PR"]))
    assert "不接 issues.rework" in broken(lambda r, d: r["处理 issue"]["when"].update(types=["issues.opened"]))
    assert "没有规则接" in broken(lambda r, d: r["审查 PR"]["when"].update(source="local"))  # 来源对不上也收不到
    assert broken(lambda r, d: [r[n]["emits"].update(types=["*"]) for n in agent_watch.WATCH_RULES]
                  and r["审查 PR"]["when"].update(types=["pr.*"])) == ""
    path.unlink()
    assert agent_watch.rules_missing(path) == "读不到 rules.json"


def test_rereview_dispatches_a_stuck_review_again_under_a_new_id(home):
    patched_rules(home)
    agent_watch.write_json(home / "baseline.json", {"recorded_at": T0.isoformat(), "issues": []})
    found = lambda repos: {"viewer": "me", "open": [pr(state=None, committed="2026-11-01T00:00:00Z")], "merged": []}
    first = events_of(agent_watch.watch(known=REVIEWED, read=lambda repos: [], pulls=found))
    assert events_of(agent_watch.watch(known=REVIEWED, read=lambda repos: [], pulls=found)) == []  # 卡住：不会自己重派
    triggered = []
    message = agent_watch.rereview("o/r#34", trigger=lambda key, at, title: triggered.append(key))
    assert "+1" in message and triggered == ["o/r#34"]
    [again] = events_of(agent_watch.watch(known=REVIEWED, read=lambda repos: [], pulls=found))
    assert again["id"] == first[0]["id"] + "+1"
    assert events_of(agent_watch.watch(known=REVIEWED, read=lambda repos: [], pulls=found)) == []
    with pytest.raises(ValueError):
        agent_watch.rereview("o/r 34")
    assert "没能马上触发" in agent_watch.rereview("o/r#34", trigger=lambda *a: (_ for _ in ()).throw(RuntimeError("中台没开")))
def test_rounds_after_the_first_report_count_as_nothing_but_still_carry_the_events():
    """同一批事件第二轮起照样报给中台去重，但第一行是「没事可做：」，运行记录不再每 10 分钟列「派出 N 条」。"""
    first = agent_watch.watch(known=KNOWN, read=lambda repos: [item(1)])
    assert first.startswith("第一次运行")
    labeled = lambda repo, n: datetime.now().astimezone()
    second = agent_watch.watch(known=KNOWN, read=lambda repos: [item(1), item(2)], when_labeled=labeled, prs=[])
    assert second.startswith("派出 2 条 issue：o/r#1、o/r#2")
    third = agent_watch.watch(known=KNOWN, read=lambda repos: [item(1), item(2)], when_labeled=labeled, prs=[])
    assert third.startswith(agent_watch.NOTHING)
    assert events_of(third) == events_of(second) and len(events_of(third)) == 2


def test_pr_events_are_announced_once():
    pr = [{"id": "pr:up/x#7@20261113T090000+0800", "type": "prs.feedback", "subject": "up/x#7", "summary": "PR 7",
           "detail": "", "link": ""}]
    labeled = lambda repo, n: None
    agent_watch.watch(known=KNOWN, read=lambda repos: [], prs=[])
    once = agent_watch.watch(known=KNOWN, read=lambda repos: [], when_labeled=labeled, prs=pr)
    again = agent_watch.watch(known=KNOWN, read=lambda repos: [], when_labeled=labeled, prs=pr)
    assert once.startswith("派出 1 条 PR 反馈：up/x#7")
    assert again.startswith(agent_watch.NOTHING) and events_of(again) == pr


def test_run_log_hides_watch_rounds_that_only_repeat_earlier_reports():
    from personal import issues
    watch = sorted(agent_watch.WATCH_RULES)[0]
    rows = [{"id": n, "rule": watch, "state": "done", "summary": text, "subjects": []} for n, text in
            [(1, "派出 2 条 issue：o/r#1、o/r#2"), (2, "派出 2 条 issue：o/r#1、o/r#2"),
             (3, "派出 1 条 PR 反馈：up/x#7；派出 2 条 issue：o/r#1、o/r#2"), (4, "派出 1 条 PR 反馈：up/x#7；没有新的带 agent 标签的 issue")]]
    assert issues.repeated_reports(rows) == {2, 4}
