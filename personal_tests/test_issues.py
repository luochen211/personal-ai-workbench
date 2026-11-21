import json
import shutil
import time

import pytest

from personal import agent_watch, github, issues, upstream


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    github.reset()
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path / "hub"))
    monkeypatch.setattr(issues, "_results", {})
    yield
    github.reset()

PROJECTS = [{"id": "demo", "name": "演示", "github": "o/r"}]


def item(number, labels=(), updated="2026-11-06T01:00:00Z"):
    return {"repository": {"nameWithOwner": "o/r"}, "number": number, "title": f"需求 {number}",
            "url": f"https://github.com/o/r/issues/{number}", "labels": [{"name": n} for n in labels],
            "author": {"login": "me"}, "commentsCount": 0, "createdAt": updated, "updatedAt": updated}


def test_view_marks_stages_from_labels_and_autoloop(monkeypatch):
    rows = [item(1), item(2, ["agent"]), item(3, ["agent"]), item(4, ["agent"]), item(5, ["agent"]),
            {**item(6), "repository": {"nameWithOwner": "other/x"}}]
    auto = {"runs": {"o/r#3": {"id": 9, "state": "claimed", "summary": ""},
                     "o/r#4": {"id": 8, "state": "done", "summary": "改好了", "result": {"kind": "no_change"}},
                     "o/r#5": {"id": 7, "state": "done", "summary": "缺决定"}},
            "waiting": {"o/r#5": [{"id": 3, "kind": "ask", "title": "o/r#5 选哪个", "text": ""}]}}
    monkeypatch.setattr(issues, "search", lambda repos, branches=None: rows)
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None: (auto, ""))
    report = issues.view(PROJECTS, refresh=True)
    stages = {r["subject"]: r["stage"] for r in report["issues"]}
    assert stages == {"o/r#1": "未交给 Agent", "o/r#2": "已交给 Agent", "o/r#3": "处理中",
                      "o/r#4": "已处理", "o/r#5": "等你决定"}


def test_view_reports_github_failure(monkeypatch):
    def fail(repos, branches=None):
        raise ValueError("没登录")
    monkeypatch.setattr(issues, "search", fail)
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None: ({}, ""))
    report = issues.view(PROJECTS, refresh=True)
    assert report["issues"] == [] and "没登录" in report["error"]


def test_detail_rejects_repositories_outside_projects():
    with pytest.raises(ValueError):
        issues.detail(PROJECTS, "other/x", 1)


def test_view_uses_handoff_time_over_older_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path))
    (tmp_path / "issues").mkdir()
    (tmp_path / "issues/handoffs.json").write_text(
        '{"o/r#1": "2026-11-12T10:00:00+08:00", "o/r#2": "2026-11-12T10:00:00+08:00"}')
    auto = {"runs": {"o/r#1": {"id": 5, "state": "done", "summary": "上次的结论", "created_at": "2026-11-11T01:00:00Z"},
                     "o/r#2": {"id": 9, "state": "claimed", "summary": "", "created_at": "2026-11-12T02:01:00Z"}}}
    monkeypatch.setattr(issues, "search", lambda repos, branches=None: [item(1, ["agent"]), item(2, ["agent"])])
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None: (auto, ""))
    report = issues.view(PROJECTS, refresh=True)
    rows = {r["subject"]: r for r in report["issues"]}
    assert rows["o/r#1"]["stage"] == "已交给 Agent" and rows["o/r#2"]["stage"] == "处理中"
    assert rows["o/r#1"]["handed_at"] == "2026-11-12T10:00:00+08:00"


def run_row(number, rule, state="done", subject=""):
    return {"id": number, "rule": rule, "state": state, "summary": f"第 {number} 轮", "createdAt": "2026-11-12T01:00:00Z",
            "events": [{"subject": subject}] if subject else []}


def test_handle_runs_pages_past_the_first_sixty(monkeypatch):
    monkeypatch.setitem(issues._runs, "settled", 0)
    monkeypatch.setitem(issues._runs, "rows", {})
    pages = {"/v1/state": {"runs": [run_row(n, "看带 agent 标签的 issue") for n in range(200, 140, -1)],
                           "hasMore": True, "nextBefore": 141, "cards": []},
             "/v1/state?before=141": {"runs": [run_row(120, "处理 issue", subject="o/r#7")] +
                                      [run_row(n, "看带 agent 标签的 issue") for n in range(119, 60, -1)],
                                      "hasMore": False, "nextBefore": None, "cards": []}}
    asked = []
    def read(path):
        asked.append(path)
        return pages[path]
    rows, _ = issues.handle_runs(read)
    assert [r["subjects"] for r in rows.values()] == [["o/r#7"]] and len(asked) == 2
    asked.clear()
    rows, _ = issues.handle_runs(read)  # 都已结束：第二次只读第一页
    assert asked == ["/v1/state"] and 120 in rows


def todo_row(projects, repo, number, data=None):
    return {"subject": f"{repo}#{int(number)}", "next": {"key": "todo", "action": "issue"}}


def test_handoff_labels_and_triggers(monkeypatch):
    monkeypatch.setattr(issues, "next_of", todo_row)
    monkeypatch.setattr(issues, "detail", lambda projects, repo, number: {"title": "需求 3", "url": "https://github.com/o/r/issues/3"})
    seen = []
    monkeypatch.setattr(issues.agent_watch, "handoff", lambda repo, number, title, url: seen.append((repo, number, title)) or {"subject": "o/r#3"})
    assert issues.handoff(PROJECTS, "o/r", "3") == {"subject": "o/r#3"}
    assert seen == [("o/r", "3", "需求 3")]

    def fail(*args):
        raise RuntimeError("没权限")
    monkeypatch.setattr(issues.agent_watch, "handoff", fail)
    with pytest.raises(ValueError, match="没交给成"):
        issues.handoff(PROJECTS, "o/r", "3")


def test_questions_are_tied_to_the_issue_run_that_left_them(monkeypatch):
    rows = {7: {"id": 7, "state": "done", "summary": "", "error": "", "created_at": "2026-11-12T01:43:44Z",
                "finished_at": "2026-11-12T01:44:00.692Z", "link": "", "subjects": ["o/r#16"]},
            8: {"id": 8, "state": "done", "summary": "第一次", "error": "", "created_at": "a", "finished_at": "t8",
                "link": "", "subjects": ["o/r#16"]}}
    questions = [{"id": 216, "kind": "notice", "state": "open", "rule": "处理 issue", "createdAt": "2026-11-12T01:44:00.692Z",
                  "title": "r#16 已收到", "text": "长" * 5000, "options": ["已处理"], "recommend": None},
                 {"id": 92, "kind": "ask", "state": "open", "rule": "派活：审查拆卡", "createdAt": "2026-11-12T01:44:00.692Z",
                  "title": "旧规则留下的提问 o/r#16", "text": "", "options": ["甲"], "recommend": 0}]
    monkeypatch.setattr(issues, "handle_runs", lambda: (rows, questions))
    auto, error = issues.autoloop_state()
    assert not error and [q["id"] for q in auto["waiting"]["o/r#16"]] == [216]  # 只认以 issue 为单位的规则
    assert len(auto["waiting"]["o/r#16"][0]["text"]) == 5000 and [r["id"] for r in auto["history"]["o/r#16"]] == [8, 7]
    assert issues.stage({"subject": "o/r#16", "labels": ["agent"]}, auto) == "等你看"


