"""「待动手」的漏判改判（personal/next_step.demote，用户 2026-11-18 按三份逐条审计定）：每条规则一个测试，外加列表读取和交给接口的把关。"""
from datetime import datetime, timezone

import pytest

from personal import __main__ as app
from personal import agent_watch, github, issues, next_step, pr_feedback, upstream, conversation

ME = "luochen211"
NOW = datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc)
MAIN = {"branch": "main", "committed_at": "2026-11-17T00:00:00Z"}  # 主线昨天还有提交
OWN = {"id": "app", "name": "工具", "group": "个人工具", "github": "luochen211/app"}
PAUSED = {"id": "chat", "name": "聊天", "group": "商业与交付", "github": "luochen211/chat", "paused": True, "paused_note": "客户暂无新需求"}


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    def offline(args, kind, **kwargs):
        raise AssertionError(f"测试不该真的请求 GitHub：{args}")
    monkeypatch.setattr(github, "run", offline)
    monkeypatch.setattr(next_step, "now", lambda: NOW)
    github.reset()
    pr_feedback.reset()
    monkeypatch.setattr(issues, "_results", {})
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path / "hub"))
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    (tmp_path / "hub/issues").mkdir(parents=True)
    yield
    github.reset()
    pr_feedback.reset()


def said(login, at, text):
    return {"__typename": "IssueComment", "author": {"__typename": "User", "login": login}, "authorAssociation": "OWNER",
            "bodyText": text, "createdAt": at, "createdViaEmail": False, "url": f"https://github.com/c#{at[5:13]}"}


def ref(number, state="MERGED", closes=False, base="main"):
    return {"__typename": "CrossReferencedEvent", "willCloseTarget": closes,
            "source": {"__typename": "PullRequest", "number": number, "title": f"PR {number}", "url": f"https://github.com/x/pull/{number}",
                       "state": state, "baseRefName": base, "mergedAt": "2026-11-09T00:00:00Z" if state == "MERGED" else None}}


def node(*items, number=5, title="修一处", body="要做：改一处。", markdown=None, labels=(), assignees=(), created="2026-11-10T00:00:00Z",
         updated=None, repo="luochen211/app", author=ME):
    return {"number": number, "title": title, "url": f"https://github.com/{repo}/issues/{number}", "createdAt": created,
            "updatedAt": updated or created, "bodyText": body, "body": body if markdown is None else markdown,
            "author": {"__typename": "User", "login": author}, "labels": {"nodes": [{"name": x} for x in labels]},
            "assignees": {"nodes": [{"login": x} for x in assignees]}, "milestone": None,
            "comments": {"totalCount": sum(i["__typename"] == "IssueComment" for i in items)}, "timelineItems": {"nodes": list(items)}}


def row(*items, repo="luochen211/app", **fields):
    return upstream.item(node(*items, repo=repo, **fields), repo)


def classify(*items, repo="luochen211/app", main=MAIN, paused=None, notes=(), open_numbers=None, flow=None, **fields):
    r = row(*items, repo=repo, **fields)
    return next_step.classify(repo=repo, author=r["author"]["login"], thread=r["thread"], refs=r["refs"], flow=flow,
                              title=r["title"], labels=[x["name"] for x in r["labels"]],
                              assignees=r["assignees"], plan=r["plan"], unchecked=r["unchecked"], text=r["text"],
                              updated_at=r["updatedAt"], main=main, paused=paused, notes=notes, open_numbers=open_numbers)


def test_list_node_carries_the_signals_and_repo_mainline():
    r = row(ref(95, base="ios"), markdown="<!-- project-plan: x -->\n- [ ] 一\n- [x] 二\n- [ ] 三", labels=["question"], assignees=["boss"])
    assert (r["plan"], r["unchecked"], r["assignees"]) == (True, 2, ["boss"])
    assert r["refs"][0]["base"] == "ios" and r["refs"][0]["merged_at"] == "2026-11-09T00:00:00Z"
    assert "body" not in r and "body" not in r["thread"]  # 原文读完就丢


