"""「回顾」标签：只读助理目录的 回顾/周、回顾/月，只在保存回答、运行回顾脚本、交给 Agent 时写。全部用临时目录和编的数据。"""
import json
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from personal import agent_watch, review
from personal.errors import APIError
from personal_tests.test_workspace import instance

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 20, 11, 0, tzinfo=CST)  # 周五
PH = review.PLACEHOLDER


def week(*, summary=PH, top3=None, questions=None, partial=True):
    top3 = top3 or "候选（有日期的截止和检查点，回顾 Agent 从中和各板块「下一步」里挑 3 件）：\n- 【11-28】某个会 · 事项/甲/README.md:27"
    questions = questions or f"1. {PH}\n   回答：\n2. {PH}\n   回答：\n3. {PH}\n   回答："
    stamp = "生成时间：2026-11-20 11:01" + (" · 数据截至 11-20 11:01，这一周还没过完" if partial else "")
    return f"""# 周回顾 2026-W47（11-16 周一 ～ 11-22 周日）

{stamp}

<!-- review:summary -->
{summary}
<!-- /review:summary -->

## 1. 时间去向（估算）

| 类别 | 本人（小时） | 占比 |  | Agent 代跑（小时 / 次） |
|---|---|---|---|---|
| 甲类 | 12.2 | 61% | ████████████ | 6.7 / 41 次 |
| 乙类 | 7.8 | 39% | ████████ | — |

- 本人合计约 20.0 小时，5 天有记录；本人提交 40 次，Agent 代跑提交 4 次。
- 每天（小时）：一 4.0 · 二 4.0

## 2. 产出

- 合并的 PR：18 个（甲类 10，乙类 8）：o/a 10；o/b 8
- 关掉的 issue（工作台登记的项目）：5 个（PR 关 3，手动关 2）：甲 5

## 6. 下周最重要的 3 件（建议）

<!-- review:top3 -->
{top3}
<!-- /review:top3 -->

## 7. 本周三问

回答直接写在本文件每个问题下面的「回答：」后；下周回顾会读。

<!-- review:questions -->
{questions}
<!-- /review:questions -->

## 上周的回答

- 上一期没有问题记录，或文件不在

## 数据缺口

- 没有
"""


ASKED = "1. 甲类占了六成，和今年的方向一致吗？\n   回答：一致，\n   但下周要收。\n2. 乙类 7.8 小时换来了什么？\n   回答：\n3. 开着的 PR 超了上限，先关哪几个？\n   回答："
FILLED = dict(summary="本人约 20 小时，甲类占 61%；乙类没有产出。",
              top3="1. 建议：把甲的报价发出去——卡着钱 · 事项/甲/README.md\n2. 建议：关掉多余的 PR——超上限\n3. 建议：填时薪表——11-30 检查点 · career-ops-data roadmap",
              questions=ASKED, partial=False)
MONTH = f"""# 月回顾 2026-10

生成时间：2026-11-01 09:00

<!-- review:summary -->
十月本人约 200 小时。
<!-- /review:summary -->

## 2. 各项目过三问

| 项目 | 类别 | 本人小时 | Agent 小时 | 本人提交 |
|---|---|---|---|---|
| 甲 | 甲类 | 30 | 2 | 100 |

<!-- review:projects -->
- 甲：现金流 能；资产 能；更重。建议 继续（有人付钱）
<!-- /review:projects -->

## 6. 本月时间去向（估算）

- 这段时间没有记录到活动

## 8. 本月三问

<!-- review:questions -->
1. {PH}
   回答：
<!-- /review:questions -->
"""
# 假的回顾脚本：--render 写同名 html；--week/--month 记下参数，写出 --date 那一期的 md 和 html
SCRIPT = '''import os, sys
from datetime import date
from pathlib import Path
root = Path(os.environ["ASSISTANT_ROOT"])
args = sys.argv[1:]
(root / "calls.txt").open("a").write(" ".join(args) + "\\n")
if args[0] == "--render":
    Path(args[1]).with_suffix(".html").write_text("<!doctype html><p>rendered</p>" + Path(args[1]).read_text(encoding="utf-8"), encoding="utf-8")
    sys.exit(0)
day = date.fromisoformat(args[2])
if args[0] == "--week":
    y, w, _ = day.isocalendar(); target = root / "回顾" / "周" / f"{y}-W{w:02d}.md"
else:
    target = root / "回顾" / "月" / f"{day:%Y-%m}.md"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text("# 回顾\\n\\n生成时间：2026-11-20 11:00\\n\\n- 本人合计约 3.5 小时，1 天有记录\\n", encoding="utf-8")
target.with_suffix(".html").write_text("<!doctype html>", encoding="utf-8")
print(target)
'''


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "assistant"
    monkeypatch.setenv("ASSISTANT_ROOT", str(root))
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path / "hub"))
    (root / "回顾" / "周").mkdir(parents=True)
    (root / "回顾" / "月").mkdir()
    (root / "scripts").mkdir()
    (root / "scripts" / "review.py").write_text(SCRIPT, encoding="utf-8")
    return root


