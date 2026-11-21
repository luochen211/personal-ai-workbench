"""运行记录点开一轮的详情：只读 worker 运行目录，解析回执、打码、防目录穿越。"""
import json
import os
import threading
from contextlib import contextmanager
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from personal import __main__ as app
from personal import issues, run_detail

RECEIPT = {"notice": [{"title": "草稿已写好", "text": "等你确认", "key": "n1"}],
           "ask": [{"title": "【问你】要新做查询吗", "text": "现在没有邮箱", "options": ["做", "不做"], "recommend": 0, "about": "内部", "key": "a1"}],
           "later": [{"key": "ci", "at": "2026-11-20T09:00:00+08:00", "about": "看 CI"}, {"key": "old", "cancel": True}],
           "resolve": [{"card": 243, "reason": "已在 #53 做完"}]}
MESSAGE = ("已给 o/r#200 写好回复草稿，没有改代码。\n\n- **缓存在哪：** 本库\n- token: ghp_abcdefghijklmnopqrstuvwxyz0123456789\n\n"
           "```agent-autoloop\n" + json.dumps(RECEIPT, ensure_ascii=False) + "\n```\nRESULT: REPLIED")
CONTEXT = {"events": [{"id": "x", "source": "github", "sourceName": "GitHub", "type": "issues.feedback", "subject": "o/r#200",
                       "summary": "投资助理接入", "detail": "很长", "link": "https://github.com/o/r/issues/200", "receivedAt": "2026-11-19T02:45:31Z"}],
           "answers": [], "timers": [], "waiting": [], "open": [], "decided": []}


def stream(*lines):
    return "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n"


def claude_log(result=None, said="正在看代码"):
    lines = [{"type": "system", "subtype": "hook_started"}, {"type": "system", "subtype": "init", "session_id": "s"},
             {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "…"}, {"type": "text", "text": said}]}},
             {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}]
    if result is not None:
        lines.append({"type": "result", "subtype": "success", "is_error": False, "result": result})
    return stream(*lines)


def make_run(base, name, files):
    folder = base / name
    folder.mkdir(parents=True)
    for file, text in files.items():
        (folder / file).write_text(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False), encoding="utf-8")
    return folder


def row(run_id, state="done", **extra):
    return {"id": run_id, "rule": "处理 issue 留言", "agent": "claude-feedback", "state": state, "summary": "中台记的结论", "error": "",
            "created_at": "2026-11-19T02:45:31Z", "claimed_at": "2026-11-19T02:45:32Z",
            "finished_at": "2026-11-19T02:47:01Z" if state in ("done", "nothing", "cancelled") else None, "subjects": ["o/r#200"], **extra}


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_AUTOLOOP_HOME", str(tmp_path / "hub"))
    folder = tmp_path / "hub" / "worker" / "runs"
    folder.mkdir(parents=True)
    return folder


def lookup(rows, cards=()):
    return lambda run_id: (rows.get(int(run_id)), list(cards))


def test_finished_stream_json_run_parses_the_receipt_and_matches_open_asks(base):
    make_run(base, "7-1", {"context.json": CONTEXT, "stdout-1.log": claude_log(MESSAGE), "stderr-1.log": "", "exit-1": "0",
                           "command-1.sh": "exec claude -p '按规则「处理 issue 留言」'"})
    cards = [{"id": 258, "kind": "ask", "state": "open", "title": "【问你】要新做查询吗", "createdAt": "2026-11-19T02:47:01Z"},
             {"id": 259, "kind": "notice", "state": "open", "title": "草稿已写好", "createdAt": "2026-11-19T02:47:01Z"}]
    d = run_detail.detail(7, lookup=lookup({7: row(7)}, cards))
    assert d["status"] == "完成" and not d["active"] and d["format"] == "stream-json" and d["delivered"]
    assert d["conclusion"] == "已给 o/r#200 写好回复草稿，没有改代码。"
    assert d["outcome"] == {"kind": "RESULT", "value": "REPLIED", "line": "RESULT: REPLIED"}
    assert "agent-autoloop" not in d["body"] and "RESULT" not in d["body"] and "缓存在哪" in d["body"]
    assert "ghp_abcdef" not in d["body"] and "****" in d["body"]
    r = d["receipt"]
    assert r["notice"] == [{"title": "草稿已写好", "text": "等你确认"}]
    assert r["ask"] == [{"title": "要新做查询吗", "text": "现在没有邮箱", "options": ["做", "不做"], "recommend": 0, "card": 258, "open": True, "tier": "ask"}]
    assert r["later"][0]["about"] == "看 CI" and r["later"][1]["cancel"] is True
    assert r["resolve"] == [{"card": 243, "reason": "已在 #53 做完"}]
    assert d["events"] == [{"source": "GitHub", "type": "issues.feedback", "subject": "o/r#200", "summary": "投资助理接入",
                            "link": "https://github.com/o/r/issues/200", "received_at": "2026-11-19T02:45:31Z", "count": 1}]
    assert d["exit_code"] == 0 and d["stderr"] == "" and d["subjects"] == ["o/r#200"]
    assert d["started_at"] == "2026-11-19T02:45:32Z" and d["finished_at"] == "2026-11-19T02:47:01Z"