def test_answer_goes_to_the_hub_and_only_issue_questions(monkeypatch):
    calls = []
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None: ({"waiting": {"o/r#16": [{"id": 216}]}}, ""))
    monkeypatch.setattr(issues, "hub", lambda path, body=None: calls.append((path, body)) or {"changed": True, "state": "open"})
    assert issues.answer(PROJECTS, "216", {"choice": 2}) == {"message": "收到，Agent 接着干。"}
    issues.answer(PROJECTS, "216", {"text": " 换个做法 "})
    issues.answer(PROJECTS, "216", {"text": "--choice 9 -e 以短横线开头"})  # 不经命令行，原样送到
    assert calls == [("/v1/cards/216/answer", {"choice": 1}), ("/v1/cards/216/answer", {"text": "换个做法"}),
                     ("/v1/cards/216/answer", {"text": "--choice 9 -e 以短横线开头"})]
    for bad in ({}, {"choice": 0}, {"choice": True}, {"text": "x" * 5000}):
        with pytest.raises(ValueError):
            issues.answer(PROJECTS, "216", bad)
    with pytest.raises(ValueError, match="不在当前项目"):
        issues.answer(PROJECTS, "92", {"choice": 1})
    assert len(calls) == 3
    monkeypatch.setattr(issues, "hub", lambda path, body=None: {"changed": False})
    assert "已经处理过" in issues.answer(PROJECTS, "216", {"choice": 1})["message"]


def test_queued_run_that_absorbed_a_new_handoff_counts_as_processing(monkeypatch):
    rows = {971: {"id": 971, "state": "queued", "summary": "", "error": "找不到 claude", "created_at": "2026-11-12T01:49:18Z",
                  "finished_at": None, "link": "", "subjects": ["o/r#16", "o/r#16"]}}
    monkeypatch.setattr(issues, "handle_runs", lambda: (rows, []))
    auto, _ = issues.autoloop_state()
    assert [r["id"] for r in auto["history"]["o/r#16"]] == [971]
    row = {"subject": "o/r#16", "labels": ["agent"]}
    assert issues.stage(row, auto, "2026-11-12T09:53:24+08:00") == "处理中"


def test_run_log_and_watch_status(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path))
    (tmp_path / "issues").mkdir()
    (tmp_path / "issues/watch-state.json").write_text('{"failures": 4, "notified": true, "last_error": "FileNotFoundError"}')
    rows = {1: {"id": 1, "rule": "看带 agent 标签的 issue", "state": "done", "summary": "派出 1 条 issue：o/r#1", "error": "",
                "created_at": "2026-11-12T01:00:00Z", "claimed_at": "2026-11-12T01:00:01Z", "finished_at": "2026-11-12T01:00:04Z",
                "subjects": [""]},
            2: {"id": 2, "rule": "处理 issue", "state": "queued", "summary": "", "error": "找不到 claude",
                "created_at": "2026-11-12T01:01:00Z", "claimed_at": None, "finished_at": None, "subjects": ["o/r#1", "o/r#1"]}}
    monkeypatch.setattr(issues, "all_runs", lambda read=None: (rows, []))
    monkeypatch.setattr(issues, "run", lambda args, timeout=120:
                        "  看带 agent 标签的 issue：定时 */10 * * * *（下次 11/12 10:00） · → issue-watch\n")
    log = issues.run_log()
    assert [r["id"] for r in log["runs"]] == [2, 1] and log["runs"][1]["seconds"] == 3 and log["runs"][0]["subjects"] == ["o/r#1"]
    status = issues.watch_status()
    assert status["last_run"]["id"] == 1 and not status["last_run"]["failed"] and status["failures"] == 4
    assert status["dispatched"]["id"] == 1 and status["failed"] == []
    assert status["schedule"] == "*/10 * * * *" and status["next"] == "11/12 10:00"



def test_run_log_hides_idle_watch_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path))
    monkeypatch.setattr(issues, "run", lambda args, timeout=120: "")
    def row(id, rule, state, summary, error=""):
        return {"id": id, "rule": rule, "state": state, "summary": summary, "error": error, "created_at": "2026-11-12T01:00:00Z",
                "claimed_at": None, "finished_at": None, "subjects": []}
    rows = {r["id"]: r for r in [
        row(1, "看带 agent 标签的 issue", "done", "没有新的带 agent 标签的 issue；启用前已带标签、没有重新交给的 1 条不派"),  # 改前的旧轮次
        row(2, "看带 agent 标签的 issue", "nothing", "没有新的带 agent 标签的 issue"),
        row(3, "立即看 agent issue", "nothing", "没有新的带 agent 标签的 issue"),
        row(4, "看带 agent 标签的 issue", "done", "派出 1 条 issue：o/r#1"),
        row(5, "看带 agent 标签的 issue", "cancelled", "连续 10 次没跑成，不再自动重试", "退出码 1"),
        row(6, "处理 issue", "nothing", "#15 已经修好了，这次没有要改的。"),  # Agent 看过 issue 的结论照常列出
        row(7, "派活：看新需求", "nothing", "没有新的待拆分卡"),
        row(8, "派活：看新需求", "done", "没有新的带 agent 标签的 issue"),  # 别的规则不按这句话认
    ]}
    monkeypatch.setattr(issues, "all_runs", lambda read=None: (rows, []))
    log = issues.run_log()
    assert [r["id"] for r in log["runs"]] == [8, 6, 5, 4] and log["idle"] == 4 and log["total"] == 4
    status = issues.watch_status()
    assert status["dispatched"]["id"] == 4 and [r["id"] for r in status["failed"]] == [5]
    assert status["last_run"] == {"id": 5, "created_at": "2026-11-12T01:00:00Z", "state": "cancelled", "failed": True}