def put(home, name, text, html=True):
    path = home / "回顾" / ("周" if "W" in name else "月") / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    if html:
        path.with_suffix(".html").write_text("<!doctype html><p>old</p>", encoding="utf-8")
    return path


def rules_with_review(home):
    path = home.parent / "hub" / "rules.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    emits = {"source": "github", "types": ["issues.opened"]}
    data = {"sources": {"github": {}}, "agents": {"issue-watch": {}, "claude-issue": {}}, "rules": [
        {"name": "看带 agent 标签的 issue", "when": {"cron": "5 * * * *"}, "to": "issue-watch", "emits": dict(emits)},
        {"name": "立即看 agent issue", "when": {"source": "local", "types": ["issues.check"]}, "to": "issue-watch", "emits": dict(emits)},
        {"name": "处理 issue", "when": {"source": "github", "types": ["issues.opened"]}, "to": "claude-issue", "cwd": "/Volumes/Luochen"},
        {"name": "周回顾", "when": {"cron": "0 21 * * 0"}, "to": "claude-issue", "cwd": "/tmp/assistant", "skill": "assistant-review", "prompt": "做本周的周回顾。"}]}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_empty_folder_says_when_the_next_reviews_come(home):
    data = review.view(now=NOW)
    assert data["weeks"] == [] and data["months"] == [] and data["error"] == ""
    assert data["current"] == {"week": "2026-W47", "month": "2026-11"}
    assert data["next_run"] == {"week": "2026-11-22T21:00+08:00", "month": "2026-12-01T09:00+08:00"}
    assert review.current(datetime(2026, 12, 1).date())["month"] == "2026-11"  # 1 日跑的是上个月
    assert review.next_runs(datetime(2026, 11, 22, 21, 0, tzinfo=CST))["week"] == "2026-11-29T21:00+08:00"


def test_missing_assistant_folder_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_ROOT", str(tmp_path / "nowhere"))
    assert "助理目录不存在" in review.view(now=NOW)["error"]


def test_a_fresh_period_hides_placeholders_and_gives_only_parsed_numbers(home):
    put(home, "2026-W47", week(), html=False)
    put(home, "2026-W46", week(**FILLED))
    (home / "回顾" / "周" / "笔记.md").write_text("不是一期", encoding="utf-8")
    rows = review.view(now=NOW)["weeks"]
    assert [r["label"] for r in rows] == ["2026-W47", "2026-W46"]  # 新的在前，别的文件不列
    fresh = rows[0]
    assert (fresh["start"], fresh["end"], fresh["partial"], fresh["html"]) == ("2026-11-16", "2026-11-22", True, False)
    assert fresh["summary"] == "" and fresh["top3"] == [] and fresh["projects"] == []
    assert fresh["written"] == {"summary": False, "top3": False, "questions": False, "projects": False}
    assert fresh["has"] == {"summary": True, "top3": True, "questions": True, "projects": False}
    assert [q["text"] for q in fresh["questions"]] == ["", "", ""]
    assert review.PLACEHOLDER not in json.dumps(fresh, ensure_ascii=False) and "候选" not in json.dumps(fresh, ensure_ascii=False)
    assert fresh["numbers"] == {"hours": 20.0, "days": 5, "merged_prs": 18, "closed_issues": 5, "categories": [
        {"name": "甲类", "hours": 12.2, "share": 61}, {"name": "乙类", "hours": 7.8, "share": 39}]}
    done = rows[1]
    assert done["partial"] is False and done["html"] is True and done["summary"].startswith("本人约 20 小时")
    assert done["top3"] == ["建议：把甲的报价发出去——卡着钱", "建议：关掉多余的 PR——超上限", "建议：填时薪表——11-30 检查点 · career-ops-data roadmap"]
    assert done["questions"][0] == {"no": 1, "text": "甲类占了六成，和今年的方向一致吗？", "answer": "一致，\n但下周要收。"}
    assert done["questions"][1]["answer"] == ""


