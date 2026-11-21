"""个人助理目录：每日简报和事项板块的只读接口。"""
import os
import time
from datetime import datetime, timezone, timedelta
from urllib.error import HTTPError
import pytest
from personal import assistant
from personal.errors import APIError
from personal_tests.test_workspace import instance

CST = timezone(timedelta(hours=8))
INDEX = """# 事项

| 板块 | 一句话 |
|---|---|
| [乙/](乙/README.md) | 第二个板块。 |
| [甲/](甲/README.md) | 第一个板块。 |
| [没目录/](没目录/README.md) | 表里有、目录没有。 |
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_ROOT", str(tmp_path))
    return tmp_path


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_no_brief_yet_is_an_explicit_empty_state(home):
    morning = datetime(2026, 11, 12, 7, 0, tzinfo=CST)
    payload = assistant.brief(now=morning)
    assert payload["latest"] is None and payload["brief"] is None and payload["dates"] == []
    assert payload["next_run"] == "2026-11-12T08:30+08:00"
    assert "08:30" in payload["notice"] and "现在生成" in payload["notice"]
    evening = assistant.brief(now=datetime(2026, 11, 12, 21, 0, tzinfo=CST))
    assert evening["next_run"] == "2026-11-13T08:30+08:00"
    assert not payload["error"]


def test_missing_assistant_root_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_ROOT", str(tmp_path / "none"))
    assert "助理目录不存在" in assistant.brief()["error"]
    assert "找不到事项目录" in assistant.boards()["error"]


NOON = datetime(2026, 11, 12, 12, 0, tzinfo=CST)


def test_latest_brief_and_history(home):
    daily = home / "资料" / "raw" / "daily"
    write(daily / "2026-11-12.md", "# 每日简报 11-12\n")
    write(daily / "2026-11-10.md", "# 每日简报 11-10\n")
    write(daily / "2026-11-12.prev.md", "重新生成前的旧版")
    write(daily / "notes.md", "不是简报")
    write(daily / "2026-13-40.md", "不存在的日期")
    (daily / "2026-11-11.md").mkdir()
    payload = assistant.brief(now=NOON)
    assert payload["dates"] == ["2026-11-12", "2026-11-10"]
    assert payload["latest"] == payload["default"] == "2026-11-12"
    assert payload["brief"]["date"] == "2026-11-12" and payload["brief"]["markdown"] == "# 每日简报 11-12\n"
    assert payload["brief"]["failures"] == []
    assert assistant.brief("2026-11-10", now=NOON)["brief"]["markdown"] == "# 每日简报 11-10\n"
    with pytest.raises(APIError) as missing:
        assistant.brief("2026-11-09", now=NOON)
    assert missing.value.status == 404


def test_future_preview_is_not_shown_by_default(home):
    daily = home / "资料" / "raw" / "daily"
    write(daily / "2026-11-13.md", "# 明天的预览\n")  # daily_brief.py --date 2026-11-13
    write(daily / "2026-11-10.md", "# 11-10\n")
    payload = assistant.brief(now=NOON)
    assert payload["latest"] == "2026-11-13"
    assert payload["default"] == "2026-11-10" and payload["brief"]["date"] == "2026-11-10"
    write(daily / "2026-11-12.md", "# 今天\n")
    assert assistant.brief(now=NOON)["brief"]["date"] == "2026-11-12"
    assert assistant.brief("2026-11-13", now=NOON)["brief"]["markdown"] == "# 明天的预览\n"
    (daily / "2026-11-12.md").unlink(); (daily / "2026-11-10.md").unlink()
    write(daily / "2026-11-15.md", "# 更远\n")
    assert assistant.brief(now=NOON)["default"] == "2026-11-13"  # 只有未来的：离今天最近的那份


def test_failed_sources_are_listed():
    text = "# 每日简报\n\n## 今天日程（11-12）\n\n该来源失败：lark-cli 退出码 3：missing scope\n\n## 未完成飞书任务\n\n- 无\n\n## GitHub 未读通知\n\n该来源失败：OSError: x\n"
    assert assistant.failures(text) == [{"source": "今天日程（11-12）", "reason": "lark-cli 退出码 3：missing scope"},
                                        {"source": "GitHub 未读通知", "reason": "OSError: x"}]


@pytest.mark.parametrize("value", ["../../etc/passwd", "2026-11-12.md", "2026-11-12/../x", "2026-02-30", "２０２６-11-12", "", "20261112"])
def test_date_parameter_is_strict(home, value):
    with pytest.raises(ValueError):
        assistant.brief(value)


def test_brief_routes_validate_dates_and_guard_the_run(tmp_path, monkeypatch, home):
    today = datetime.now().astimezone().date().isoformat()
    write(home / "资料" / "raw" / "daily" / f"{today}.md", "# 简报\n")
    calls = []
    monkeypatch.setattr(assistant, "run_brief", lambda: calls.append(1) or {"ok": True})
    with instance(tmp_path, monkeypatch) as request:
        assert request("/api/brief")["brief"]["markdown"] == "# 简报\n"
        assert request(f"/api/brief?date={today}")["latest"] == today
        for bad in ("..%2F..%2Fsecret", "2026-11-12%2F..%2F..", "x"):
            with pytest.raises(HTTPError) as error:
                request(f"/api/brief?date={bad}")
            assert error.value.code == 400
        with pytest.raises(HTTPError) as error:
            request("/api/brief?date=2000-01-01")
        assert error.value.code == 404
        with pytest.raises(HTTPError) as denied:
            request("/api/brief/run", {}, {"Origin": "https://evil.example"})
        assert denied.value.code == 403
        with pytest.raises(HTTPError) as denied:
            request("/api/brief/run", {}, {"X-Paihuo-Request": "0"})
        assert denied.value.code == 403
        assert calls == []
        assert request("/api/brief/run", {}) == {"ok": True}
    assert calls == [1]


SCRIPT = """import os, sys
from pathlib import Path
root = Path(os.environ["ASSISTANT_ROOT"])
out = root / "资料" / "raw" / "daily" / (os.environ["BRIEF_TEST_DATE"] + ".md")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("# 新的\\n\\n## GitHub 未读通知\\n\\n该来源失败：没网\\n", encoding="utf-8")
print("stdin=" + repr(sys.stdin.read()), "leader=" + str(os.getpgid(0) == os.getpid()), "cwd=" + os.getcwd())
"""


def test_run_brief_runs_the_script_detached_and_keeps_the_old_copy(home, monkeypatch):
    monkeypatch.setenv("BRIEF_TEST_DATE", "2026-11-12")
    write(home / "scripts" / "daily_brief.py", SCRIPT)
    old = write(home / "资料" / "raw" / "daily" / "2026-11-12.md", "# 08:30 那份\n")
    result = assistant.run_brief(now=NOON)
    assert "stdin=''" in result["output"] and "leader=True" in result["output"]
    assert f"cwd={home.resolve()}" in result["output"] or f"cwd={home}" in result["output"]
    assert result["ok"] and result["default"] == "2026-11-12" and result["brief"]["date"] == "2026-11-12"
    assert result["brief"]["failures"] == [{"source": "GitHub 未读通知", "reason": "没网"}]
    backup = old.with_name("2026-11-12.prev.md")
    assert result["previous"] == str(backup) and backup.read_text(encoding="utf-8") == "# 08:30 那份\n"
    assert result["dates"] == ["2026-11-12"]  # .prev.md 不进日期列表
    backup.unlink(); old.unlink()
    assert assistant.run_brief(now=NOON)["previous"] == ""  # 没有原件就不另存


def test_run_brief_failures(home):
    with pytest.raises(APIError) as missing:
        assistant.run_brief()
    assert missing.value.status == 404
    script = write(home / "scripts" / "daily_brief.py", "import sys\nprint('Traceback', file=sys.stderr)\nprint('OSError: 盘满了', file=sys.stderr)\nsys.exit(2)\n")
    with pytest.raises(APIError) as error:
        assistant.run_brief()
    assert error.value.status == 502 and "盘满了" in str(error.value)
    script.write_text("import sys\nprint('  ', file=sys.stderr)\nsys.exit(1)\n", encoding="utf-8")
    with pytest.raises(APIError, match="无输出"):
        assistant.run_brief()
    assistant._run_lock.acquire()
    try:
        with pytest.raises(APIError) as busy:
            assistant.run_brief()
        assert busy.value.status == 409
    finally:
        assistant._run_lock.release()


def test_run_brief_timeout_kills_the_whole_process_group(home, tmp_path_factory, monkeypatch):
    pid_file = tmp_path_factory.mktemp("pid") / "child"
    monkeypatch.setenv("PID_FILE", str(pid_file))
    write(home / "scripts" / "daily_brief.py",
          "import os, subprocess, sys, time\n"
          "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
          "open(os.environ['PID_FILE'], 'w').write(str(child.pid))\n"
          "time.sleep(60)\n")
    with pytest.raises(APIError) as timeout:
        assistant.run_brief(timeout=3)
    assert timeout.value.status == 504
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("超时后孙进程还活着")


def test_symlinks_out_of_the_assistant_folder_are_ignored(home, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    write(outside / "2026-11-12.md", "# 外面的简报\n")
    (home / "资料" / "raw").mkdir(parents=True)
    (home / "资料" / "raw" / "daily").symlink_to(outside)
    assert assistant.brief(now=NOON)["dates"] == []
    write(home / "事项" / "README.md", INDEX)
    write(outside / "板块" / "README.md", "# 外\n")
    (home / "事项" / "甲").mkdir()
    (home / "事项" / "甲" / "README.md").symlink_to(outside / "板块" / "README.md")
    assert assistant.boards()["boards"] == []
    (home / "事项" / "README.md").unlink()
    (home / "事项" / "README.md").symlink_to(outside / "板块" / "README.md")
    write(home / "事项" / "乙" / "README.md", "# 乙\n")
    assert [(b["name"], b["summary"]) for b in assistant.boards()["boards"]] == [("乙", "")]
    script = write(outside / "daily_brief.py", "")
    (home / "scripts").mkdir()
    (home / "scripts" / "daily_brief.py").symlink_to(script)
    with pytest.raises(APIError) as missing:
        assistant.run_brief()
    assert missing.value.status == 404


def test_boards_come_from_real_folders_in_index_order(home, tmp_path_factory):
    matters = home / "事项"
    write(matters / "README.md", INDEX)
    write(matters / "甲" / "README.md", "# 甲\n\n## 状态\n\n- 进行中\n\n```md\n## 下一步\n```\n\n## 下一步\n\n- 周五交稿\n- 方向：来源里没有记录，待补。\n\n## 记录\n")
    write(matters / "乙" / "README.md", "# 乙\n\n## 健康\n\n- 未接入\n\n## 下一步\n\n## 来源\n\nx\n")
    write(matters / "丙" / "README.md", "# 丙\n\n## 状态（2026-11）\n\n好\n\n## 下一步\n\n1. 第一步\n")
    write(matters / "空目录" / "notes.md", "没有 README 的目录不算板块")
    write(matters / ".hidden" / "README.md", "# 隐藏\n")
    write(matters / "文件.md", "不是目录")
    outside = write(tmp_path_factory.mktemp("outside") / "README.md", "# 外面\n")
    (matters / "外链").symlink_to(outside.parent)
    payload = assistant.boards()
    names = [b["name"] for b in payload["boards"]]
    assert names == ["乙", "甲", "丙"]
    second, first, third = payload["boards"]
    assert first["summary"] == "第一个板块。" and third["summary"] == ""
    assert first["status"] == "- 进行中\n\n```md\n## 下一步\n```"  # 代码块里的标题不切段
    assert first["next"] == "- 周五交稿\n- 方向：来源里没有记录，待补。"
    assert first["next_gaps"] == ["方向：来源里没有记录，待补。"] and not first["next_missing"]
    assert second["status"] is None and second["next_missing"] and second["next"] == ""
    assert third["status"] == "好" and third["next_gaps"] == []
    assert first["path"] == "事项/甲/README.md" and first["abs_path"] == str(matters / "甲" / "README.md")
    assert first["readme"].startswith("# 甲") and first["modified"]


def test_boards_route(tmp_path, monkeypatch, home):
    write(home / "事项" / "README.md", INDEX)
    write(home / "事项" / "甲" / "README.md", "# 甲\n\n## 状态\n\n好\n")
    with instance(tmp_path, monkeypatch) as request:
        payload = request("/api/boards")
    assert [b["name"] for b in payload["boards"]] == ["甲"]
    assert payload["boards"][0]["next_missing"]


def test_boards_hide_internal_file_names(home):
    matters = home / "事项"
    write(matters / "README.md", "| 板块 | 一句话 |\n|---|---|\n| 甲 | 在做（a.md） |\n")
    write(matters / "甲" / "README.md", "# 甲\n\n## 状态\n\n- 进行中。（a-b.md、c_d.md）\n- 微信本地库（见 规范/信息管道.md）\n"
          "- 括注（保留说明）\n\n```\n（keep.md）\n```\n\n## 下一步\n\n- 周五交稿。（x.md）\n\n## 来源记忆文件\n\na.md、b.md\n\n## 记录\n\n- 完\n")
    first = assistant.boards()["boards"][0]
    assert first["summary"] == "在做"
    assert first["status"] == "- 进行中。\n- 微信本地库\n- 括注（保留说明）\n\n```\n（keep.md）\n```"
    assert first["next"] == "- 周五交稿。"
    assert "来源记忆文件" not in first["readme"] and "a.md" not in first["readme"] and "\n\n## 记录" in first["readme"]


NEWS = """# 甲 · 动态

