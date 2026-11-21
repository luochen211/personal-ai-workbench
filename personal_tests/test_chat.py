"""对话页：只读的启动参数、读 stream-json、接着聊、出错和停止、issue 草稿卡片、接口守卫。不真的起 Claude，内容是编的。"""
import json
import stat
from urllib.error import HTTPError

import pytest

from personal import chat, issue_drafts
from personal_tests.test_workspace import instance

PROJECTS = [{"id": "tool", "name": "拓客工具", "github": "me/tool"}]


def line(**event):
    return json.dumps(event, ensure_ascii=False) + "\n"


def text_start():
    return line(type="stream_event", event={"type": "content_block_start", "content_block": {"type": "text"}})


def delta(text):
    return line(type="stream_event", event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}})


def tool(name, **given):
    return line(type="assistant", message={"content": [{"type": "tool_use", "name": name, "input": given}]})


class Fake:
    """假的 Claude 进程：stdout 按给的行吐，记下收到的消息。"""
    def __init__(self, lines, code=0, err=""):
        self.lines, self.returncode, self.got, self.killed = lines, code, [], False
        fake = self

        class In:
            def write(self, text): fake.got.append(text)
            def close(self): pass

        class Err:
            def read(self): return err
        self.stdin, self.stderr = In(), Err()

    @property
    def stdout(self):
        return iter(self.lines)

    def wait(self):
        return self.returncode

    def kill(self):
        self.killed = True


@pytest.fixture
def env(tmp_path, monkeypatch):
    guard = tmp_path / "claude-guarded"
    guard.write_text("#!/bin/sh\n")
    monkeypatch.setenv(chat.PROGRAM_ENV, str(guard))
    monkeypatch.setattr(chat, "_cwd", lambda: str(tmp_path))
    chat._live.clear()
    calls = []

    def run(lines, code=0, err=""):
        proc = Fake(lines, code, err)

        def spawn(args, cwd, env):
            calls.append({"args": args, "cwd": cwd, "env": env, "proc": proc})
            return proc
        return spawn
    return tmp_path / "data", calls, run


def now(t):
    t.run()  # 测试里不开线程，跑完再往下


def test_read_only_flags():
    args = chat.args_for("")
    assert args[1] == "-p" and "--restricted" in args and "--strict-mcp-config" in args
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    tools = args[args.index("--tools") + 1].split(",")
    assert "Edit" not in tools and "Write" not in tools and "NotebookEdit" not in tools
    allowed = args[args.index("--allowedTools") + 1]
    assert f"Bash(python3 {chat.READER} *)" in allowed and allowed.count("Bash(") == 1
    assert "--resume" not in args
    assert json.loads(args[args.index("--mcp-config") + 1]) == chat.SCYS  # 只带生财，不带别的 MCP
    scys = {a.removeprefix(chat.SCYS_PREFIX) for a in allowed.split(",") if a.startswith(chat.SCYS_PREFIX)}
    assert scys == set(chat.SCYS_TOOLS) and "ToolSearch" in tools
    for writes in ("addCoinToTopic", "addFavorite", "topicLike", "tokenrank__setFollowStatus", "skillhubPublish", "skillhubUpdateReadme",
                   "skillhubCreateComment", "skillhubCreateGuide", "skillhubManageMySkill", "skillhubReportInstalled",
                   "co-creation__submitFeedback", "co-creation__supplementFeedback", "startShengcaiAiChat", "startLiuXiaoPaiChat",
                   "startAiYiRenChat", "getMyCustomerRecords"):
        assert writes not in scys
    assert args[args.index("--model") + 1] == chat.MODEL
    assert chat.args_for("abc")[-2:] == ["--resume", "abc"]


def test_reply_streams_into_the_chat_and_the_next_message_resumes(env):
    data, calls, run = env
    lines = [line(type="system", subtype="init", session_id="s1"), text_start(), delta("先查一下"), tool("Bash", command=f"python3 {chat.READER} api autoloop/runs"),
             tool("Read", file_path="/Volumes/Luochen/LCS/assistant/README.md"), tool("mcp__scys-mcp__searchTopic", keyword="AI 编程", page=1),
             text_start(), delta("结论："), delta("最近一轮是 #12。"),
             line(type="result", subtype="success", is_error=False, result="结论：最近一轮是 #12。", total_cost_usd=0.05, session_id="s1")]
    cid = chat.send("助理最近在做什么？", data=data, spawn=run(lines), start=now)["id"]
    assert calls[0]["proc"].got == ["助理最近在做什么？"] and calls[0]["env"]["WORKBENCH_PORT"]
    got = chat.detail(cid, data)
    assert not got["running"] and got["title"] == "助理最近在做什么？"
    user, reply = got["messages"]
    assert user["role"] == "user" and reply["text"] == "先查一下\n\n结论：最近一轮是 #12。"
    assert reply["tools"] == [{"name": "查询", "detail": "api autoloop/runs"}, {"name": "读文件", "detail": "/Volumes/Luochen/LCS/assistant/README.md"},
                             {"name": "查生财", "detail": "searchTopic AI 编程"}]
    assert reply["cost"] == 0.05 and "error" not in reply
    assert stat.S_IMODE(chat.path_of(data).stat().st_mode) == 0o600
    chat.send("那上一轮呢？", cid, data=data, spawn=run([line(type="result", subtype="success", is_error=False, result="#11。")]), start=now)
    assert calls[1]["args"][-2:] == ["--resume", "s1"]
    assert chat.detail(cid, data)["messages"][-1]["text"] == "#11。"  # 没有逐字片段时用 result
    assert [c["id"] for c in chat.listing(data)["chats"]] == [cid]


