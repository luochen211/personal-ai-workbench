"""微信插件的抽取脚本（personal/plugins/wechat/extract.py）和共用部分（scripts/needs_common.py）。
聊天内容全是编的；读取工具、模型、gh 都是假的，草稿存进临时数据目录。"""
import json
import stat
import sys
from datetime import datetime

import pytest

from personal import issue_drafts
from personal.plugins.wechat import extract, needs, reader

common = extract.common
NOW = datetime(2026, 11, 20, 12, 0, 0)
T0 = int(datetime(2026, 11, 20, 10, 0, 0).timestamp())
SECRET = "登录页的验证码收不到短信了麻烦看一下"
PROJECTS = [{"id": "demo", "name": "演示工具", "github": "o/demo"}, {"id": "other", "name": "另一个", "github": "o/other"}]


def row(n, text=SECRET):
    ts = T0 + n * 60
    return {"ts": ts, "time": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"), "from": "甲", "text": text}


class FakeReader:
    def __init__(self, box):
        self.box = box

    def problem(self):
        return self.box["problem"]

    def history(self, chat, since, *, decrypted_dir="", timeout=120):
        self.box["read_from"].append((chat, decrypted_dir))
        if self.box["error"]:
            raise reader.ReaderError(self.box["error"])
        return list(self.box["rows"])


@pytest.fixture
def box(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "plugins.json").write_text('{"wechat": {"installed": true}}', encoding="utf-8")
    (data / "wechat.json").write_text(json.dumps({"vault_dir": str(tmp_path), "python": sys.executable}), encoding="utf-8")
    state = {"data": data, "rows": [row(1), row(2, "好的")], "problem": "", "error": "", "read_from": [], "refreshed": [],
             "items": [{"title": "登录页短信验证码收不到", "body": "## 背景\n客户方反馈收不到验证码。", "project_id": "demo", "message_ids": [1]}],
             "skipped": [], "asked": [], "cursor": tmp_path / "cursor.json", "accounts": tmp_path / "accounts.json"}
    monkeypatch.setenv(extract.STATE_ENV, str(state["cursor"]))
    monkeypatch.setenv(needs.ACCOUNTS_ENV, str(state["accounts"]))
    monkeypatch.setattr(extract, "workbench", lambda: (data, PROJECTS))
    monkeypatch.setattr(reader, "open_reader", lambda cfg: FakeReader(state))
    monkeypatch.setattr(extract, "open_issue_titles", lambda repo: ["导出按钮没反应"])
    monkeypatch.setattr(common, "ask", lambda payload, system: state["asked"].append(payload) or (state["items"], state["skipped"]))
    monkeypatch.setattr(needs, "refresh_all", lambda data: state["refreshed"].append(data) or [])
    needs.map_chat("测试群", "demo", PROJECTS, data=data)
    return state


def drafts(box):
    return sorted(issue_drafts.load(box["data"])["drafts"].values(), key=lambda d: d.get("status") == issue_drafts.SKIPPED)


def run_main(capsys, *argv):
    code = extract.main(list(argv))
    return code, capsys.readouterr().out


def scan_file(box):
    return json.loads((box["data"] / "issue-drafts-scan.json").read_text())


def test_saves_drafts_and_moves_the_cursor(box):
    assert extract.scan(now=NOW) == (2, 1, 1, 0, [])
    (draft,) = drafts(box)
    assert draft["source"]["kind"] == "wechat" and draft["source"]["chat"] == "测试群" and draft["repo"] == "o/demo"
    assert [x["text"] for x in draft["excerpt"]] == [SECRET]
    assert extract.load_cursors() == {"测试群": T0 + 120}
    assert stat.S_IMODE(box["cursor"].stat().st_mode) == 0o600
    assert box["refreshed"] == [box["data"]]  # 终端里跑：先刷新明文库
    assert extract.scan(now=NOW)[:3] == (0, 0, 0)  # 第二次只读更新的


def test_model_routing_and_dropped_items(box):
    box["items"][0]["project_id"] = "other"
    extract.scan(now=NOW, refresh=False)
    assert drafts(box)[0]["repo"] == "o/other" and box["refreshed"] == []
    box["cursor"].unlink()
    box["items"][0].update(project_id="nope", message_ids=[99], title="另一件事")
    assert extract.scan(now=NOW)[:3] == (2, 0, 0)  # 依据的消息对不上：丢掉，游标照推
    assert extract.load_cursors() == {"测试群": T0 + 120}


def test_new_repo_proposal_and_skipped_topics(box):
    box["items"][0].update(project_id="", new_repo={"name": "survey-form", "description": "  企业需求调研  问卷 "})
    box["skipped"] = [{"title": "新产品方向：个人助理", "body": "## 背景\n合作方在讨论一个新产品。", "project_id": "demo", "message_ids": [2],
                       "reason": "  讨论后决定\n先不做  "}]
    assert extract.scan(now=NOW)[1:4] == (1, 1, 1)
    draft, topic = drafts(box)
    assert draft["new_repo"] == {"name": "survey-form", "description": "企业需求调研 问卷"} and draft["repo"] == ""
    assert topic["status"] == issue_drafts.SKIPPED and topic["skip_reason"] == "讨论后决定 先不做"


def test_accounts_route_chats_to_their_own_vault(box):
    box["accounts"].write_text(json.dumps({"accounts": {"second": {"chats": ["测试群"], "decrypted_dir": "/x/w2", "home": "/x/home"}}}))
    extract.scan(now=NOW, refresh=False)
    assert box["read_from"] == [("测试群", "/x/w2")]
    box["accounts"].write_text(json.dumps({"accounts": {"second": {"chats": ["别的群"], "decrypted_dir": "/x/w2"}}}))
    box["cursor"].unlink()
    extract.scan(now=NOW, refresh=False)
    assert box["read_from"][-1] == ("测试群", "")


def test_main_reports_counts_without_chat_text(box, capsys):
    code, out = run_main(capsys, "--no-refresh")
    assert code == 0 and "新拟 1 条草稿" in out and SECRET not in out and "甲" not in out
    assert scan_file(box)["sources"]["wechat"] | {"finished_at": ""} == {
        "label": "微信", "status": "done", "finished_at": "", "found": 2, "saved": 1, "skipped": 0, "error": ""}
    assert box["refreshed"] == []
    code, out = run_main(capsys, "--no-refresh")
    assert code == 0 and out.startswith(common.IDLE_MARK)


def test_dry_run_writes_nothing(box, capsys):
    code, out = run_main(capsys, "--dry-run")
    assert code == 0 and "拟出 1 条" in out
    assert drafts(box) == [] and not box["cursor"].exists() and not (box["data"] / "issue-drafts-scan.json").exists()


def test_not_installed_no_chats_and_reader_errors(box, capsys):
    box["error"] = "微信本地库 history 退出码 3"
    code, out = run_main(capsys, "--no-refresh")
    assert code == 1 and out == "没跑成：微信本地库 history 退出码 3\n"
    assert scan_file(box)["sources"]["wechat"]["error"] == "微信本地库 history 退出码 3"
    needs.unmap_chat("测试群", data=box["data"])
    code, out = run_main(capsys, "--no-refresh")
    assert code == 0 and out.startswith(f"{common.IDLE_MARK}：还没有登记要读的微信群")  # 没登记不算失败
    (box["data"] / "plugins.json").write_text('{"wechat": {"installed": false}}', encoding="utf-8")
    code, out = run_main(capsys, "--no-refresh")
    assert code == 0 and out == f"{common.IDLE_MARK}：微信插件没装\n" and scan_file(box)["sources"]["wechat"]["status"] == "done"


def test_reader_problem_stops_the_round(box):
    box["problem"] = "找不到微信读取工具的目录：/nowhere"
    with pytest.raises(common.ScanError, match="找不到微信读取工具的目录"):
        extract.scan(now=NOW)


def test_new_messages_keeps_the_latest_and_cuts_long_text(monkeypatch):
    rows = [row(3), row(1), row(2)]
    assert [(r["id"], r["ts"]) for r in extract.new_messages(rows, T0 + 60)] == [(1, T0 + 120), (2, T0 + 180)]
    monkeypatch.setattr(extract, "MAX_MESSAGES", 2)
    found = extract.new_messages([row(n, "字" * 2000) for n in range(1, 6)], 0)
    assert [r["ts"] for r in found] == [T0 + 240, T0 + 300] and len(found[0]["text"]) == common.MAX_TEXT


def test_model_input_has_no_identifiers():
    rows = extract.new_messages([row(1)], 0)
    allowed = issue_drafts.eligible(PROJECTS)
    payload = extract.model_input({"key": "测试群", "chat": "测试群"}, allowed[0], allowed, rows, ["a"])
    assert set(payload["messages"][0]) == {"id", "time", "from", "text"} and payload["projects"] == [{"id": "other", "name": "另一个"}]
    assert "拿不准就归到 project" in extract.SYSTEM_PROMPT and "new_repo" in common.SCHEMA["properties"]["items"]["items"]["properties"]


def test_reader_history_keeps_readable_text_only(tmp_path):
    vault = tmp_path / "vault"
    (vault / "scripts").mkdir(parents=True)
    messages = [{"timestamp": T0, "time": "t", "sender": "甲", "type": "文本", "content": " 你好 "},
                {"timestamp": T0 + 1, "sender": "乙", "type": "图片", "content": "[图片]"},
                {"timestamp": "bad", "type": "文本", "content": "x"}, {"timestamp": T0 + 2, "type": "text", "content": "  "}]
    (vault / reader.Vault.CLI).write_text(f"import json; print(json.dumps({{'messages': {messages!r}}}))\n", encoding="utf-8")
    assert reader.Vault(vault, sys.executable).history("群", datetime(2026, 11, 1)) == [{"ts": T0, "time": "t", "from": "甲", "text": "你好"}]
    (vault / reader.Vault.CLI).write_text(f"import sys; print({SECRET!r}, file=sys.stderr); sys.exit(3)\n", encoding="utf-8")
    with pytest.raises(reader.ReaderError) as caught:
        reader.Vault(vault, sys.executable).history("群", datetime(2026, 11, 1))
    assert str(caught.value) == "微信本地库 history 退出码 3"  # 不带输出原文


def test_common_pieces(monkeypatch):
    assert common.saved_count("已存 3 条 issue 草稿，跳过 1 条") == 3 and common.saved_count("别的") == 0
    assert common.held_count("已存 3 条 issue 草稿、2 条没拟的话题，跳过 1 条") == 2
    assert common.parse_loose('好的：{"items": []} 以上') == {"items": []} and common.parse_loose("没有") is None
    with pytest.raises(common.ScanError) as caught:
        common.run(["/bin/sh", "-c", f"echo {SECRET} >&2; exit 3"], name="某命令")
    assert str(caught.value) == "某命令 退出码 3"
    monkeypatch.setenv(common.MODEL_ENV, "my-model --flag")
    assert common.model_command() == ["my-model", "--flag"]
    assert common.proposed_repo({"project_id": "", "new_repo": {"name": "Bad Name"}}) is None