def test_watch_status_lists_only_dispatched_and_failed_rounds(monkeypatch, tmp_path):
    """没事可做的轮次、把同一批事件再报一次的轮次都不逐条列；失败的轮次照列，带原因。"""
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path))
    monkeypatch.setattr(issues, "run", lambda args, timeout=120: "")
    def row(id, state, summary, error="", rule="看带 agent 标签的 issue"):
        return {"id": id, "rule": rule, "state": state, "summary": summary, "error": error,
                "created_at": f"2026-11-18T0{id}:00:00Z", "claimed_at": None, "finished_at": None, "subjects": []}
    rows = {r["id"]: r for r in [
        row(1, "done", "派出 1 条 issue：o/r#1"),
        row(2, "queued", "", "找不到 claude"),  # 还在重试的失败
        row(3, "done", "派出 1 条 issue：o/r#1"),  # 同一批事件再报一次
        row(4, "nothing", "没事可做：没有新的带 agent 标签的 issue", rule="立即看 agent issue"),
        row(5, "cancelled", "连续 10 次没跑成，不再自动重试", "退出码 1"),
        row(6, "nothing", "没事可做：没有新的带 agent 标签的 issue"),
        row(7, "nothing", "没事可做：没有新的带 agent 标签的 issue"),
    ] + [row(id, "cancelled", "连续 10 次没跑成，不再自动重试", "退出码 1") for id in (8, 9)]}  # 同一原因连着失败：并成一行
    monkeypatch.setattr(issues, "all_runs", lambda read=None: (rows, []))
    status = issues.watch_status()
    assert "runs" not in status
    assert status["dispatched"]["id"] == 1 and status["dispatched"]["summary"] == "派出 1 条 issue：o/r#1"
    # 2 和 5 之后同一规则跑成过（3、6、7），不算当前失败
    assert [(r["id"], r["error"], r["count"]) for r in status["failed"]] == [(9, "退出码 1", 2)]
    assert status["failed"][0]["first_at"] == "2026-11-18T08:00:00Z"
    assert status["last_run"] == {"id": 9, "created_at": "2026-11-18T09:00:00Z", "state": "cancelled", "failed": True}


def test_watch_status_drops_failures_followed_by_a_success_of_the_same_rule(monkeypatch, tmp_path):
    """11-11 找不到旧路径的失败，之后同一巡检都跑成了：不再列为当前失败；别的规则跑成不算数；运行记录照样留着。"""
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path))
    monkeypatch.setattr(issues, "run", lambda args, timeout=120: "")
    def row(id, state, summary="", error="", rule="看带 agent 标签的 issue"):
        return {"id": id, "rule": rule, "state": state, "summary": summary, "error": error,
                "created_at": f"2026-11-1{id}T01:00:00Z", "claimed_at": None, "finished_at": None, "subjects": []}
    missing = "退出码 1：FileNotFoundError: /Volumes/Luochen/workbench/personal/projects.toml"
    rows = {r["id"]: r for r in [
        row(1, "cancelled", "连续 10 次没跑成，不再自动重试", missing),
        row(2, "nothing", "没事可做：没有新的带 agent 标签的 issue"),
        row(3, "cancelled", "连续 10 次没跑成，不再自动重试", "退出码 2", rule="立即看 agent issue"),
        row(4, "done", "派出 1 条 issue：o/r#1"),  # 看带标签的又跑成了，不影响「立即看」的失败
    ]}
    monkeypatch.setattr(issues, "all_runs", lambda read=None: (rows, []))
    status = issues.watch_status()
    assert [r["id"] for r in status["failed"]] == [3]
    assert 1 in [r["id"] for r in issues.run_log()["runs"]]
    rows[5] = row(5, "nothing", "没事可做：没有新的带 agent 标签的 issue", rule="立即看 agent issue")
    assert issues.watch_status()["failed"] == []


def test_run_log_hides_rules_removed_from_rules_json(monkeypatch, tmp_path):
    """已从 rules.json 删掉的规则（比如以卡片为单位的「派活：审查拆卡」）的历史默认不列，只报条数；读不到 rules.json 就不按规则隐藏。"""
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path))
    def row(id, rule, state="done", summary="完成"):
        return {"id": id, "rule": rule, "state": state, "summary": summary, "error": "", "created_at": "2026-11-12T01:00:00Z",
                "claimed_at": None, "finished_at": None, "subjects": []}
    rows = {r["id"]: r for r in [row(1, "派活：审查拆卡"), row(2, "派活：看新需求"), row(3, "处理 issue"), row(4, "每日简报")]}
    monkeypatch.setattr(issues, "all_runs", lambda read=None: (rows, []))
    log = issues.run_log()  # 没有 rules.json：只隐藏以卡片为单位的规则
    assert [r["id"] for r in log["runs"]] == [4, 3, 2] and log["retired"] == 1
    (tmp_path / "rules.json").write_text(json.dumps({"rules": [{"name": "处理 issue"}, {"name": "每日简报"}]}), encoding="utf-8")
    log = issues.run_log()
    assert [r["id"] for r in log["runs"]] == [4, 3] and log["retired"] == 2 and log["total"] == 2
    assert "拆卡" not in json.dumps(log, ensure_ascii=False)
    (tmp_path / "rules.json").write_text("不是 JSON", encoding="utf-8")
    assert [r["id"] for r in issues.run_log()["runs"]] == [4, 3, 2]
    assert [r["id"] for r in issues.run_log(keep_idle=True)["runs"]] == [4, 3, 2, 1]  # 巡检状态自己挑，不按规则隐藏

def test_rate_limit_keeps_cached_list_and_does_not_hammer(monkeypatch):
    calls = []
    def search(repos, branches=None):
        calls.append(1)
        if len(calls) > 1:
            raise github.RateLimited(time.time() + 600, "secondary rate limit")
        return [item(1)]
    monkeypatch.setattr(issues, "search", search)
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None: ({}, ""))
    assert len(issues.view(PROJECTS)["issues"]) == 1
    report = issues.view(PROJECTS, refresh=True)
    assert [r["number"] for r in report["issues"]] == [1] and "GitHub 限流中" in report["limited"] and not report["error"]
    for _ in range(3):
        issues.view(PROJECTS, refresh=True)
    assert len(calls) == 2  # 限流期间不再发请求


def pr(number, repo="up/x", **fields):
    return {"type": "PullRequest", "repository": {"nameWithOwner": repo}, "number": number, "title": "PR",
            "url": "u", "createdAt": "t", "updatedAt": "t", "author": {"login": "luochen211"}, **fields}


def test_upstream_projects_only_show_issues_involving_me(monkeypatch):
    projects = PROJECTS + [{"id": "up", "name": "上游", "github": "up/x", "issue_scope": "mine"}]
    mine = {**item(7), "type": "Issue", "repository": {"nameWithOwner": "up/x"}}
    monkeypatch.setattr(issues, "search", lambda repos, branches=None: [item(1)] if repos == ["o/r"] else pytest.fail(f"上游仓库不该整仓读：{repos}"))
    monkeypatch.setattr(issues, "involved", lambda: {"open": [mine, pr(8, reviewDecision="CHANGES_REQUESTED"), pr(9, isDraft=False),
                                                              pr(10, author={"login": "other"})],
                                                     "merged": [pr(11, mergedAt="2026-11-10T00:00:00Z")]})
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None: ({}, ""))
    report = issues.view(projects)
    assert sorted(r["subject"] for r in report["issues"]) == ["o/r#1", "up/x#7"]
    assert report["upstream"]["up"] == {"open_prs": 2, "待审": 1, "要改": 1, "草稿": 0, "已合并": 1, "issues": 1}
    monkeypatch.setattr(issues, "detail", lambda projects, repo, number: {"title": "需求 7", "url": "https://github.com/up/x/issues/7"})
    monkeypatch.setattr(issues.agent_watch, "handoff", lambda *a, **k: pytest.fail("上游仓库不打标签"))
    with pytest.raises(ValueError, match="rules.json"):  # 上游 issue 走账本：中台没配好时不交
        issues.handoff(projects, "up/x", "7")