def test_answered_ask_is_not_waiting(base):
    make_run(base, "7-1", {"context.json": CONTEXT, "stdout-1.log": claude_log(MESSAGE), "exit-1": "0"})
    d = run_detail.detail(7, lookup=lookup({7: row(7)}, []))
    assert d["receipt"]["ask"][0]["open"] is False and d["receipt"]["ask"][0]["card"] is None
    unknown = run_detail.detail(7, lookup=lambda _: (None, None))
    assert unknown["receipt"]["ask"][0]["open"] is None and unknown["rule"] == ""


def test_plain_text_receipt_from_a_script_and_the_last_attempt_and_turn_win(base):
    text = "派出 1 条 issue 留言：o/r#200\n\n```agent-autoloop\n{\"events\": [{\"id\": \"e\", \"type\": \"issues.feedback\", \"subject\": \"o/r#200\", \"summary\": \"s\"}]}\n```\n"
    make_run(base, "9-1", {"stdout-1.log": "第一次", "exit-1": "1"})
    make_run(base, "9-2", {"stdout-1.log": "原样回执\n```agent-autoloop\n{bad\n```", "exit-1": "0", "stdout-2.log": text, "exit-2": "0",
                           "command-1.sh": "按规则「立即看 agent issue」把这件活派给你"})
    d = run_detail.detail(9, lookup=lambda _: (None, None))
    assert d["format"] == "text" and d["attempt"] == 2 and d["status"] == "完成"
    assert d["conclusion"] == "派出 1 条 issue 留言：o/r#200"
    assert d["receipt"]["events"] == [{"type": "issues.feedback", "subject": "o/r#200", "summary": "s"}]
    assert d["rule"] == "立即看 agent issue"  # 中台读不到时从提示词里认规则
    assert d["events"] == [] and d["events_error"] == "没有 context.json"


def test_repeated_events_are_merged_with_a_count(base):
    tick = {"source": "schedule", "sourceName": "定时", "type": "schedule.tick", "receivedAt": "x"}
    make_run(base, "4-1", {"context.json": {"events": [tick, tick, tick, CONTEXT["events"][0], tick]}, "exit-1": "0"})
    events = run_detail.detail(4, lookup=lambda _: (None, None))["events"]
    assert [(e["type"], e["count"]) for e in events] == [("schedule.tick", 3), ("issues.feedback", 1), ("schedule.tick", 1)]


def test_broken_receipt_says_so(base):
    make_run(base, "3-1", {"stdout-1.log": "做完了\n```agent-autoloop\n{bad\n```\n", "exit-1": "0"})
    assert run_detail.detail(3, lookup=lambda _: (None, None))["receipt"] == {"error": "回执读不懂（不是合法 JSON）"}


def test_running_run_falls_back_to_the_last_assistant_text(base):
    make_run(base, "11-1", {"context.json": CONTEXT, "stdout-1.log": claude_log(None, "先读一遍讨论")})
    d = run_detail.detail(11, lookup=lookup({11: row(11, "claimed")}))
    assert d["status"] == "进行中" and d["active"] and not d["delivered"] and d["conclusion"] == "先读一遍讨论"
    assert d["exit_code"] is None and d["finished_at"] is None
    without_hub = run_detail.detail(11, lookup=lambda _: (None, None))
    assert without_hub["status"] == "进行中" and without_hub["active"]


def test_codex_json_output(base):
    log = stream({"type": "thread.started"}, {"type": "item.completed", "item": {"type": "agent_message", "text": "Codex 做完了\nRESULT: NO_CHANGE"}},
                 {"type": "turn.completed"})
    make_run(base, "12-1", {"stdout-1.log": log, "exit-1": "0"})
    d = run_detail.detail(12, lookup=lambda _: (None, None))
    assert d["format"] == "codex-json" and d["conclusion"] == "Codex 做完了" and d["outcome"]["value"] == "NO_CHANGE"


def test_failed_run_returns_exit_code_and_the_last_30_stderr_lines_masked(base):
    stderr = "\n".join(f"line {n}" for n in range(1, 51)) + "\nAuthorization: Bearer abcdefghijkl123456 sk-ant-api03-ABCDEFGHIJKLMNOP\n"
    make_run(base, "5-1", {"stdout-1.log": "", "stderr-1.log": stderr, "exit-1": "1"})
    d = run_detail.detail(5, lookup=lookup({5: row(5, "cancelled", error="退出码 1：找不到文件")}))
    assert d["status"] == "没跑成" and d["exit_code"] == 1 and not d["active"]
    lines = d["stderr"].split("\n")
    assert len(lines) == 30 and lines[0] == "line 22"
    assert "abcdefghijkl123456" not in d["stderr"] and "Bearer ****" in d["stderr"] and "sk-****" in d["stderr"]
    assert d["conclusion"] == "中台记的结论"  # 没有输出时用中台记的结论
    still_retrying = run_detail.detail(5, lookup=lookup({5: row(5, "queued", error="退出码 1")}))
    assert still_retrying["status"] == "失败" and still_retrying["active"]