def test_rule1_body_only_counts_as_todo_only_while_fresh():
    fresh_one = classify(created="2026-11-12T00:00:00Z")
    assert fresh_one["key"] == "todo" and fresh_one["button"] == "交给 Agent"
    old = classify(created="2026-10-07T00:00:00Z")
    assert old["key"] == "look" and old["line"] == "你 10-07 开的，只有正文、没有留言" and old["drawer_only"]
    commented = classify(said(ME, "2026-10-08T00:00:00Z", "进展：下一步写导入。"), created="2026-10-07T00:00:00Z")
    assert commented["key"] == "todo"  # 有你的留言：照原来的规则


def test_rule2_merged_pr_on_the_mainline_waits_for_close():
    out = classify(ref(95))
    assert out["key"] == "close" and out["line"] == "PR #95 已合并，可验收关闭" and out["button"] == ""
    assert next_step.bucket(out) == "wait"
    assert classify(ref(94, base="master"))["key"] == "todo"  # 合在别的分支不算
    ios = classify(ref(95, base="ios-chat-refresh-fix"), main={"branch": "ios-chat-refresh-fix", "committed_at": "2026-11-09T00:00:00Z"})
    assert ios["key"] == "close"  # projects.toml 配了 main_branch：按它算
    partial = classify(ref(74), markdown="- [ ] 一行多件加间隔\n- [ ] 起发门槛")
    assert partial["key"] == "close" and partial["line"] == "PR #74 已合并，部分已做（正文还有 2 项没勾）"
    assert classify(ref(80, state="OPEN"))["key"] == "pr"


@pytest.mark.parametrize("fields, line", [
    ({"title": "已知问题跟踪（2.0.1 快照）"}, "标题写了「跟踪」，是记录"),
    ({"title": "[总览] v0.2.0 交付进度"}, "标题写了「总览」，是记录"),
    ({"title": "园区试点规划：进度和下一步"}, "标题写了「进度」，是记录"),
    ({"title": "Epic: deliver V1.0"}, "标题写了「Epic」，是记录"),
    ({"labels": ["roadmap:epic"]}, "标签 roadmap:epic，是记录"),
    ({"markdown": "<!-- project-plan: mixcut -->\n目标"}, "正文带自动生成的规划标记，是记录"),
])
def test_rule3_records_are_not_work(fields, line):
    out = classify(**fields)
    assert out["key"] == "record" and out["line"] == line and out["button"] == "" and next_step.bucket(out) == "record"


@pytest.mark.parametrize("title", ["写入表 1 与处理记录表", "演练模式自测清单与自测记录", "一段话拆成多条记录（M08）"])
def test_rule3_feature_names_with_record_words_stay_work(title):
    assert classify(title=title)["key"] == "todo"


@pytest.mark.parametrize("fields, line", [
    ({"body": "要做：部署。\n依赖：王静文的 Cloudflare 权限。"}, "等对方：正文写了「依赖：王静文的 Cloudflare 权限」"),
    ({"body": "请总头登记这几个键。"}, "等对方：正文写了「请总头登记这几个键」"),
    ({"body": "短信登录等子公司注册下来再做。"}, "等对方：正文写了「等子公司注册下来再做」"),
    ({"body": "营收 100 万元后再做 App。"}, "等对方：正文写了「营收 100 万元后再做 App」"),
    ({"body": "单价待客户确认。"}, "等对方：正文写了「待客户确认」"),
    ({"title": "待确认：首发行业和抽样名单"}, "等对方：标题写了待确认"),
    ({"labels": ["needs-backend"]}, "等对方：标签 needs-backend"),
    ({"labels": ["question"]}, "等对方：标签 question"),
    ({"assignees": ["yichuan0817"]}, "等对方：指派给了 yichuan0817"),
])
def test_rule4_conditions_wait_on_the_other_side(fields, line):
    out = classify(**fields)
    assert out["key"] == "wait" and out["line"] == line and out["button"] == ""