def linked_node(number, state="OPEN", typename="PullRequest", checks="SUCCESS"):
    return {"source": {"__typename": typename, "number": number, "title": f"PR {number}", "state": state,
                       "url": f"https://github.com/up/x/pull/{number}", "isDraft": False, "reviewDecision": "APPROVED",
                       "author": {"login": "luochen211"}, "repository": {"nameWithOwner": "up/x"},
                       "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": checks}}}]}}}


def test_linked_prs_lists_open_referencing_prs_once_and_caches(monkeypatch):
    projects = [{"id": "up", "name": "上游", "github": "up/x", "issue_scope": "mine"}]
    calls = []
    def graphql(query, kind="graphql", **variables):
        calls.append(variables)
        nodes = [linked_node(9), linked_node(8, state="MERGED"), linked_node(9), linked_node(5, typename="Issue"),
                 {"source": None}, linked_node(7, checks=None)]
        return {"repository": {"issue": {"timelineItems": {"nodes": nodes}}}}
    monkeypatch.setattr(issues.upstream.github, "graphql", graphql)
    result = issues.linked(projects, "up/x", "7274")
    assert [(p["number"], p["checks"], p["review"]) for p in result["prs"]] == [(7, "", "APPROVED"), (9, "SUCCESS", "APPROVED")]
    assert result["error"] == "" and result["limited"] == ""
    assert calls == [{"owner": "up", "name": "x", "number": 7274}]
    issues.linked(projects, "up/x", "7274")
    assert len(calls) == 1  # 5 分钟内重开详情不再请求
    with pytest.raises(ValueError, match="不在当前项目"):
        issues.linked(projects, "other/y", "1")


def test_linked_prs_rate_limited_sends_nothing(monkeypatch):
    projects = [{"id": "up", "name": "上游", "github": "up/x"}]
    monkeypatch.setattr(issues.upstream.github, "graphql", lambda *a, **k: pytest.fail("限流期间不该请求"))
    github.strike("secondary rate limit")
    result = issues.linked(projects, "up/x", "1")
    assert result["prs"] == [] and "GitHub 限流中" in result["limited"]


def test_label_only_and_lost_handoffs_can_be_handed_again(monkeypatch, tmp_path):
    from datetime import datetime, timedelta
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path))
    (tmp_path / "issues").mkdir()
    now = datetime.now().astimezone()
    (tmp_path / "issues/handoffs.json").write_text(
        '{"o/r#2": "%s", "o/r#3": "%s"}' % ((now - timedelta(minutes=20)).isoformat(), (now - timedelta(minutes=5)).isoformat()))
    monkeypatch.setattr(issues, "search", lambda repos, branches=None: [item(1, ["agent"]), item(2, ["agent"]), item(3, ["agent"]), item(4)])
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None: ({}, ""))
    rows = {r["subject"]: r for r in issues.view(PROJECTS, refresh=True)["issues"]}
    assert rows["o/r#1"]["stage"] == "已交给 Agent" and rows["o/r#1"]["stalled"] == "label_only"  # 只有标签、没有运行记录
    assert rows["o/r#2"]["stalled"] == "lost"  # 交给后 20 分钟没有新的一轮
    assert rows["o/r#3"]["stalled"] == "" and rows["o/r#4"]["stalled"] == ""
    run_ = {"id": 1, "state": "done", "created_at": "2026-11-12T01:00:00Z"}
    assert issues.stalled({"stage": "已处理", "handed_at": None, "run": run_}) == ""


def test_detail_is_cached_and_served_stale_while_limited():
    reads = []
    def read(repo, number):
        reads.append(number)
        if len(reads) > 1:
            raise github.RateLimited(time.time() + 300, "secondary rate limit")
        return {"number": 1, "title": "需求 1", "body": "正文", "url": "u", "labels": [{"name": "agent"}], "author": {"login": "me"},
                "createdAt": "2026-11-06T01:00:00Z", "updatedAt": "2026-11-06T01:00:00Z", "comments": []}
    first = issues.detail(PROJECTS, "o/r", "1", read=read)
    assert first["body"] == "正文" and not first["notice"]
    issues.detail(PROJECTS, "o/r", "1", read=read)
    assert len(reads) == 1  # 1 分钟内不再读
    github.expire("detail")
    stale = issues.detail(PROJECTS, "o/r", "1", read=read)
    assert stale["body"] == "正文" and "GitHub 限流中" in stale["notice"] and len(reads) == 2
    github.reset()
    with pytest.raises(ValueError):
        issues.detail(PROJECTS, "o/r", "1", read=lambda repo, n: (_ for _ in ()).throw(github.RateLimited(time.time() + 60, "")))


@pytest.mark.parametrize("text, expected", [
    ("结论\n\n```agent-autoloop\n{}\n```\nRESULT: PR=34", {"kind": "pr", "pr": 34}),
    ("结论\nRESULT: PR=34  \n\n", {"kind": "pr", "pr": 34}),
    ("结论\nRESULT: NO_CHANGE", {"kind": "no_change"}),
    ("结论\nRESULT: BLOCKED=推送被拒，分支受保护", {"kind": "blocked", "reason": "推送被拒，分支受保护"}),
    ("RESULT: PR=34\n后面又写了一句", None),  # 不在最后一行
    ("结论\n`RESULT: PR=34`", None),
    ("结论\n**RESULT: NO_CHANGE**", None),
    ("结论\nresult: no_change", None),
    ("结论\nRESULT: PR=", None),
    ("结论\nRESULT: PR=0", None),
    ("结论\nRESULT: BLOCKED=  ", None),
    ("结论\nRESULT: PR=34 已开", None),
    ("", None),
    ("结论\nRESULT: PR=" + "9" * 5000, None),  # 太长的号不认，也不能抛错
    ("结论\nRESULT: PR=123456789", {"kind": "pr", "pr": 123456789}),
    ("结论\nRESULT: PR=1234567890", None),
    ("结论\n说明\u2028RESULT: PR=34", None),  # 只有换行算分行
    ("结论\n说明\x0bRESULT: NO_CHANGE", None),
    ("结论\n说明\x0cRESULT: NO_CHANGE", None),
])
def test_parse_result_only_trusts_an_exact_last_line(text, expected):
    assert issues.parse_result(text) == expected