def test_month_gives_the_per_project_lines_and_no_invented_numbers(home):
    put(home, "2026-10", MONTH)
    row = review.view(now=NOW)["months"][0]
    assert (row["start"], row["end"]) == ("2026-10-01", "2026-10-31")
    assert row["projects"] == ["甲：现金流 能；资产 能；更重。建议 继续（有人付钱）"]
    assert row["numbers"] == {} and row["has"]["top3"] is False


def test_saving_an_answer_only_touches_that_answer_and_rerenders(home):
    path = put(home, "2026-W46", week(**FILLED))
    before = path.read_text(encoding="utf-8")
    seen = review.view(now=NOW)["weeks"][0]["version"]
    saved = review.save_answer({"kind": "week", "label": "2026-W46", "no": 2, "text": "换来两个客户演示。\n\n没有收入。\n", "version": seen})
    after = path.read_text(encoding="utf-8")
    assert after == before.replace("2. 乙类 7.8 小时换来了什么？\n   回答：\n", "2. 乙类 7.8 小时换来了什么？\n   回答：换来两个客户演示。\n   没有收入。\n")
    assert saved["note"] == "" and saved["period"]["questions"][1]["answer"] == "换来两个客户演示。\n没有收入。"
    assert "换来两个客户演示" in path.with_suffix(".html").read_text(encoding="utf-8")  # HTML 用脚本重出
    # 改第一题（原来两行）：整段换掉；清空只剩「回答：」
    review.save_answer({"kind": "week", "label": "2026-W46", "no": 1, "text": "先不变"})
    assert "   回答：先不变\n2. 乙类" in path.read_text(encoding="utf-8")
    review.save_answer({"kind": "week", "label": "2026-W46", "no": 1, "text": "  "})
    assert "一致吗？\n   回答：\n2. 乙类" in path.read_text(encoding="utf-8")
    # 最后一题后面的块结束标记不动
    review.save_answer({"kind": "week", "label": "2026-W46", "no": 3, "text": "先关最老的两个"})
    assert "   回答：先关最老的两个\n<!-- /review:questions -->" in path.read_text(encoding="utf-8")
    calls = (home / "calls.txt").read_text(encoding="utf-8").splitlines()
    assert len(calls) == 4 and all(c.startswith("--render ") for c in calls)
    # 内容没变不写、不重出
    stamp = path.stat().st_mtime_ns
    review.save_answer({"kind": "week", "label": "2026-W46", "no": 3, "text": "先关最老的两个"})
    assert path.stat().st_mtime_ns == stamp and len((home / "calls.txt").read_text(encoding="utf-8").splitlines()) == 4


def test_saving_checks_the_version_and_rejects_what_would_break_the_file(home):
    path = put(home, "2026-W46", week(**FILLED), html=False)
    put(home, "2026-W47", week())
    with pytest.raises(APIError) as error:
        review.save_answer({"kind": "week", "label": "2026-W46", "no": 1, "text": "x", "version": "1"})
    assert error.value.status == 409 and "刷新" in str(error.value)
    for body in ({"no": 4, "text": "x"}, {"no": 0, "text": "x"}, {"no": "1", "text": "x"}, {"no": 1, "text": 3},
                 {"no": 1, "text": "a <!-- /review:questions --> b"}, {"no": 1, "text": review.PLACEHOLDER}, {"no": 1, "text": "x" * 5001},
                 {"no": 1, "text": "x", "label": "2026-W47"},  # 问题还是占位符：不能答
                 {"no": 1, "text": "x", "label": "../../x"}, {"no": 1, "text": "x", "kind": "day"}):
        with pytest.raises(ValueError):
            review.save_answer({"kind": "week", "label": "2026-W46", **body})
    with pytest.raises(APIError) as error:
        review.save_answer({"kind": "week", "label": "2026-W40", "no": 1, "text": "x"})
    assert error.value.status == 404
    assert path.read_text(encoding="utf-8") == week(**FILLED)
    review.save_answer({"kind": "week", "label": "2026-W46", "no": 2, "text": "没有 HTML 也能存"})
    assert not path.with_suffix(".html").exists() and not (home / "calls.txt").exists()