def test_big_stdout_is_read_from_the_tail_only(base):
    noise = stream(*[{"type": "assistant", "message": {"content": [{"type": "text", "text": "x" * 1000}]}}] * 1500)
    make_run(base, "13-1", {"stdout-1.log": noise + claude_log("最后的结论"), "exit-1": "0"})
    d = run_detail.detail(13, lookup=lambda _: (None, None))
    assert d["truncated"] and d["conclusion"] == "最后的结论" and d["format"] == "stream-json"


@pytest.mark.parametrize("text, hidden", [
    ("ghp_" + "a" * 36, "a" * 36), ("github_pat_" + "B" * 40, "B" * 40), ("sk-proj-" + "c" * 30, "c" * 30),
    ("Bearer eyJhbGciOiJIUzI1NiJ9.x", "eyJhbGciOiJIUzI1NiJ9"), ("xoxb-1234567890-abcdef", "1234567890-abcdef"),
    ("AKIAABCDEFGHIJKLMNOP", "ABCDEFGHIJKLMNOP"), ("api_key=supersecretvalue", "supersecretvalue"),
    ("\"password\": \"hunter2hunter2\"", "hunter2hunter2"), ("提交 " + "0123456789abcdef" * 3, "0123456789abcdef" * 3),
])
def test_mask_hides_secret_looking_strings(text, hidden):
    out = run_detail.mask(text)
    assert hidden not in out and "****" in out


def test_mask_keeps_normal_text():
    for text in ("PR #34 开了，commit abc1234", "basic usage of the tokenizer", "session 60f10ab0-3e9f-4778-888b-6cb736839cec"):
        assert run_detail.mask(text) == text


def test_run_ids_and_folders_cannot_escape_the_runs_folder(base, tmp_path):
    for bad in ("../1", "0", "1/2", "-1", "1e3", "abc", "1234567890"):
        with pytest.raises(ValueError):
            run_detail.run_folder(bad, base)
    outside = tmp_path / "outside"
    make_run(outside, "21-1", {"stdout-1.log": "外面的", "exit-1": "0"})
    os.symlink(outside / "21-1", base / "21-1")
    assert run_detail.run_folder(21, base) == (None, 0)  # 符号链接的运行目录不认
    folder = make_run(base, "22-1", {"exit-1": "0"})
    os.symlink(outside / "21-1" / "stdout-1.log", folder / "stdout-1.log")
    d = run_detail.detail(22, lookup=lambda _: (None, None))
    assert d["conclusion"] == "" and d["format"] == ""  # 运行目录里指向外面的文件也不读
    make_run(base, "22-x", {"stdout-1.log": "名字不对"})
    make_run(base, "220-1", {"stdout-1.log": "别的轮次"})
    assert run_detail.run_folder(22, base)[0].name == "22-1"


def test_missing_run_is_a_lookup_error(base):
    with pytest.raises(LookupError):
        run_detail.detail(404, lookup=lambda _: (None, None))
    gone = run_detail.detail(404, lookup=lookup({404: row(404)}))
    assert gone["folder_missing"] and gone["conclusion"] == "中台记的结论" and gone["events_error"].startswith("运行目录已清理")


def test_find_row_uses_the_runs_already_read(monkeypatch):
    monkeypatch.setattr(issues, "all_runs", lambda: ({7: row(7)}, [{"id": 1}]))
    assert run_detail.find_row("7") == (row(7), [{"id": 1}])
    def broken():
        raise OSError("down")
    monkeypatch.setattr(issues, "all_runs", broken)
    assert run_detail.find_row(7) == (None, None)


@contextmanager
def web(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DATA", tmp_path / "instance")
    server = app.make_web(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    opener = build_opener(ProxyHandler({}))
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield lambda path: json.load(opener.open(Request(url + path)))
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_route_returns_the_detail_and_404s(base, tmp_path, monkeypatch):
    make_run(base, "7-1", {"context.json": CONTEXT, "stdout-1.log": claude_log(MESSAGE), "exit-1": "0"})
    monkeypatch.setattr(issues, "all_runs", lambda: ({7: row(7)}, []))
    with web(tmp_path, monkeypatch) as get:
        d = get("/api/autoloop/runs/7")
        assert d["id"] == 7 and d["outcome"]["value"] == "REPLIED" and "ghp_abc" not in json.dumps(d)
        for path, code in (("/api/autoloop/runs/8", 404), ("/api/autoloop/runs/..%2F7", 404), ("/api/autoloop/runs/0", 404)):
            with pytest.raises(HTTPError) as error:
                get(path)
            assert error.value.code == code