def test_rule4_settled_or_empty_dependencies_do_not_wait():
    assert classify(body="依赖：无，按原稿做。")["key"] == "todo"
    assert classify(body="依赖：#4 #9", open_numbers={5, 12})["key"] == "todo"  # 依赖的都已关闭
    assert classify(body="依赖：#4 #9", open_numbers={4, 5})["key"] == "wait"  # #4 还开着
    assert classify(body="把成片交给 Postiz 创建待确认草稿。")["key"] == "todo"  # 功能名
    assert classify(assignees=[ME])["key"] == "todo"  # 指派给自己


def test_rule5_statements_from_the_issues_that_reference_it():
    table = "子议题\n状态\n#16 骨架\n代码已合，请验收后关\n#26（合并 244f8d8）\n#21 回调公网入口\n等王子定（已暂停）\n恢复时要入口方案"
    rows = [row(said(ME, "2026-11-05T05:39:16Z", table), number=24, title="Qiwe 拆分总表"),
            row(said(ME, "2026-11-05T05:33:36Z", "按 #34 那条：暂不实测。\n请你：\n看看 #6、#7 能不能关。\n#58 发送前复查暂停；一行多件无间隔"),
                number=11),
            row(number=34, body="QWPad 有封号记录，暂不实测（#23 要的编号不急了）\n#3、#4：暂停，等王子")]
    notes = next_step.statements(rows)
    assert {k: [n["kind"] for n in v] for k, v in notes.items()} == {
        "luochen211/app#16": ["close"], "luochen211/app#21": ["hold"], "luochen211/app#6": ["close"], "luochen211/app#7": ["close"],
        "luochen211/app#3": ["hold"], "luochen211/app#4": ["hold"]}
    assert "luochen211/app#34" not in notes  # 「按 #34」是引用出处，不是说它
    assert "luochen211/app#23" not in notes and "luochen211/app#58" not in notes  # 暂停写在编号前面；标题里的「暂停」
    closing = classify(number=16, notes=notes["luochen211/app#16"])
    assert closing["key"] == "close" and closing["line"] == "#24 里 luochen211 11-05 写了「请验收后关」"
    holding = classify(number=21, notes=notes["luochen211/app#21"])
    assert holding["key"] == "hold" and next_step.bucket(holding) == "hold" and holding["button"] == ""


def test_rule5_title_cells_with_pause_words_are_not_statements():
    notes = next_step.statements([row(said(ME, "2026-11-05T00:00:00Z", "#58 同批已领行不复查暂停\n第一批"), number=55)])
    assert "luochen211/app#58" not in notes


def test_rule6_long_quiet_issue_in_a_quiet_repo_is_on_hold():
    old = {"created": "2026-02-08T00:00:00Z", "updated": "2026-02-15T00:00:00Z"}
    quiet = {"branch": "main", "committed_at": "2026-10-01T00:00:00Z"}
    out = classify(said(ME, "2026-02-15T00:00:00Z", "进展：下一步补测试。"), main=quiet, **old)
    assert out["key"] == "hold" and out["line"] == "最后动静 2026-02-15，主线 2026-10-01 后没有提交"
    assert classify(said(ME, "2026-02-15T00:00:00Z", "进展：下一步补测试。"), **old)["key"] == "todo"  # 主线还在提交
    assert classify(said(ME, "2026-02-15T00:00:00Z", "进展：下一步补测试。"), main=None, **old)["key"] == "todo"  # 不知道主线


def test_rule7_paused_project_puts_everything_on_hold_except_agent_flow():
    out = classify(paused="客户暂无新需求")
    assert out["key"] == "hold" and out["line"] == "项目已暂停：客户暂无新需求" and out["button"] == ""
    theirs = classify(said("boss", "2026-11-17T00:00:00Z", "@luochen211 这里要改"), paused="")
    assert theirs["key"] == "hold" and theirs["line"] == "项目已暂停"
    flow = {"stage": "处理中", "label": "处理中", "kind": "issue", "at": None}
    assert classify(paused="x", flow=flow)["key"] == "flow"
    assert issues.paused_note(PAUSED) == "客户暂无新需求" and issues.paused_note(OWN) is None
    assert issues.paused_note({**PAUSED, "paused": "yes"}) is None


