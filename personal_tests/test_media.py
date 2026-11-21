"""「自媒体」页：麦田只读代理（连不上、口令错、正常）、连接配置的先后、「在做的内容」表格解析和接口。"""
import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import pytest

from personal import media
from personal import __main__ as app
from personal_tests.test_workspace import instance

KEY = "s3cret-key"
OVERVIEW = {
    "name": "麦田",
    "episodes": [
        {"id": "ep-6", "episode": 6, "name": "第六期｜副标题", "short": "第六期", "schedule": "2026-11-17T10:00:00.000Z",
         "tasks": [{"platform": "bilibili", "name": "B站", "state": "published"}, {"platform": "douyin", "name": "抖音", "state": "published"}]},
        {"id": "ep-7", "episode": 7, "name": "第七期｜副标题", "short": "第七期", "schedule": "2026-11-19T10:00:00.000Z",
         "tasks": [{"platform": "bilibili", "name": "B站", "state": "scheduled"}, {"platform": "xhs", "name": "小红书", "state": "awaiting_user"},
                   {"platform": "channels", "name": "视频号", "state": "not_logged_in"}, {"platform": "douyin", "name": "抖音", "state": "filled"}]},
    ] + [{"id": f"ep-{n}", "episode": n, "name": f"第{n}期", "short": f"第{n}期", "schedule": "", "tasks": []} for n in range(1, 6)],
    "followers": [{"day": f"2026-11-{d:02d}", "platform": "xhs", "total": 1000 + d * 10, "gain": 10} for d in range(1, 19)]
    + [{"day": "2026-11-16", "platform": "bilibili", "total": 500, "gain": 0}, {"day": "2026-11-18", "platform": "bilibili", "total": 520, "gain": 20},
       {"day": "2026-11-18", "platform": "youtube", "total": 30, "gain": 0}, {"day": "2026-11-18", "platform": "douyin", "total": None, "gain": None}],
    "health": {"nas": True, "chrome": False, "login": {"channels": "out", "douyin": "ok"}, "metricsDay": "", "doctorDay": ""},
    "feed": [], "now": "2026-11-18T10:00:00.000Z",
}


@contextmanager
def fake_maitian(key=KEY, body=OVERVIEW):
    """模拟麦田网页：口令放在 cookie mt_key 里，对了才给 /api/overview；记下每个请求。"""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.command, self.path, self.headers.get("Cookie", "")))
            if f"mt_key={key}" not in (self.headers.get("Cookie") or ""):
                self.send_response(401); self.end_headers(); self.wfile.write(b"{}"); return
            data = json.dumps(body, ensure_ascii=False).encode() if self.path == "/api/overview" else b"{}"
            self.send_response(200 if self.path == "/api/overview" else 404)
            self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(data)

        def do_POST(self):
            seen.append((self.command, self.path, "")); self.send_response(500); self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown(); server.server_close(); thread.join()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("MAITIAN_ROOT", str(tmp_path / "maitian"))
    folder = tmp_path / "data"
    folder.mkdir()
    return folder


def local(data, url, key=KEY):
    (data / "maitian.json").write_text(json.dumps({"url": url, "key": key}), encoding="utf-8")


def test_settings_prefer_local_then_maitian_config_then_demo(data, tmp_path):
    conf = media.settings(data)
    assert (conf["url"], conf["key"], conf["source"], conf["demo"]) == ("http://127.0.0.1:8788", "demo", "demo", True)
    assert "node src/demo.mjs" in media.start_command(conf)
    (tmp_path / "maitian").mkdir()
    (tmp_path / "maitian/config.json").write_text(json.dumps({"web": {"port": 8787, "key": "real"}}), encoding="utf-8")
    conf = media.settings(data)
    assert (conf["url"], conf["key"], conf["source"], conf["demo"]) == ("http://127.0.0.1:8787", "real", "maitian", False)
    assert media.start_command(conf) == f"cd {tmp_path / 'maitian'} && node src/hub.mjs daemon"
    local(data, "http://127.0.0.1:9999/", "mine")
    conf = media.settings(data)
    assert (conf["url"], conf["key"], conf["source"]) == ("http://127.0.0.1:9999", "mine", "local")


def test_down_maitian_is_one_line_state_with_start_command(data):
    local(data, f"http://127.0.0.1:{free_port()}")
    payload = media.view(data)
    assert payload["state"] == "down" and payload["maitian"] is None and payload["error"] == ""
    assert payload["start"].endswith("node src/hub.mjs daemon")
    assert KEY not in json.dumps(payload)


def test_wrong_key_is_denied_without_leaking_the_key(data):
    with fake_maitian(key="other") as (url, seen):
        local(data, url)
        payload = media.view(data)
    assert payload["state"] == "denied" and "口令不对" in payload["error"]
    assert KEY not in json.dumps(payload, ensure_ascii=False)
    assert seen == [("GET", "/api/overview", f"mt_key={KEY}")]


