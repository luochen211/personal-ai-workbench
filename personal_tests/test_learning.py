"""「学习」标签：只读学习课题目录，只在保存反馈和点「写下一篇」时写。全部用临时目录造的假课题。"""
import json
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from personal import agent_watch, learning
from personal.errors import APIError
from personal_tests.test_workspace import instance

SUBJECT = learning.subject("组织学")
HUB_SUBJECT = r"[A-Za-z0-9#._:/@-]*"  # 中台对事件 subject 的校验，带中文会被整条拒绝


def test_handoff_subjects_pass_the_hub_check():
    import re

    from personal import review
    assert re.fullmatch(HUB_SUBJECT, SUBJECT) and SUBJECT != learning.subject("沟通表达")
    assert re.fullmatch(HUB_SUBJECT, review.subject("week", "2026-W47"))
    assert re.fullmatch(HUB_SUBJECT, review.subject("month", "2026-11"))

CST = timezone(timedelta(hours=8))
T0 = datetime(2026, 11, 18, 16, 0, tzinfo=CST)
INDEX = """# 学习课题索引

生成网页：`python3 -I _tools/build_learning.py <课题目录>`

| 课题 | 当前进度 | 最近更新 | 下一步 |
|---|---:|---|---|
| 多巴胺 | 21（15–21 已重写） | 2026-11-15 | 从 15 往下读新版，逐篇填反馈 |
| 组织学 | 02 | 2026-11-18 | 03 考核与激励（材料从 参考07 取）；01 有出入待改 |
| 没目录 | 01 | 2026-11-19 | 先建目录 |
"""
FEEDBACK = ("\n\n---\n\n## 学习反馈\n\n你可以写：\n\n1. 哪里看懂了？\n2. 哪里没看懂？\n3. 哪个地方想展开？\n"
            "4. 这个主题和你的真实问题有什么关系？\n\n请写在这行下面：\n")


def article(number, title, said=""):
    return f"# {number}｜{title}\n\n## 这一篇要解决的问题\n\n正文。\n" + FEEDBACK + (f"\n{said}\n" if said else "")


@pytest.fixture
def base(tmp_path, monkeypatch):
    root = tmp_path / "学习课题"
    monkeypatch.setenv("LEARNING_ROOT", str(root))
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path / "hub"))
    org = root / "组织学"
    (org / "assets").mkdir(parents=True)
    (root / "INDEX.md").write_text(INDEX, encoding="utf-8")
    (org / "00-学习计划.md").write_text("# 组织学｜学习计划\n\n## 学习目标\n\n学会。\n", encoding="utf-8")
    (org / "01.md").write_text(article("01", "组织与公司", "老板说的一定对吗？"), encoding="utf-8")
    (org / "02.md").write_text(article("02", "层级为什么存在"), encoding="utf-8")
    (org / "01.html").write_text("<!doctype html><p>01</p>", encoding="utf-8")
    (org / "index.html").write_text("<!doctype html><h2 id=\"学习目标\">学习目标</h2>", encoding="utf-8")
    (org / "参考07-考核与激励.md").write_text("# 参考\n", encoding="utf-8")
    (org / "assets" / "a.png").write_bytes(b"\x89PNG")
    (org / ".secret.html").write_text("x", encoding="utf-8")
    dopa = root / "多巴胺"
    dopa.mkdir()
    for n in range(1, 4):
        (dopa / f"{n:02d}.md").write_text(article(f"{n:02d}", f"第 {n} 篇", "懂了" if n == 2 else ""), encoding="utf-8")
    (dopa / "00-学习计划.md").write_text("# 计划\n", encoding="utf-8")
    (root / "outside.md").write_text("不在课题里", encoding="utf-8")
    return root