def worker_log(home, name, *messages):
    """messages 里的字符串是成功交出的消息；dict 原样写成一行（用来写出错的 result）。"""
    folder = home / "worker/runs" / name.rsplit("/", 1)[0]
    folder.mkdir(parents=True, exist_ok=True)
    lines = ['{"type":"system","subtype":"init"}', "不是 JSON"]
    lines += [json.dumps(m if isinstance(m, dict) else {"type": "result", "subtype": "success", "is_error": False, "result": m},
                         ensure_ascii=False) for m in messages]
    (folder / name.rsplit("/", 1)[1]).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_final_message_reads_the_last_attempt_and_turn_of_the_worker(tmp_path):
    hub = tmp_path / "hub"
    worker_log(hub, "12-1/stdout-1.log", "第一次\nRESULT: NO_CHANGE")
    worker_log(hub, "12-2/stdout-1.log", "第二次原样\nRESULT: PR=3")
    worker_log(hub, "12-2/stdout-2.log", "先说一句", "第二次重写回执\nRESULT: PR=4")
    worker_log(hub, "120-1/stdout-1.log", "别的轮次\nRESULT: PR=9")
    assert issues.final_message(12) == "第二次重写回执\nRESULT: PR=4"
    assert issues.final_message(13) is None


def handle_row(number, state="done", subject="o/r#1", error=""):
    return {"id": number, "rule": "处理 issue", "state": state, "summary": f"第 {number} 轮", "error": error,
            "created_at": "2026-11-12T01:00:00Z", "finished_at": f"2026-11-12T01:{number:02d}:00Z", "link": "", "subjects": [subject]}


def test_autoloop_state_attaches_results_and_remembers_them_after_logs_go(tmp_path, monkeypatch):
    hub, data = tmp_path / "hub", tmp_path / "personal"
    worker_log(hub, "5-1/stdout-1.log", "o/r#1 已提交 PR #34\n```agent-autoloop\n{}\n```\nRESULT: PR=34")
    worker_log(hub, "6-1/stdout-1.log", "o/r#2 没写结果行")
    rows = {5: handle_row(5), 6: handle_row(6, subject="o/r#2"), 7: handle_row(7, "claimed", "o/r#3"),
            8: handle_row(8, "cancelled", "o/r#4", error="连续 3 次没跑成"), 9: handle_row(9, subject="o/r#5")}
    monkeypatch.setattr(issues, "handle_runs", lambda: (rows, []))
    auto, _ = issues.autoloop_state(data)
    results = {s: r["result"] for s, r in auto["runs"].items()}
    assert results["o/r#1"] == {"kind": "pr", "pr": 34} and results["o/r#3"] is None
    assert results["o/r#2"]["kind"] == "unknown" and "RESULT" in results["o/r#2"]["reason"]
    assert results["o/r#4"] == {"kind": "failed", "reason": "连续 3 次没跑成"}
    assert results["o/r#5"]["kind"] == "unknown"  # 读不到回执：只记在内存，不写进文件
    stored = json.loads((data / "run-results.json").read_text())
    assert set(stored) == {"5", "6"} and stored["5"] == {"finished_at": "2026-11-12T01:05:00Z", "result": {"kind": "pr", "pr": 34}}
    assert [p.name for p in data.iterdir()] == ["run-results.json"]  # 临时文件换名后不留

    shutil.rmtree(hub / "worker")  # worker 日志 14 天后清理，工作台重启后照样认
    monkeypatch.setattr(issues, "_results", {})
    auto, _ = issues.autoloop_state(data)
    assert auto["runs"]["o/r#1"]["result"] == {"kind": "pr", "pr": 34}


@pytest.mark.parametrize("result, waiting, expected", [
    ({"kind": "pr", "pr": 34}, [], "等你看"),
    ({"kind": "no_change"}, [], "已处理"),
    ({"kind": "no_change"}, [{"kind": "notice"}], "等你看"),
    ({"kind": "blocked", "reason": "缺金额"}, [], "等你决定"),
    ({"kind": "pr", "pr": 34}, [{"kind": "ask"}], "等你决定"),
    ({"kind": "pr", "pr": 34}, [{"kind": "ask", "tier": "ask"}, {"kind": "ask", "tier": "default"}], "等你决定"),
    ({"kind": "pr", "pr": 34}, [{"kind": "ask", "tier": "default"}], "等你看"),  # 只剩默认过：Agent 已按推荐做了
    ({"kind": "unknown", "reason": "回执最后一行没有 RESULT 结果行"}, [], "等你看"),
    ({"kind": "failed", "reason": "连续 3 次没跑成"}, [], "等你看"),
    (None, [], "等你看"),
])
def test_stage_follows_the_result_line(result, waiting, expected):
    auto = {"runs": {"o/r#1": {"id": 5, "state": "done", "created_at": "2026-11-12T01:00:00Z", "result": result}},
            "waiting": {"o/r#1": waiting} if waiting else {}}
    assert issues.stage({"subject": "o/r#1", "labels": ["agent"]}, auto) == expected


def test_view_exposes_the_pr_link_of_the_result(monkeypatch):
    run = {"id": 5, "state": "done", "summary": "已提交 PR #34", "created_at": "2026-11-12T01:00:00Z", "result": {"kind": "pr", "pr": 34}}
    auto = {"runs": {"o/r#1": run}, "history": {"o/r#1": [run]}}
    monkeypatch.setattr(issues, "search", lambda repos, branches=None: [item(1, ["agent"])])
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None: (auto, ""))
    row = issues.view(PROJECTS, refresh=True)["issues"][0]
    assert row["stage"] == "等你看"
    assert row["run"]["result"] == {"kind": "pr", "pr": 34, "url": "https://github.com/o/r/pull/34"}
    assert row["history"][0]["result"]["url"] == "https://github.com/o/r/pull/34"
    assert "url" not in run["result"]  # 不改中台状态里的原值


def test_repo_skill_documents_exactly_the_lines_the_parser_accepts():
    text = (agent_watch.ROOT / "personal/skills/handle-issue/SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: handle-issue\n")
    for example, kind in (("RESULT: PR=34", "pr"), ("RESULT: NO_CHANGE", "no_change"), ("RESULT: BLOCKED=推送被拒，分支受保护", "blocked")):
        assert f"`{example}`" in text and issues.parse_result(example)["kind"] == kind