def test_overview_is_summarized_read_only(data):
    with fake_maitian() as (url, seen):
        local(data, url)
        payload = media.view(data)
    assert [s[0] for s in seen] == ["GET"] and seen[0][1] == "/api/overview"  # 只读，不碰 /api/action
    assert payload["state"] == "ok" and payload["demo"] is False and payload["source"] == "local"
    assert KEY not in json.dumps(payload, ensure_ascii=False) and url not in json.dumps(payload)
    m = payload["maitian"]
    assert [e["episode"] for e in m["episodes"]] == [7, 6, 5, 4, 3, 2]  # 最近 6 期，新的在前
    seventh = m["episodes"][0]
    assert seventh["title"] == "第七期" and seventh["schedule"] == "2026-11-19T10:00:00.000Z"
    assert seventh["waiting"] == ["小红书", "抖音"] and m["waiting"] == 2
    assert [(g["label"], g["tone"], g["platforms"]) for g in seventh["groups"]] == [
        ("等你点发布", "wait", ["小红书", "抖音"]), ("未登录", "bad", ["视频号"]), ("已定时", "", ["B站"])]
    assert [(g["label"], g["tone"]) for g in m["episodes"][1]["groups"]] == [("已发布", "ok")]
    assert [(f["name"], f["total"], f["week"]) for f in m["followers"]] == [("B站", 520, 20), ("小红书", 1180, 70), ("YouTube", 30, None)]
    assert m["problems"] == ["专用 Chrome 没开", "视频号登录失效，在专用 Chrome 里重新登录"]


def test_problems_cover_nas_and_quiet_health():
    assert media.problems({"chrome": True, "nas": True, "login": {"x": "ok", "xhs": ""}}) == []
    assert media.problems({"chrome": True, "nas": False}) == ["成片目录读不到（NAS 没挂载）"]


def test_open_url_carries_key_only_in_the_redirect(data):
    local(data, "http://127.0.0.1:8787", "a b&c")
    assert media.open_url(data) == "http://127.0.0.1:8787/?key=a%20b%26c"
    local(data, "file:///etc/passwd")
    with pytest.raises(ValueError):
        media.open_url(data)


README = """# 自媒体与内容

## 发布渠道

| 不是 | 这张 |
|---|---|
| a | b |

## 在做的内容

| 内容 | 做到哪 | 发布 |
|---|---|---|
| 口播视频《甲》 | 文案 v2，`ref-luochen02` 重新配音 | 待确认 |
| 个人网站 a\\|b | 10-29 更新 | 已上线 |

其他视频：

| 内容 | 做到哪 | 发布 |
|---|---|---|
| 不算 | 第二张表 | — |

## 下一步
"""


def test_content_table_is_the_first_table_under_its_heading():
    rows = media.table(README)
    assert rows == [{"content": "口播视频《甲》", "progress": "文案 v2，`ref-luochen02` 重新配音", "publish": "待确认"},
                    {"content": "个人网站 a|b", "progress": "10-29 更新", "publish": "已上线"}]
    swapped = "## 在做的内容\n\n| 发布 | 内容 |\n|---|---|\n| 已上线 | 网站 |\n"
    assert media.table(swapped) == [{"content": "网站", "progress": "", "publish": "已上线"}]
    assert media.table("## 在做的内容\n\n没有表\n\n## 下一步\n| 内容 |\n|---|\n| x |\n") == []


def test_content_reads_assistant_readme(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_ROOT", str(tmp_path))
    assert "找不到" in media.content()["error"]
    path = tmp_path / "事项/自媒体与内容/README.md"
    path.parent.mkdir(parents=True)
    path.write_text(README, encoding="utf-8")
    payload = media.content()
    assert payload["error"] == "" and len(payload["rows"]) == 2 and payload["modified"]
    path.write_text("# 空\n", encoding="utf-8")
    assert "没有「在做的内容」表" in media.content()["error"]


def test_content_falls_back_to_the_old_board_name(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_ROOT", str(tmp_path))
    old = tmp_path / "事项/写书与内容/README.md"
    old.parent.mkdir(parents=True)
    old.write_text(README, encoding="utf-8")
    assert len(media.content()["rows"]) == 2
    new = tmp_path / "事项/自媒体与内容/README.md"
    new.parent.mkdir(parents=True)
    new.write_text("## 在做的内容\n\n| 内容 | 做到哪 | 发布 |\n|---|---|---|\n| 新 | 新 | 新 |\n", encoding="utf-8")
    assert media.content()["rows"] == [{"content": "新", "progress": "新", "publish": "新"}]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def test_routes_proxy_maitian_and_redirect_to_the_board(tmp_path, monkeypatch):
    monkeypatch.setenv("MAITIAN_ROOT", str(tmp_path / "none"))
    monkeypatch.setenv("ASSISTANT_ROOT", str(tmp_path / "assistant"))
    with fake_maitian() as (url, seen), instance(tmp_path, monkeypatch) as request:
        app.DATA.mkdir(parents=True, exist_ok=True)
        local(app.DATA, url)
        payload = request("/api/media/maitian")
        assert payload["state"] == "ok" and payload["maitian"]["waiting"] == 2 and KEY not in json.dumps(payload)
        assert "找不到" in request("/api/media/content")["error"]
        # 302 要看响应头：工作台地址取自 instance 的请求函数
        web = [c.cell_contents for c in request.__closure__ if isinstance(c.cell_contents, str) and c.cell_contents.startswith("http://")][0]
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with pytest.raises(HTTPError) as error:
            opener.open(Request(web + "/media/maitian"))
        assert error.value.code == 302 and error.value.headers["Location"] == f"{url}/?key={KEY}"
        assert all(method == "GET" for method, _, _ in seen)