按日期记这件事发生了什么，新的写在最上面。

## 2026-11

- 11-18：截至当天没有付费客户。（a.md）
- 11-17：上线试用账号。
  - 下级条目不单列
- 11-13～14：四方协议出最终版。
- 11-09：协议寄出。
- 没有日期的条目不算

```md
- 11-30：代码块里的不算
```

## 2026-10

- 10-31：上月最后一条。
- 10-02：更早的一条。

## 接下来已约的

- 11-28～29：年会（还没发生，不算动态）
"""


def test_boards_list_the_latest_news_entries(home):
    matters = home / "事项"
    write(matters / "README.md", INDEX)
    write(matters / "甲" / "README.md", "# 甲\n\n## 状态\n\n好\n")
    write(matters / "甲" / "动态.md", NEWS)
    write(matters / "乙" / "README.md", "# 乙\n")
    second, first = assistant.boards()["boards"]
    assert first["news"] == [{"date": "11-18", "text": "截至当天没有付费客户。"}, {"date": "11-17", "text": "上线试用账号。"},
                             {"date": "11-13～14", "text": "四方协议出最终版。"}, {"date": "11-09", "text": "协议寄出。"},
                             {"date": "10-31", "text": "上月最后一条。"}]
    assert second["news"] == []  # 没有 动态.md 的板块


def test_news_are_sorted_by_date_and_ignore_files_outside(home, tmp_path_factory):
    folder = home / "事项" / "甲"
    write(folder / "README.md", "# 甲\n")
    write(folder / "动态.md", "## 2026-10\n\n- 10-02：旧\n\n## 2026-11\n\n- 11-01：新\n- 11-01：同一天第二条\n")
    assert assistant.news(folder, home, limit=2) == [{"date": "11-01", "text": "新"}, {"date": "11-01", "text": "同一天第二条"}]
    outside = write(tmp_path_factory.mktemp("outside") / "动态.md", "## 2026-11\n\n- 11-01：外面的\n")
    (folder / "动态.md").unlink()
    (folder / "动态.md").symlink_to(outside)
    assert assistant.news(folder, home) == []

OLD_GITHUB = """# 每日简报 2026-11-12 星期四