def test_question_tiers_come_from_the_title_prefix(monkeypatch):
    rows = {9: {"id": 9, "state": "done", "summary": "", "error": "", "created_at": "a", "finished_at": "t9",
                "link": "", "subjects": ["o/r#1"]}}
    def card(id_, title, kind="ask"):
        return {"id": id_, "kind": kind, "state": "open", "rule": "处理 issue", "createdAt": "t9", "title": title,
                "text": "", "options": ["甲", "乙"], "recommend": 0}
    questions = [card(1, "【问你】收不收费"), card(2, "【默认过】 间隔用 10 分钟"), card(3, "没写档位"),
                 card(4, "【默认过】已提交 PR", "notice")]
    monkeypatch.setattr(issues, "handle_runs", lambda: (rows, questions))
    auto, _ = issues.autoloop_state()
    shown = {q["id"]: (q["title"], q["tier"]) for q in auto["waiting"]["o/r#1"]}
    assert shown == {1: ("收不收费", "ask"), 2: ("间隔用 10 分钟", "default"), 3: ("没写档位", "ask"),
                     4: ("【默认过】已提交 PR", "")}


def test_answer_package_checks_everything_before_sending(monkeypatch):
    waiting = {"o/r#1": [{"id": 1, "kind": "ask"}, {"id": 2, "kind": "ask"}, {"id": 3, "kind": "notice"}],
               "o/r#2": [{"id": 9, "kind": "ask"}]}
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None: ({"waiting": waiting}, ""))
    calls = []
    send = lambda path, body=None: calls.append((path, body)) or {"changed": True, "state": "decided"}
    done = issues.answer_package(PROJECTS, "o/r#1", {"answers": [{"id": 1, "choice": 2}, {"id": 2, "text": " 换个做法 "}]}, send=send)
    assert done == {"message": "收到 2 条回答，Agent 接着干。"}
    assert calls == [("/v1/cards/1/answer", {"choice": 1}), ("/v1/cards/2/answer", {"text": "换个做法"})]
    for bad, why in (({"answers": []}, "至少答一条"), ({"answers": [{"id": 1, "choice": 1}, {"id": 9, "choice": 1}]}, "不在这条 issue"),
                     ({"answers": [{"id": 3, "choice": 1}]}, "不在这条 issue"),  # 处理结果不在包里
                     ({"answers": [{"id": 1, "choice": 1}, {"id": 1, "choice": 2}]}, "答了两次"),
                     ({"answers": [{"id": 1, "choice": 1}, {"id": 2}]}, "请选一个选项"),
                     ({"answers": [{"id": True, "choice": 1}]}, "不在这条 issue")):
        with pytest.raises(ValueError, match=why):
            issues.answer_package(PROJECTS, "o/r#1", bad, send=send)
    with pytest.raises(ValueError, match="不在当前项目"):
        issues.answer_package(PROJECTS, "x/y#1", {"answers": [{"id": 1, "choice": 1}]}, send=send)
    assert len(calls) == 2  # 校验不过的一条都没送

    def flaky(path, body=None):
        if path.endswith("/2/answer"):
            raise ValueError("中台没接受：没有这个选项")
        return {"changed": True}
    with pytest.raises(ValueError, match="已送出 1 条，第 2 条没送出"):
        issues.answer_package(PROJECTS, "o/r#1", {"answers": [{"id": 1, "choice": 1}, {"id": 2, "choice": 1}]}, send=flaky)
    already = lambda path, body=None: {"changed": False}
    assert "已经处理过" in issues.answer_package(PROJECTS, "o/r#1", {"answers": [{"id": 1, "choice": 1}]}, send=already)["message"]


def test_repo_skill_describes_the_decision_package():
    text = (agent_watch.ROOT / "personal/skills/handle-issue/SKILL.md").read_text(encoding="utf-8")
    for word in ("【问你】", "【默认过】"):
        assert word in text and issues.tiered(word + "标题", "ask") == ("标题", issues.TIERS[word[1:-1]])
    assert "代码能回答的事不问" in text and "同一个 PR" in text
    assert "可能分成同一个 PR 上的几轮" in text and "不要重问" in text  # 包里的回答可能分几轮到
    assert "没有 `recommend`" in text and "已按第" in text  # answers 不带推荐项，靠 about 记下这一轮做了哪项


def test_an_answer_round_without_new_commits_keeps_the_open_pr(monkeypatch):
    """只确认【默认过】的一轮可能没有新提交：技能规定仍写 PR=<号>，页面保持等你看并给 PR 链接；
    写成 NO_CHANGE 就会显示已处理、丢掉还开着的 PR，所以技能不许这样写。"""
    text = (agent_watch.ROOT / "personal/skills/handle-issue/SKILL.md").read_text(encoding="utf-8")
    assert "没有新提交也写 `RESULT: PR=<那个 PR 号>`" in text
    first = {"id": 5, "state": "done", "summary": "已提交 PR #34", "created_at": "2026-11-12T01:00:00Z",
             "result": {"kind": "pr", "pr": 34}}
    confirmed = {**first, "id": 6, "summary": "默认过的都确认了，PR #34 没有新提交", "created_at": "2026-11-12T02:00:00Z"}
    auto = {"runs": {"o/r#1": confirmed}, "history": {"o/r#1": [confirmed, first]}}
    assert issues.stage({"subject": "o/r#1", "labels": ["agent"]}, auto) == "等你看"
    monkeypatch.setattr(issues, "search", lambda repos, branches=None: [item(1, ["agent"])])
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None: (auto, ""))
    row = issues.view(PROJECTS, refresh=True)["issues"][0]
    assert row["stage"] == "等你看" and row["run"]["result"]["url"] == "https://github.com/o/r/pull/34"
    wrong = {"runs": {"o/r#1": {**confirmed, "result": {"kind": "no_change"}}}}
    assert issues.stage({"subject": "o/r#1", "labels": ["agent"]}, wrong) == "已处理"  # 写错结果行的后果


def test_final_message_skips_failed_turns_and_falls_back_to_the_previous_one(tmp_path):
    hub = tmp_path / "hub"
    worker_log(hub, "30-1/stdout-1.log", "第一回\nRESULT: PR=3")
    worker_log(hub, "30-1/stdout-2.log", "重写的半截",
               {"type": "result", "subtype": "error_max_turns", "is_error": True, "result": "没写完"})
    worker_log(hub, "30-1/stdout-3.log", {"type": "result", "subtype": "success", "is_error": True, "result": "API Error"})
    assert issues.final_message(30) == "第一回\nRESULT: PR=3"
    worker_log(hub, "31-1/stdout-1.log", "早一点的消息\nRESULT: NO_CHANGE",
               {"type": "result", "subtype": "error_during_execution", "is_error": True, "result": ""})
    assert issues.final_message(31) is None  # 这一回最后出错、又没有上一回：读不到


def test_a_receipt_that_breaks_the_parser_is_unknown_not_an_error(monkeypatch):
    def broken(text):
        raise ValueError("读坏了")
    monkeypatch.setattr(issues, "parse_result", broken)
    calls = []
    def read(run_id):
        calls.append(run_id)
        return "结论\nRESULT: PR=34"
    row = {"id": 40, "state": "done", "error": "", "summary": "", "finished_at": "2026-11-12T02:00:00Z"}
    assert issues.outcome(row, read)["kind"] == "unknown"
    assert issues.outcome(row, read)["kind"] == "unknown" and calls == [40]  # 记下了，不每次重读重抛
    monkeypatch.undo()
    huge = {**row, "id": 41}
    assert issues.outcome(huge, lambda run_id: "结论\nRESULT: PR=" + "9" * 5000) == issues.MISSING


