import shlex

import pytest

from personal import notify_open


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(notify_open, "record", lambda line: None)


def test_fragments_point_at_question_or_issue():
    assert notify_open.fragment(question=216) == "#/q/216"
    assert notify_open.fragment(issue="luochen211/app#16") == "#/i/luochen211/app/16"
    assert notify_open.fragment(issue="bad subject") == ""


def test_open_starts_workbench_before_opening(monkeypatch):
    opened, started = [], []
    url = notify_open.open_page(question=5, port=8799, launch=lambda port: started.append(port) or True, opener=opened.append)
    assert started == [8799] and opened == [url] == ["http://127.0.0.1:8799/#/q/5"]


def test_open_does_not_open_unreachable_page(monkeypatch):
    opened, dialogs = [], []
    monkeypatch.setattr(notify_open, "dialog", lambda *a, **k: dialogs.append(a))
    assert notify_open.open_page(question=5, launch=lambda port: False, opener=opened.append) is None
    assert opened == [] and "没启动起来" in dialogs[0][0]


def test_dialog_fallback_opens_only_when_clicked(monkeypatch):
    monkeypatch.setattr(notify_open.shutil, "which", lambda name: None)
    shown = []
    assert notify_open.notify("t", "x", question=1, ask=lambda t, x: True, show_page=lambda c, i: shown.append(c)) == "opened"
    assert notify_open.notify("t", "x", question=2, ask=lambda t, x: False, show_page=lambda c, i: shown.append(c)) == "dismissed"
    assert shown == [1]


def test_terminal_notifier_banner_executes_open(monkeypatch):
    monkeypatch.setattr(notify_open.shutil, "which", lambda name: "/opt/homebrew/bin/terminal-notifier")
    calls = []
    monkeypatch.setattr(notify_open.subprocess, "run", lambda args, **k: calls.append(args))
    assert notify_open.notify("标题", "正文", issue="o/r#3") == "banner"
    execute = calls[0][calls[0].index("-execute") + 1]
    assert "personal.notify_open" in execute and "--issue 'o/r#3'" in execute


def test_dash_title_stays_a_value(monkeypatch):
    seen = []
    monkeypatch.setattr(notify_open, "notify", lambda title, text, question, issue: seen.append((title, text, question)))
    assert notify_open.main(["notify", "--title=Agent 问你", "--text=-e 坏标题", "--question=7"]) == 0
    assert seen == [("Agent 问你", "-e 坏标题", 7)]


def test_execute_quotes_every_part_for_the_shell(monkeypatch, tmp_path):
    root = tmp_path / 'we"ird $(touch pwned) `id` dir'
    monkeypatch.setattr(notify_open, "ROOT", root)
    monkeypatch.setattr(notify_open.shutil, "which", lambda name: "/usr/local/bin/terminal-notifier")
    calls = []
    monkeypatch.setattr(notify_open.subprocess, "run", lambda args, **k: calls.append(args))
    notify_open.notify("标题", "正文", issue="o/r#3")
    execute = calls[0][calls[0].index("-execute") + 1]
    words = shlex.split(execute)
    assert words[:3] == ["cd", str(root), "&&"] and words[-2:] == ["--issue", "o/r#3"]


def test_issue_must_look_like_owner_repo_number(monkeypatch, capsys):
    monkeypatch.setattr(notify_open.shutil, "which", lambda name: "/usr/local/bin/terminal-notifier")
    sent = []
    monkeypatch.setattr(notify_open.subprocess, "run", lambda args, **k: sent.append(args))
    for bad in ("o/r#3; rm -rf ~", "o/r", "$(id)/r#1", "o/r#x"):
        with pytest.raises(SystemExit):
            notify_open.main(["notify", "--title=t", f"--issue={bad}"])
        with pytest.raises(ValueError):
            notify_open.notify("t", "x", issue=bad)
    assert sent == [] and "owner/repo#编号" in capsys.readouterr().err