def test_rule7_config_validates_the_new_keys():
    assert app.validate([{**PAUSED, "main_branch": "ios"}])
    for bad in ({"paused": "true"}, {"paused_note": 1}, {"main_branch": ["ios"]}):
        with pytest.raises(ValueError):
            app.validate([{**OWN, **bad}])


@pytest.mark.parametrize("fields", [{"body": "重复 #49，后端拆分版"}, {"body": "Duplicate of #12"}, {"labels": ["duplicate"]}])
def test_rule8_duplicates_wait_for_close(fields):
    out = classify(**fields)
    assert out["key"] == "close" and out["line"] in ("和 #49 重复，可关闭", "和 #12 重复，可关闭", "标了 duplicate，可关闭")
    assert classify(said(ME, "2026-11-11T00:00:00Z", "与 #42 重合，留那条"))["line"] == "和 #42 重复，可关闭"


def test_someone_waiting_on_you_is_never_demoted():
    theirs = said("boss", "2026-11-17T00:00:00Z", "@luochen211 这个还要吗？")
    assert classify(theirs, title="进度总表", labels=["question"], author="boss")["key"] == "reply"


def test_handoff_gate_refuses_hold_and_record():
    for key, words in [("hold", "搁置"), ("record", "记录")]:
        route, reason = next_step.allowed({"key": key, "action": ""})
        assert route == "" and words in reason and "卡" not in reason


def test_view_wires_paused_projects_mainline_and_statements(monkeypatch):
    rows = [row(number=1, repo="luochen211/chat"),
            row(said(ME, "2026-11-05T00:00:00Z", "看看 #3 能不能关。"), number=2, repo="luochen211/app"),
            row(number=3, repo="luochen211/app"), row(ref(9, base="ios"), number=4, repo="luochen211/app"),
            {"type": upstream.REPO_INFO, "repository": {"nameWithOwner": "luochen211/app"}, "branch": "ios", "committed_at": "2026-11-17T00:00:00Z"}]
    seen = {}

    def search(repos, branches=None):
        seen["branches"] = branches
        return rows
    monkeypatch.setattr(issues, "search", search)
    monkeypatch.setattr(issues, "involved", lambda: {"open": [], "merged": []})
    monkeypatch.setattr(issues, "autoloop_state", lambda data=None, rule=None, parse=None: ({}, ""))
    monkeypatch.setattr(issues, "feedback_state", lambda data=None: ({"runs": {}, "history": {}, "waiting": {}, "handed": {}}, ""))
    out = {r["subject"]: r["next"] for r in issues.view([{**OWN, "main_branch": "ios"}, PAUSED])["issues"]}
    assert seen["branches"] == {"luochen211/app": "ios"}
    assert (out["luochen211/chat#1"]["bucket"], out["luochen211/chat#1"]["line"]) == ("hold", "项目已暂停：客户暂无新需求")
    assert out["luochen211/app#3"]["key"] == "close" and "#2 里" in out["luochen211/app#3"]["line"]
    assert out["luochen211/app#4"]["line"] == "PR #9 已合并，可验收关闭"
    monkeypatch.setattr(agent_watch, "handoff", lambda *a, **k: pytest.fail("不该打标签"))
    with pytest.raises(ValueError, match="搁置"):
        issues.handoff([{**OWN, "main_branch": "ios"}, PAUSED], "luochen211/chat", 1)
    batch = issues.batch_handoff([{**OWN, "main_branch": "ios"}, PAUSED], ["luochen211/chat#1"])
    assert not batch["results"][0]["ok"] and "搁置" in batch["results"][0]["error"]