def test_results_store_ignores_junk_and_rereads_a_reused_run_number(tmp_path, monkeypatch):
    data = tmp_path / "personal"
    data.mkdir()
    (data / "run-results.json").write_text("[1, 2]")
    assert issues.load_results(data) == {}
    (data / "run-results.json").write_text(json.dumps(
        {"5": {"finished_at": "2026-11-01T00:00:00Z", "result": {"kind": "pr", "pr": 1}}, "x": {}, "6": "坏的"}))
    assert set(issues.load_results(data)) == {5}
    row = {"id": 5, "state": "done", "error": "", "summary": "", "finished_at": "2026-11-12T01:05:00Z"}
    assert issues.outcome(row, lambda run_id: "结论\nRESULT: NO_CHANGE") == {"kind": "no_change"}  # 结束时间对不上：重读
    issues.save_results(data, issues.load_results(data))
    assert json.loads((data / "run-results.json").read_text())["5"]["result"] == {"kind": "no_change"}


def review_row(number, state="done", subject="o/r#1"):
    return {**handle_row(number, state, subject), "rule": "审查 PR", "rework": 0,
            "pr_link": "https://github.com/o/r/pull/34"}


def test_slim_reads_rework_round_and_pr_link_from_events():
    events = [{"type": "issues.rework", "subject": "o/r#1", "summary": "返工第 2 轮：PR #34 做需求",
               "link": "https://github.com/o/r/pull/34#issuecomment-9"}]
    row = issues.slim({"id": 3, "rule": "处理 issue", "events": events})
    assert row["rework"] == 2 and row["pr_link"] == "https://github.com/o/r/pull/34"
    plain = issues.slim({"id": 4, "rule": "处理 issue", "events": [{"type": "issues.opened", "subject": "o/r#1", "summary": "返工第 9 轮"}]})
    assert plain["rework"] == 0 and plain["pr_link"] == ""


def test_review_runs_carry_their_verdict_and_count_rework_rounds(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    worker_log(hub, "5-1/stdout-1.log", "已提交 PR #34\nRESULT: PR=34")
    worker_log(hub, "6-1/stdout-1.log", "要改 2 处\n```agent-autoloop\n{}\n```\nVERDICT: CHANGES_REQUIRED")
    worker_log(hub, "8-1/stdout-1.log", "审了\n没写结论")
    rows = {5: handle_row(5), 6: review_row(6), 7: {**handle_row(7, "claimed"), "rework": 1},
            8: review_row(8, subject="o/r#2")}
    monkeypatch.setattr(issues, "handle_runs", lambda: (rows, []))
    auto, _ = issues.autoloop_state()
    assert auto["runs"]["o/r#1"]["id"] == 7 and auto["reviews"]["o/r#1"]["result"] == {"kind": "review", "verdict": "CHANGES_REQUIRED"}
    assert auto["reworks"]["o/r#1"] == 1 and [r["id"] for r in auto["history"]["o/r#1"]] == [7, 6, 5]
    assert auto["reviews"]["o/r#2"]["result"]["kind"] == "unknown"
    row = {"subject": "o/r#1", "labels": ["agent"]}
    row["stage"] = issues.stage(row, auto)
    assert row["stage"] == "返工中" and issues.stage_label(row, auto) == "返工第 1 轮"


def review_auto(verdict, state="done", reworks=0, review_id=6):
    handle = {"id": 5, "state": "done", "created_at": "2026-11-12T01:00:00Z", "result": {"kind": "pr", "pr": 34}}
    review = {"id": review_id, "state": state, "result": {"kind": "review", "verdict": verdict} if verdict else None,
              "pr_link": "https://github.com/o/r/pull/34", "finished_at": None}
    return {"runs": {"o/r#1": handle}, "reviews": {"o/r#1": review}, "reworks": {"o/r#1": reworks}}


@pytest.mark.parametrize("verdict, state, reworks, review_id, expected, label", [
    (None, "claimed", 0, 6, "审查中", "审查中"),
    ("APPROVE", "done", 0, 6, "等你看", "等你看"),
    ("WAIT", "done", 0, 6, "等你看", "等你看"),
    ("CHANGES_REQUIRED", "done", 0, 6, "返工中", "返工第 1 轮"),
    ("CHANGES_REQUIRED", "done", 2, 6, "返工中", "返工第 3 轮"),
    ("CHANGES_REQUIRED", "done", 3, 6, "等你决定", "等你决定"),  # 返工满 3 轮：停下等本人
    ("CHANGES_REQUIRED", "done", 1, 4, "等你看", "等你看"),  # 审的是更早的一轮，之后返工开了新提交
])
def test_stage_follows_the_review_after_a_pr(verdict, state, reworks, review_id, expected, label):
    auto = review_auto(verdict, state, reworks, review_id)
    row = {"subject": "o/r#1", "labels": ["agent"]}
    row["stage"] = issues.stage(row, auto)
    assert (row["stage"], issues.stage_label(row, auto)) == (expected, label)


def test_view_exposes_review_progress(monkeypatch):
    auto = {**review_auto("CHANGES_REQUIRED", reworks=3), "history": {}}
    monkeypatch.setattr(issues, "search", lambda repos, branches=None: [item(1, ["agent"])])
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None: (auto, ""))
    row = issues.view(PROJECTS, refresh=True)["issues"][0]
    assert row["stage"] == "等你决定" and row["review"] == {
        "id": 6, "state": "done", "active": False, "verdict": "CHANGES_REQUIRED", "current": True, "moved": False, "spent": False,
        "sha": "", "head": "", "hold": "", "reworks": 3, "max": 3, "url": "https://github.com/o/r/pull/34", "at": None}


SHA, NEW = "a" * 40, "b" * 40


def head_row(sha, number=34):
    return {"type": "PullRequestHead", "repository": {"nameWithOwner": "o/r"}, "number": number, "headRefOid": sha}


def test_slim_reads_the_reviewed_commit_from_the_review_link():
    events = [{"type": "pr.review", "subject": "o/r#1", "summary": "审查 PR #34", "link": f"https://github.com/o/r/pull/34/commits/{SHA}"}]
    row = issues.slim({"id": 6, "rule": "审查 PR", "events": events})
    assert row["sha"] == SHA and row["pr_link"] == "https://github.com/o/r/pull/34"
    legacy = issues.slim({"id": 6, "rule": "审查 PR", "events": [{**events[0], "link": "https://github.com/o/r/pull/34"}]})
    assert legacy["sha"] == "" and legacy["pr_link"] == "https://github.com/o/r/pull/34"