def rules_with_learning(home):
    """中台已有「处理 issue」、两条监看规则，并已接上「写学习文章」。"""
    path = home / "hub" / "rules.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    emits = {"source": "github", "types": ["issues.opened"]}
    data = {"sources": {"github": {}}, "agents": {"issue-watch": {}, "claude-issue": {}, "claude-feedback": {}}, "rules": [
        {"name": "看带 agent 标签的 issue", "when": {"cron": "*/10 * * * *"}, "to": "issue-watch", "emits": dict(emits)},
        {"name": "立即看 agent issue", "when": {"source": "local", "types": ["issues.check"]}, "to": "issue-watch", "emits": dict(emits)},
        {"name": "处理 issue", "when": {"source": "github", "types": ["issues.opened"]}, "to": "claude-issue", "cwd": "/Volumes/Luochen"}]}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_index_lists_topics_with_cells_copied_verbatim(base):
    data = learning.view(at=T0)
    names = [t["name"] for t in data["topics"]]
    assert names == ["多巴胺", "组织学", "没目录"]
    org = data["topics"][1]
    assert org["progress"] == "02" and org["updated"] == "2026-11-18"
    assert org["next"] == "03 考核与激励（材料从 参考07 取）；01 有出入待改"
    assert data["default"] == "组织学"  # 最近更新、目录在的那个；没目录的不算
    assert data["topics"][2]["exists"] is False and data["topics"][2]["articles"] == []


def test_articles_are_numbered_titled_and_marked_for_feedback(base):
    org = learning.view(at=T0)["topics"][1]
    assert [(a["no"], a["title"], a["html"], a["filled"]) for a in org["articles"]] == [
        ("01", "组织与公司", True, True), ("02", "层级为什么存在", False, False)]
    assert org["articles"][0]["feedback"] == "老板说的一定对吗？"
    assert org["next_no"] == "03" and org["unfilled"] == ["02"]
    assert org["home"] and org["plan"] and org["handoff"]["state"] == "idle"
    assert learning.view(at=T0)["topics"][0]["unfilled"] == ["01", "03"]


def test_feedback_extraction_ignores_the_template_prompts():
    assert learning.feedback_of(article("01", "x")) == ""
    assert learning.feedback_of(article("01", "x", "1. 哪里看懂了？\n都懂")) == "1. 哪里看懂了？\n都懂"
    assert learning.feedback_of("# 01\n\n## 学习反馈\n\n你可以写：\n\n1. 哪里看懂了？\n\n自己写的\n") == "自己写的"
    assert learning.feedback_of("# 01\n\n正文\n") == ""


def test_writing_feedback_only_touches_the_part_after_the_marker():
    text = article("01", "x", "旧的")
    out = learning.with_feedback(text, "新的\r\n第二行  ")
    assert out == article("01", "x") + "\n新的\n第二行\n"
    assert out.split("请写在这行下面：")[0] == text.split("请写在这行下面：")[0]
    assert learning.with_feedback(text, "") == article("01", "x")
    bare = learning.with_feedback("# 01｜x\n\n正文\n", "写了")
    assert bare.startswith("# 01｜x\n\n正文\n\n---\n\n## 学习反馈") and learning.feedback_of(bare) == "写了"


def test_save_feedback_writes_back_to_the_article_and_checks_the_version(base):
    org = learning.view(at=T0)["topics"][1]
    second = org["articles"][1]
    saved = learning.save_feedback({"topic": "组织学", "no": "02", "text": "层级看懂了", "version": second["version"]})
    path = base / "组织学" / "02.md"
    assert saved["filled"] and learning.feedback_of(path.read_text(encoding="utf-8")) == "层级看懂了"
    assert path.read_text(encoding="utf-8").startswith("# 02｜层级为什么存在\n\n## 这一篇要解决的问题\n\n正文。\n")
    with pytest.raises(APIError) as stale:  # 用打开时的旧版本号再存：文章已经变了，不覆盖
        learning.save_feedback({"topic": "组织学", "no": "02", "text": "覆盖", "version": second["version"]})
    assert stale.value.status == 409 and "层级看懂了" in path.read_text(encoding="utf-8")
    assert sorted(p.name for p in path.parent.iterdir() if p.name.startswith(".")) == [".secret.html"]  # 不留临时文件
    unchanged = path.stat().st_mtime_ns
    learning.save_feedback({"topic": "组织学", "no": "02", "text": "层级看懂了\n"})  # 内容没变就不写
    assert path.stat().st_mtime_ns == unchanged


