"""浏览器断开连接（BrokenPipe、ConnectionReset）不打印堆栈；别的异常照常记录。"""
import logging

import pytest

from personal import __main__ as app


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DATA", tmp_path / "instance")
    web = app.make_web(0)
    yield web
    web.server_close()


def handler(server, sent):
    h = object.__new__(server.RequestHandlerClass)
    h.client_address = ("127.0.0.1", 50000)
    h.send = lambda status, body, kind="application/json": sent.append(status)
    return h


def raising(exc):
    def action():
        raise exc
    return action


@pytest.mark.parametrize("exc", [BrokenPipeError(32, "Broken pipe"), ConnectionResetError(54, "Connection reset by peer"),
                                 ConnectionAbortedError(53, "aborted")])
def test_disconnect_while_answering_is_silent(server, capsys, caplog, exc):
    sent = []
    with caplog.at_level(logging.DEBUG, logger="personal.web"):
        handler(server, sent).guarded(raising(exc))
    assert capsys.readouterr().err == "" and sent == []  # 不打印堆栈，也不再往断开的连接写 502
    assert len(caplog.records) == 1 and caplog.records[0].levelno == logging.DEBUG


def test_disconnect_while_sending_an_error_is_silent(server, capsys):
    h = handler(server, [])
    def send(status, body, kind="application/json"):
        raise BrokenPipeError(32, "Broken pipe")
    h.send = send
    h.guarded(raising(KeyError("x")))
    assert capsys.readouterr().err == ""


def test_other_errors_are_still_logged(server, capsys):
    sent = []
    handler(server, sent).guarded(raising(RuntimeError("任务库坏了")))
    err = capsys.readouterr().err
    assert "Traceback" in err and "RuntimeError: 任务库坏了" in err and sent == [502]


def test_server_handle_error_skips_disconnects_only(server, capsys):
    for exc in (BrokenPipeError(32, "Broken pipe"), ConnectionResetError(54, "reset")):
        try:
            raise exc
        except OSError:
            server.handle_error(None, ("127.0.0.1", 50000))
    assert capsys.readouterr().err == ""
    try:
        raise ValueError("坏请求")
    except ValueError:
        server.handle_error(None, ("127.0.0.1", 50000))
    err = capsys.readouterr().err
    assert "Traceback" in err and "ValueError: 坏请求" in err