def view_with(monkeypatch, auto, heads=()):
    monkeypatch.setattr(issues, "search", lambda repos, branches=None: [item(1, ["agent"])] + [head_row(h) for h in heads])
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None: ({"history": {}, **auto}, ""))
    [row] = issues.view(PROJECTS, refresh=True)["issues"]
    return row


def test_a_verdict_counts_only_for_the_commit_it_reviewed(monkeypatch):
    auto = review_auto("CHANGES_REQUIRED", reworks=1)
    auto["reviews"]["o/r#1"]["sha"] = SHA
    row = view_with(monkeypatch, auto, [SHA])
    assert row["stage"] == "返工中" and row["review"]["current"] and row["review"]["sha"] == SHA[:12]
    row = view_with(monkeypatch, auto, [NEW])  # 审完之后 PR 有了新提交：结论不算数，等重新审查
    assert row["stage"] == "等你看" and not row["review"]["current"] and row["review"]["moved"]
    assert (row["review"]["sha"], row["review"]["head"]) == (SHA[:12], NEW[:12])
    # 决定包的回答轮排在审查之后（轮次编号更大）但没有新提交：结论照样算数
    answered = {**auto, "runs": {"o/r#1": {**auto["runs"]["o/r#1"], "id": 9}}}
    row = view_with(monkeypatch, answered, [SHA])
    assert row["stage"] == "返工中" and row["review"]["current"]
    # 旧事件不知道审的提交：退回比轮次
    legacy = {**answered, "reviews": {"o/r#1": {**auto["reviews"]["o/r#1"], "sha": ""}}}
    assert not view_with(monkeypatch, legacy, [SHA])["review"]["current"]


def test_a_stale_review_round_waits_for_the_next_review(monkeypatch):
    auto = review_auto("STALE")
    row = view_with(monkeypatch, auto)
    assert row["stage"] == "等你看" and row["review"]["moved"] and not row["review"]["current"]
    assert issues.parse_review("没事可做：PR 已有新提交\nVERDICT: STALE") == {"kind": "review", "verdict": "STALE"}


def test_the_watch_ledger_feeds_rework_rounds_and_holds(monkeypatch, tmp_path):
    agent_watch.write_json(tmp_path / "hub/issues/dispatched.json", {
        "sent": {"rework:o/r#34@1": {"at": "2026-11-12T01:00:00+00:00", "kind": "rework", "pr": "o/r#34", "subject": "o/r#1",
                                     "head": SHA, "round": 1},
                 "rework:o/r#34@2": {"at": "2026-11-12T02:00:00+00:00", "kind": "rework", "pr": "o/r#34", "subject": "o/r#1",
                                     "head": NEW, "round": 2}},
        "told": {}, "holds": {"o/r#34": {"subject": "o/r#1", "head": NEW, "why": "CI 没过，未审查", "since": "2026-11-12T03:00:00+00:00"}}})
    handle = {"id": 5, "state": "done", "created_at": "2026-11-12T01:00:00Z", "result": {"kind": "pr", "pr": 34}}
    row = view_with(monkeypatch, {"runs": {"o/r#1": handle}}, [NEW])
    assert row["review"]["hold"] == "CI 没过，未审查" and row["review"]["reworks"] == 2
    assert row["review"]["url"] == "https://github.com/o/r/pull/34" and row["review"]["id"] is None
    assert view_with(monkeypatch, {"runs": {"o/r#1": handle}}, ["c" * 40])["review"] is None  # 记的是旧提交的原因


def test_open_issues_reads_agent_pr_heads_in_the_same_request(monkeypatch):
    calls = []
    def graphql(query, kind="graphql", **kwargs):
        calls.append(kind)
        node = {"number": 1, "title": "t", "url": "u", "createdAt": "x", "updatedAt": "x", "author": None,
                "labels": {"nodes": []}, "comments": {"totalCount": 0}}
        prs = [{"number": 34, "headRefName": "agent/issue-1", "headRefOid": SHA}, {"number": 35, "headRefName": "feat/x", "headRefOid": NEW}]
        return {"r0": {"nameWithOwner": "o/r", "issues": {"nodes": [node]}, "pullRequests": {"nodes": prs},
                       "defaultBranchRef": {"name": "main", "target": {"committedDate": "2026-11-17T00:00:00Z"}}}}
    monkeypatch.setattr(upstream.github, "graphql", graphql)
    rows = upstream.open_issues(["o/r"])
    assert [r["type"] for r in rows] == [upstream.REPO_INFO, "Issue", upstream.HEAD] and rows[2]["headRefOid"] == SHA
    assert calls == ["graphql:issues"]
    assert (rows[0]["branch"], rows[0]["committed_at"]) == ("main", "2026-11-17T00:00:00Z")  # 主线最近一次提交也在同一个请求里


def test_review_notices_show_up_on_the_issue(monkeypatch):
    rows = {6: review_row(6)}
    questions = [{"id": 300, "kind": "notice", "state": "open", "rule": "审查 PR", "createdAt": rows[6]["finished_at"],
                  "title": "o/r#1 的 PR #34 审查通过", "text": "可以合并", "options": [], "recommend": None}]
    monkeypatch.setattr(issues, "handle_runs", lambda: (rows, questions))
    auto, _ = issues.autoloop_state()
    assert [q["id"] for q in auto["waiting"]["o/r#1"]] == [300]


def test_a_rework_round_that_pushed_nothing_waits_for_the_owner(monkeypatch, tmp_path):
    """返工那一轮结束了却没推新提交（误写了 RESULT: PR=）：监看不会对同一提交再派返工，页面不能一直写「返工第 2 轮」。"""
    agent_watch.write_json(tmp_path / "hub/issues/dispatched.json", {"sent": {"rework:o/r#34@1": {
        "at": "2026-11-12T01:00:00+00:00", "kind": "rework", "pr": "o/r#34", "subject": "o/r#1", "head": SHA, "round": 1}}})
    auto = review_auto("CHANGES_REQUIRED", reworks=1)
    auto["reviews"]["o/r#1"]["sha"] = SHA
    rework = {"id": 7, "state": "done", "rework": 1, "created_at": "2026-11-12T01:00:00Z", "result": {"kind": "pr", "pr": 34}}
    auto["runs"]["o/r#1"] = rework
    row = view_with(monkeypatch, {**auto, "history": {"o/r#1": [rework]}}, [SHA])
    assert row["stage"] == "等你决定" and row["review"]["spent"]
    row = view_with(monkeypatch, {**auto, "history": {"o/r#1": []}}, [SHA])  # 返工那一轮还没出现：照常算返工中
    assert row["stage"] == "返工中" and not row["review"]["spent"]
    row = view_with(monkeypatch, {**auto, "history": {"o/r#1": [rework]}}, [NEW])  # 推了新提交：等重新审查
    assert row["stage"] == "等你看" and not row["review"]["spent"]