@pytest.mark.parametrize("body, status", [
    ({"topic": "../组织学", "no": "01", "text": "x"}, 404),
    ({"topic": "没登记", "no": "01", "text": "x"}, 404),
    ({"topic": "没目录", "no": "01", "text": "x"}, 404),
    ({"topic": "组织学", "no": "09", "text": "x"}, 404),
    ({"topic": "组织学", "no": "1", "text": "x"}, 400),
    ({"topic": "组织学", "no": "00", "text": "x"}, 400),
    ({"topic": "组织学", "no": "../01", "text": "x"}, 400),
    ({"topic": "组织学", "no": "01", "text": 3}, 400),
    ({"topic": "组织学", "no": "01", "text": "x" * 20001}, 400),
])
def test_save_feedback_rejects_anything_outside_registered_articles(base, body, status):
    before = (base / "组织学" / "01.md").read_text(encoding="utf-8")
    with pytest.raises((APIError, ValueError)) as error:
        learning.save_feedback(body)
    assert (getattr(error.value, "status", 400)) == status
    assert (base / "组织学" / "01.md").read_text(encoding="utf-8") == before


def test_next_article_is_handed_over_only_when_asked(base):
    rules_with_learning(base.parent)
    with pytest.raises(ValueError) as missing:  # 中台还没有「写学习文章」：一条不记
        learning.handoff({"topic": "组织学"}, at=T0, trigger=lambda *a: "x", read_runs=lambda: ({}, ""))
    assert "写学习文章" in str(missing.value) and not agent_watch.learning_ledger_path().exists()
    agent_watch.ensure_rules(base.parent / "hub" / "rules.json")
    assert learning.view(at=T0)["topics"][1]["handoff"]["state"] == "idle"  # 看列表不交给
    assert agent_watch.learning_events() == []
    fired = []
    result = learning.handoff({"topic": "组织学"}, at=T0, trigger=lambda *a: fired.append(a) or "routed", read_runs=lambda: ({}, ""))
    assert result["no"] == "03" and result["error"] == "" and len(fired) == 1 and fired[0][0] == SUBJECT
    entry = agent_watch.load_handoffs(agent_watch.learning_ledger_path())[SUBJECT]
    assert entry["no"] == "03" and "第 03 篇" in entry["detail"] and "RESULT: ARTICLE=03" in entry["detail"]
    assert "03 考核与激励（材料从 参考07 取）" in entry["detail"] and "build_learning.py" in entry["detail"]
    assert "不要停下来问" in entry["detail"] and "不给书单" in entry["detail"]
    with pytest.raises(APIError) as twice:  # 双击：已交给还在派，不再交
        learning.handoff({"topic": "组织学"}, at=T0 + timedelta(minutes=1), trigger=lambda *a: fired.append(a), read_runs=lambda: ({}, ""))
    assert twice.value.status == 409 and len(fired) == 1
    events = agent_watch.learning_events(at=T0 + timedelta(minutes=2))
    assert [(e["type"], e["subject"]) for e in events] == [("learning.next", SUBJECT)]
    assert events[0]["id"].startswith("learning:" + SUBJECT + "@")
    assert (base / "组织学" / "03.md").exists() is False  # 工作台自己不写文章
    state = learning.view(at=T0 + timedelta(minutes=2), read_runs=lambda: ({}, ""))["topics"][1]["handoff"]
    assert state["state"] == "pending" and state["no"] == "03"


def test_handoff_progress_follows_the_hub_run(base):
    entry = {"handed_at": T0.isoformat(), "no": "03"}
    run = lambda state, result=None, at=T0 + timedelta(minutes=1): {"id": 7, "state": state, "created_at": at.isoformat(),
                                                                   "finished_at": None, "result": result, "error": ""}
    assert learning.status(entry, None, T0 + timedelta(minutes=5))["state"] == "pending"
    assert learning.status(entry, None, T0 + timedelta(minutes=20))["state"] == "lost"
    assert learning.status(entry, run("claimed"), T0)["state"] == "running"
    assert learning.status(entry, run("done", {"kind": "article", "no": "03"}), T0)["state"] == "done"
    blocked = learning.status(entry, run("done", {"kind": "blocked", "reason": "没网"}), T0)
    assert blocked["state"] == "blocked" and blocked["reason"] == "没网"
    old = run("done", {"kind": "article", "no": "02"}, at=T0 - timedelta(days=1))  # 交给之前的旧一轮不算
    assert learning.status(entry, old, T0)["state"] == "pending"
    assert learning.status(None, None, T0)["state"] == "idle"
    assert learning.parse_result("写好了\nRESULT: ARTICLE=03") == {"kind": "article", "no": "03"}
    assert learning.parse_result("RESULT: BLOCKED=没网\n") == {"kind": "blocked", "reason": "没网"}
    assert learning.parse_result("RESULT: ARTICLE=3") is None