## 未完成飞书任务

- 无未完成任务

## GitHub 未读通知

共 98 条

- [codewhale-hq/Codewhale] CI workflow run failed for fix/zh-hans-display branch（CheckSuite，ci_activity）
- [codewhale-hq/Codewhale] fix(engine): keep timeline summaries（PullRequest，author）
- [openai/codex] MCP server processes leak (9+ GB RSS)（Issue，mention）
- 另有 3 条未列出
"""


def test_old_github_list_collapses_to_one_line():
    collapsed = assistant.collapse_github(OLD_GITHUB)
    assert collapsed.endswith("## GitHub 未读通知\n\nGitHub 未读 98 条，没有需要你处理的\n")
    assert "Codewhale" not in collapsed and "- 无未完成任务" in collapsed


def test_collapsed_github_keeps_only_items_that_need_action():
    text = OLD_GITHUB.replace("- 另有 3 条未列出\n", "- [o/a] Review (me)（PullRequest，review_requested）\n"
                              "- [o/b] Yours（Issue，assign）\n\n## 另一节\n\n- 保留\n")
    collapsed = assistant.collapse_github(text)
    assert collapsed.endswith("GitHub 未读 98 条，需要你处理 2 条：[o/a] Review (me)（请你评审）；[o/b] Yours（分配给你）"
                              "\n\n## 另一节\n\n- 保留\n")
    assert "Codewhale" not in collapsed and "openai/codex" not in collapsed
    many = "## GitHub 未读通知\n\n共 5 条\n\n" + "".join(f"- [o/r] r{i}（PullRequest，review_requested）\n" for i in range(5))
    assert assistant.collapse_github(many).endswith(
        "需要你处理 5 条：[o/r] r0（请你评审）；[o/r] r1（请你评审）；[o/r] r2（请你评审）；另有 2 条\n")


@pytest.mark.parametrize("body", ["GitHub 未读 98 条，没有需要你处理的", "- 无未读通知", "该来源失败：gh 退出码 1：HTTP 401"])
def test_new_or_failed_github_sections_are_untouched(body):
    text = f"# 每日简报\n\n## GitHub 未读通知\n\n{body}\n"
    assert assistant.collapse_github(text) == text


def test_brief_view_shows_the_collapsed_section(home):
    write(home / "资料" / "raw" / "daily" / "2026-11-12.md", OLD_GITHUB)
    markdown = assistant.brief(now=NOON)["brief"]["markdown"]
    assert "GitHub 未读 98 条，没有需要你处理的" in markdown and "CI workflow" not in markdown