def test_run_calls_the_script_for_the_period_and_refuses_when_a_block_would_be_reset(home):
    result = review.run({"kind": "week"}, now=NOW)
    assert result["ok"] and result["label"] == "2026-W47" and result["existed"] is False
    assert [r["label"] for r in result["weeks"]] == ["2026-W47"] and result["weeks"][0]["numbers"] == {"hours": 3.5, "days": 1}
    result = review.run({"kind": "month"}, now=NOW)
    assert result["label"] == "2026-11" and [r["label"] for r in result["months"]] == ["2026-11"]
    assert review.run({"kind": "week", "label": "2026-W47"}, now=NOW)["existed"] is True
    assert (home / "calls.txt").read_text(encoding="utf-8").splitlines() == ["--week --date 2026-11-16", "--month --date 2026-11-01", "--week --date 2026-11-16"]
    # 三问只写了一部分（还带占位符）又有回答：脚本重跑会整块重置，拒绝
    put(home, "2026-W46", week(questions=f"1. 真问题？\n   回答：我的回答\n2. {PH}\n   回答：\n3. {PH}\n   回答："))
    with pytest.raises(APIError) as error:
        review.run({"kind": "week", "label": "2026-W46"}, now=NOW)
    assert error.value.status == 409 and "三问" in str(error.value)
    # 全是占位符的、全写好的都能重跑
    assert review.at_risk(week()) == [] and review.at_risk(week(**FILLED)) == []
    for body in ({"kind": "day"}, {"kind": "week", "label": "2026-W48"}, {"kind": "week", "label": "2026-W99"}, {"kind": "month", "label": "2026-12"}):
        with pytest.raises(ValueError):
            review.run(body, now=NOW)


def test_run_failures(home):
    (home / "scripts" / "review.py").write_text("import sys\nprint('review: 外接盘没有挂载；没有生成回顾。', file=sys.stderr)\nsys.exit(2)\n", encoding="utf-8")
    with pytest.raises(APIError) as error:
        review.run({"kind": "week"}, now=NOW)
    assert error.value.status == 502 and "外接盘没有挂载" in str(error.value)
    (home / "scripts" / "review.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    with pytest.raises(APIError) as error:
        review.run({"kind": "week"}, now=NOW, timeout=1)
    assert error.value.status == 504
    (home / "scripts" / "review.py").unlink()
    with pytest.raises(APIError) as error:
        review.run({"kind": "week"}, now=NOW)
    assert error.value.status == 404


def test_handoff_is_recorded_once_and_becomes_a_review_next_event(home):
    path = put(home, "2026-W47", week())
    before = path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="rules.json"):  # 中台还接不住：一条不记
        review.handoff({"kind": "week", "label": "2026-W47"}, at=NOW, trigger=lambda *a: "", read_runs=lambda: ({}, ""))
    rules = rules_with_review(home)
    assert "新增规则「写回顾」" in agent_watch.ensure_rules(rules)
    data = json.loads(rules.read_text(encoding="utf-8"))
    added = next(r for r in data["rules"] if r["name"] == "写回顾")
    assert added == {"name": "写回顾", "when": {"source": "github", "types": ["review.next"]}, "to": "claude-issue", "cwd": "/tmp/assistant",
                     "skill": "assistant-review", "parallel": 1}
    assert all("review.next" in r["emits"]["types"] for r in data["rules"] if "emits" in r)
    assert next(r for r in data["rules"] if r["name"] == "周回顾")["prompt"] == "做本周的周回顾。"  # 别的规则不动
    assert "写回顾" not in agent_watch.ensure_rules(rules)  # 第二次不再改
    calls = []
    result = review.handoff({"kind": "week", "label": "2026-W47"}, at=NOW, trigger=lambda *a: calls.append(a) or "ok", read_runs=lambda: ({}, ""))
    assert result["handoff"] == {"state": "pending", "no": "2026-W47", "handed_at": NOW.isoformat(timespec="seconds")} and len(calls) == 1
    with pytest.raises(APIError) as error:  # 还在派：不再交
        review.handoff({"kind": "week", "label": "2026-W47"}, at=NOW + timedelta(minutes=1), trigger=lambda *a: "", read_runs=lambda: ({}, ""))
    assert error.value.status == 409
    events = agent_watch.recap_events(at=NOW + timedelta(minutes=2))
    assert [(e["type"], e["subject"]) for e in events] == [("review.next", "review/2026-W47")] and events[0]["id"].startswith("recap:")
    assert "--week --date 2026-11-16" in events[0]["detail"] and "RESULT: REVIEW=2026-W47" in events[0]["detail"] and str(path) in events[0]["detail"]
    assert path.read_text(encoding="utf-8") == before  # 工作台不写那一期
    shown = review.view(now=NOW + timedelta(minutes=2), read_runs=lambda: ({}, ""))["weeks"][0]["handoff"]
    assert shown["state"] == "pending"
    run = {"id": 9, "state": "done", "created_at": (NOW + timedelta(minutes=3)).isoformat(), "finished_at": (NOW + timedelta(minutes=9)).isoformat(),
           "error": "", "result": review.parse_result("周回顾 2026-W47 已合并。\nRESULT: REVIEW=2026-W47\n\n```agent-autoloop\n{}\n```")}
    shown = review.view(now=NOW + timedelta(minutes=10), read_runs=lambda: ({"review/2026-W47": run}, ""))["weeks"][0]["handoff"]
    assert shown["state"] == "done" and shown["no"] == "2026-W47"
    assert review.parse_result("x\nRESULT: BLOCKED=外接盘没挂载") == {"kind": "blocked", "reason": "外接盘没挂载"} and review.parse_result("没写") is None
    # 都写好了的不用交；没有这一期的 404
    put(home, "2026-W46", week(**FILLED))
    with pytest.raises(ValueError, match="都已经写了"):
        review.handoff({"kind": "week", "label": "2026-W46"}, at=NOW, trigger=lambda *a: "", read_runs=lambda: ({}, ""))
    with pytest.raises(APIError):
        review.handoff({"kind": "month", "label": "2026-09"}, at=NOW, trigger=lambda *a: "", read_runs=lambda: ({}, ""))