def test_a_lost_handoff_can_be_handed_again(base):
    agent_watch.ensure_rules(rules_with_learning(base.parent))
    learning.handoff({"topic": "组织学"}, at=T0, trigger=lambda *a: "", read_runs=lambda: ({}, ""))
    later = T0 + timedelta(minutes=20)
    again = learning.handoff({"topic": "组织学"}, at=later, trigger=lambda *a: "", read_runs=lambda: ({}, ""))
    assert again["handed_at"] == later.isoformat(timespec="seconds")


def test_install_adds_the_learning_rule_once(base):
    path = rules_with_learning(base.parent)
    note = agent_watch.ensure_rules(path)
    assert "新增规则「写学习文章」" in note
    data = json.loads(path.read_text(encoding="utf-8"))
    rule = next(r for r in data["rules"] if r["name"] == "写学习文章")
    assert rule == {"name": "写学习文章", "when": {"source": "github", "types": ["learning.next"]}, "to": "claude-feedback",
                    "cwd": str(base), "skill": "dbs-learning", "parallel": 1}
    assert all("learning.next" in r["emits"]["types"] for r in data["rules"] if r["name"] in agent_watch.WATCH_RULES)
    assert agent_watch.learning_rule_missing(path) == ""
    assert agent_watch.ensure_rules(path) == ""


def test_watch_hands_learning_events_to_the_hub(base):
    agent_watch.write_json(agent_watch.folder() / "baseline.json", {"recorded_at": T0.isoformat(), "issues": []})
    ledger = {SUBJECT: {"handed_at": agent_watch.now().isoformat(timespec="seconds"), "title": "写《组织学》第 03 篇",
                           "detail": "课题：组织学", "url": ""}}
    agent_watch.write_json(agent_watch.learning_ledger_path(), ledger)
    receipt = agent_watch.watch(known={}, read=lambda repos: [], prs=[], upstream=[], feedback=[], pulls=lambda r: {})
    assert receipt.splitlines()[0].startswith("派出 1 条 学习文章：" + SUBJECT)
    events = json.loads(receipt.split("```agent-autoloop\n")[1].split("\n```")[0])["events"]
    assert [e["type"] for e in events] == ["learning.next"]
    again = agent_watch.watch(known={}, read=lambda repos: [], prs=[], upstream=[], feedback=[], pulls=lambda r: {})
    assert again.startswith(agent_watch.NOTHING)  # 同一事件再报一次时不再写「派出」，中台按 id 去重


def test_static_files_are_read_only_and_stay_inside_the_topic(base):
    assert learning.static_file("组织学", "01.html") == (b"<!doctype html><p>01</p>", "text/html")
    assert learning.static_file("组织学", "assets/a.png")[1] == "image/png"
    for name, rest in (("组织学", "../outside.md"), ("组织学", "01.md"), ("组织学", ".secret.html"), ("组织学", "assets/../01.html"),
                       ("..", "学习课题/组织学/01.html"), ("没登记", "01.html"), ("组织学", ""), ("组织学", "nope.html")):
        with pytest.raises(APIError) as error:
            learning.static_file(name, rest)
        assert error.value.status == 404
    (base / "组织学" / "link.html").symlink_to(base / "组织学" / "01.html")
    with pytest.raises(APIError):
        learning.static_file("组织学", "link.html")