def test_failures_are_kept_on_the_reply(env):
    data, _, run = env
    cid = chat.send("在吗", data=data, spawn=run([], code=1, err="boom"), start=now)["id"]
    assert chat.detail(cid, data)["messages"][-1]["error"] == "Claude 没跑完（退出码 1）：boom"
    chat.send("再试", cid, data=data, spawn=run([line(type="result", subtype="error_during_execution", is_error=True, result="额度用完了")]), start=now)
    assert chat.detail(cid, data)["messages"][-1]["error"] == "额度用完了"


def test_send_refuses_empty_busy_and_missing_guard(env, monkeypatch, tmp_path):
    data, _, run = env
    with pytest.raises(ValueError):
        chat.send("  ", data=data, spawn=run([]), start=now)
    held = []
    cid = chat.send("第一条", data=data, spawn=run([]), start=held.append)["id"]  # 线程还没跑：算在回答中
    assert chat.detail(cid, data)["running"] and chat.listing(data)["chats"][0]["running"]
    with pytest.raises(ValueError, match="还在回答"):
        chat.send("第二条", cid, data=data, spawn=run([]), start=now)
    with pytest.raises(ValueError, match="还在回答"):
        chat.delete(cid, data)
    assert chat.stop(cid)["running"]
    held[0].run()
    assert chat.detail(cid, data)["messages"][-1]["error"] == "你停止了这次回答"
    monkeypatch.setenv(chat.PROGRAM_ENV, str(tmp_path / "nope"))
    with pytest.raises(ValueError, match="找不到"):
        chat.send("在吗", data=data, spawn=run([]), start=now)
    assert chat.delete(cid, data)["ok"] and chat.listing(data)["chats"] == []


def test_issue_draft_cards_go_to_the_drawer_only_when_saved(env):
    data, _, run = env
    card = {"repo": "me/tool", "title": "导出按钮支持按月份筛选", "body": "背景：导出现在只能全量。\n\n要做什么：加月份筛选。\n\n怎么算做完：选了月份只导出那个月。"}
    reply = "这个要改代码，我拟了一条：\n```issue-draft\n" + json.dumps(card, ensure_ascii=False) + "\n```\n```issue-draft\n{坏的}\n```"
    cid = chat.send("导出能按月份吗", data=data, spawn=run([text_start(), delta(reply), line(type="result", subtype="success", is_error=False, result=reply)]), start=now)["id"]
    assert chat.detail(cid, data)["messages"][-1]["drafts"] == [card]
    assert issue_drafts.load(data)["drafts"] == {}  # 没点之前不进抽屉
    saved = chat.save_draft(cid, card, PROJECTS, data=data)
    assert saved["saved"]
    draft = issue_drafts.load(data)["drafts"][saved["id"]]
    assert draft["status"] == "pending" and draft["repo"] == "me/tool" and draft["source"]["kind"] == "chat"
    assert chat.save_draft(cid, card, PROJECTS, data=data) == {"ok": True, "saved": False, "why": "已有待确认的草稿"}
    with pytest.raises(ValueError):
        chat.save_draft(cid, {**card, "repo": "someone/else"}, PROJECTS, data=data)


def test_routes_are_guarded(tmp_path, monkeypatch):
    guard = tmp_path / "claude-guarded"
    guard.write_text("#!/bin/sh\n")
    monkeypatch.setenv(chat.PROGRAM_ENV, str(guard))
    monkeypatch.setattr(chat, "_popen", lambda args, cwd, env: Fake([line(type="result", subtype="success", is_error=False, result="在。")]))
    monkeypatch.setattr(chat, "_cwd", lambda: str(tmp_path))
    with instance(tmp_path, monkeypatch) as request:
        for bad in ({"Origin": "https://evil.example"}, {"X-Paihuo-Request": "0"}):
            with pytest.raises(HTTPError) as denied:
                request("/api/chat/send", {"text": "在吗"}, headers=bad)
            assert denied.value.code == 403
        cid = request("/api/chat/send", {"text": "在吗"})["id"]
        for _ in range(200):
            got = request(f"/api/chats/{cid}")
            if not got["running"]:
                break
        assert got["messages"][-1]["text"] == "在。"
        assert request("/api/chats")["chats"][0]["id"] == cid
        with pytest.raises(HTTPError) as missing:
            request("/api/chats/c000000000000")
        assert missing.value.code == 400