def test_watch_hands_review_events_to_the_hub(home):
    put(home, "2026-W47", week())
    agent_watch.ensure_rules(rules_with_review(home))
    review.handoff({"kind": "week", "label": "2026-W47"}, at=agent_watch.now(), trigger=lambda *a: "", read_runs=lambda: ({}, ""))
    receipt = agent_watch.watch(known={}, read=lambda repos: [], prs=[], upstream=[], pulls=[], busy=set(), feedback=[], learning=[])
    assert receipt.startswith("派出 1 条 回顾：review/2026-W47") and '"type": "review.next"' in receipt


def test_rules_without_a_scheduled_review_are_left_alone(home):
    rules = rules_with_review(home)
    data = json.loads(rules.read_text(encoding="utf-8"))
    data["rules"] = [r for r in data["rules"] if r["name"] != "周回顾"]
    assert agent_watch.recap_changes(data) == ([], "")
    assert all("review.next" not in r.get("emits", {}).get("types", []) for r in data["rules"])


def test_static_page_is_read_only_and_only_serves_period_html(home, tmp_path):
    put(home, "2026-W46", week(**FILLED))
    data, kind = review.static_file("week", "2026-W46.html")
    assert data == b"<!doctype html><p>old</p>" and kind == "text/html"
    (tmp_path / "outside.html").write_text("x", encoding="utf-8")
    (home / "回顾" / "周" / "2026-W45.html").symlink_to(tmp_path / "outside.html")
    for kind, name in (("week", "2026-W46.md"), ("week", "../月/2026-10.html"), ("week", "2026-W45.html"), ("week", "2026-W40.html"),
                       ("month", "2026-W46.html"), ("day", "2026-W46.html"), ("week", ".html")):
        with pytest.raises(APIError) as error:
            review.static_file(kind, name)
        assert error.value.status == 404


def test_review_routes(home, tmp_path, monkeypatch):
    put(home, "2026-W46", week(**FILLED))
    with instance(tmp_path, monkeypatch) as request:
        data = request("/api/review")
        assert [r["label"] for r in data["weeks"]] == ["2026-W46"] and data["months"] == []
        saved = request("/api/review/answer", {"kind": "week", "label": "2026-W46", "no": 2, "text": "从页面写的", "version": data["weeks"][0]["version"]})
        assert saved["period"]["questions"][1]["answer"] == "从页面写的"
        with pytest.raises(HTTPError) as error:
            request("/api/review/answer", {"kind": "week", "label": "2026-W46", "no": 2, "text": "旧版本", "version": data["weeks"][0]["version"]})
        assert error.value.code == 409
        ran = request("/api/review/run", {"kind": "month", "label": "2026-10"})
        assert ran["label"] == "2026-10" and [r["label"] for r in ran["months"]] == ["2026-10"]
        with pytest.raises(HTTPError) as error:
            request("/api/review/next", {"kind": "week", "label": "2026-W46"})
        assert error.value.code == 400
        port = next(c.cell_contents for c in request.__closure__
                    if isinstance(c.cell_contents, str) and c.cell_contents.startswith("http://127.0.0.1:"))
        opener = build_opener(ProxyHandler({}))
        with opener.open(Request(port + "/review/week/2026-W46.html")) as response:
            assert response.headers["Content-Security-Policy"].startswith("sandbox") and "allow-same-origin" not in response.headers["Content-Security-Policy"]
            assert b"rendered" in response.read()
        for path in ("/review/week/2026-W46.md", "/review/week/..%2F..%2Fscripts%2Freview.py", "/review/day/2026-W46.html"):
            with pytest.raises(HTTPError) as error:
                opener.open(Request(port + path))
            assert error.value.code == 404