def test_learning_routes(base, tmp_path, monkeypatch):
    with instance(tmp_path, monkeypatch) as request:
        data = request("/api/learning")
        assert data["default"] == "组织学" and [a["no"] for a in data["topics"][1]["articles"]] == ["01", "02"]
        saved = request("/api/learning/feedback", {"topic": "组织学", "no": "02", "text": "懂了"})
        assert saved["filled"] and learning.feedback_of((base / "组织学" / "02.md").read_text(encoding="utf-8")) == "懂了"
        with pytest.raises(HTTPError) as denied:  # 别的来源不能写反馈
            request("/api/learning/feedback", {"topic": "组织学", "no": "01", "text": "x"}, {"Origin": "https://evil.example"})
        assert denied.value.code == 403
        with pytest.raises(HTTPError) as denied:
            request("/api/learning/next", {"topic": "组织学"}, {"Origin": "https://evil.example"})
        assert denied.value.code == 403
        with pytest.raises(HTTPError) as missing:  # 中台没有规则：不交
            request("/api/learning/next", {"topic": "组织学"})
        assert missing.value.code == 400 and not agent_watch.learning_ledger_path().exists()
        # 静态页：直接 GET，带沙箱策略（instance 只给 JSON 请求，地址从它的闭包里取）
        port = next(c.cell_contents for c in request.__closure__
                    if isinstance(c.cell_contents, str) and c.cell_contents.startswith("http://127.0.0.1:"))
        opener = build_opener(ProxyHandler({}))
        with opener.open(Request(port + "/learning/%E7%BB%84%E7%BB%87%E5%AD%A6/01.html")) as response:
            assert response.read() == b"<!doctype html><p>01</p>"
            assert response.headers["Content-Type"] == "text/html; charset=utf-8"
            assert "sandbox" in response.headers["Content-Security-Policy"]
            assert "allow-same-origin" not in response.headers["Content-Security-Policy"]
        with pytest.raises(HTTPError) as error:
            opener.open(Request(port + "/learning/%E7%BB%84%E7%BB%87%E5%AD%A6/..%2F..%2Foutside.md"))
        assert error.value.code == 404


def test_a_second_root_is_listed_and_its_pages_reach_the_shared_guide(base, tmp_path, monkeypatch):
    """黄执中的课题在另一个根目录（NAS）：一起列出；页面按 <根目录名>/<课题>/ 打开，../../导读/ 的配图读得到，别的目录读不到。"""
    other = tmp_path / "黄执中" / "学习课题"
    talk = other / "沟通表达"
    talk.mkdir(parents=True)
    (other / "INDEX.md").write_text("| 课题 | 当前进度 | 最近更新 | 下一步 |\n|---|---|---|---|\n"
                                    "| 沟通表达 | 04 | 2026-11-13 | 读 04 |\n| 组织学 | 09 | 2026-12-01 | 同名不认 |\n", encoding="utf-8")
    (talk / "01.md").write_text(article("01", "底座"), encoding="utf-8")
    (talk / "01.html").write_text('<img src="../../导读/img/a.jpg">', encoding="utf-8")
    (tmp_path / "黄执中" / "导读" / "img").mkdir(parents=True)
    (tmp_path / "黄执中" / "导读" / "img" / "a.jpg").write_bytes(b"jpg")
    (tmp_path / "黄执中" / "逐字稿").mkdir()
    (tmp_path / "黄执中" / "逐字稿" / "x.html").write_text("x", encoding="utf-8")
    monkeypatch.setenv("LEARNING_ROOT", f"{base}:{other}")
    data = learning.view(at=T0)
    names = [t["name"] for t in data["topics"]]
    assert names == ["多巴胺", "组织学", "没目录", "沟通表达"] and not data["error"]
    talk_topic = data["topics"][-1]
    assert talk_topic["path"] == "学习课题/沟通表达" and [a["no"] for a in talk_topic["articles"]] == ["01"]
    assert learning.static_file("沟通表达", "学习课题/沟通表达/01.html")[1] == "text/html"
    assert learning.static_file("沟通表达", "01.html")[1] == "text/html"
    assert learning.static_file("沟通表达", "导读/img/a.jpg") == (b"jpg", "image/jpeg")
    for rest in ("逐字稿/x.html", "学习课题/INDEX.md", "../导读/img/a.jpg"):
        with pytest.raises(APIError):
            learning.static_file("沟通表达", rest)
    assert learning.save_feedback({"topic": "沟通表达", "no": "01", "text": "好"})["filled"]
    # 一个根目录不在（NAS 没挂上）：其余照列，并说哪个不在
    monkeypatch.setenv("LEARNING_ROOT", f"{base}:{tmp_path / 'nas'}")
    data = learning.view(at=T0)
    assert len(data["topics"]) == 3 and "nas" in data["error"]
