"""「学习」标签的「小说导读」分区：自动发现导读目录里的书、只读路由、阅读状态只写运行数据。全部用临时目录造的假书。"""
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from urllib.parse import quote

import pytest

from personal import novels
from personal.errors import APIError
from personal_tests.test_workspace import instance


@pytest.fixture
def shelf(tmp_path, monkeypatch):
    root = tmp_path / "教材"
    monkeypatch.setenv("NOVELS_ROOT", str(root))
    monkeypatch.setenv("LEARNING_ROOT", str(tmp_path / "没有学习目录"))
    for name in ("白鹿原", "古船", "百年孤独"):
        (root / name / "img").mkdir(parents=True)
        (root / name / "index.html").write_text(f"<!doctype html><title>{name}</title><a href=\"../index.html\">书架</a>", encoding="utf-8")
    (root / "白鹿原" / "img" / "hero.jpg").write_bytes(b"\xff\xd8jpg")
    (root / "白鹿原" / "style.css").write_text("body{}", encoding="utf-8")
    (root / "白鹿原" / "app.js").write_text("1", encoding="utf-8")
    (root / "白鹿原" / "notes.md").write_text("草稿", encoding="utf-8")
    (root / "白鹿原" / "data.json").write_text("{}", encoding="utf-8")
    (root / "白鹿原" / ".hidden.html").write_text("x", encoding="utf-8")
    (root / "白鹿原 (陈忠实).epub").write_bytes(b"PK")
    (root / "index.html").write_text("<!doctype html>书架", encoding="utf-8")
    (root / "我和多伦多的100件小事").mkdir()  # 没有 index.html：不算导读
    (root / "我和多伦多的100件小事" / "a.md").write_text("x", encoding="utf-8")
    (root / "_img").mkdir()
    (root / "_img" / "gallery.html").write_text("x", encoding="utf-8")
    (root / ".cache").mkdir()
    (root / ".cache" / "index.html").write_text("x", encoding="utf-8")
    (root / "选择对照" / "notes").mkdir(parents=True)
    (root / "选择对照" / "人物选择对照.html").write_text("<!doctype html>对照", encoding="utf-8")
    (tmp_path / "outside.html").write_text("外面", encoding="utf-8")
    (root / "链接书").symlink_to(root / "古船")
    return root


def files_of(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_books_are_found_automatically(shelf, tmp_path):
    data = novels.view(tmp_path / "data")
    assert [b["name"] for b in data["books"]] == ["古船", "白鹿原", "百年孤独"]  # 只认带 index.html 的真目录
    assert {b["status"] for b in data["books"]} == {"unread"}
    assert data["compare"] == "选择对照/人物选择对照.html" and data["error"] == ""


def test_missing_root_says_where_to_point_it(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVELS_ROOT", str(tmp_path / "nope"))
    data = novels.view(tmp_path / "data")
    assert data["books"] == [] and data["compare"] == "" and "NOVELS_ROOT" in data["error"]


def test_reading_status_lives_in_runtime_data_and_reading_books_come_first(shelf, tmp_path):
    data_dir = tmp_path / "data"
    before = files_of(shelf)
    assert novels.set_status(data_dir, {"book": "百年孤独", "status": "reading"}) == {"book": "百年孤独", "status": "reading"}
    novels.set_status(data_dir, {"book": "古船", "status": "done"})
    books = novels.view(data_dir)["books"]
    assert [(b["name"], b["status"]) for b in books] == [("百年孤独", "reading"), ("古船", "done"), ("白鹿原", "unread")]
    assert set(novels.load_status(data_dir)) == {"百年孤独", "古船"}
    novels.set_status(data_dir, {"book": "百年孤独", "status": "unread"})  # 回到默认就不留记录
    assert set(novels.load_status(data_dir)) == {"古船"}
    assert files_of(shelf) == before  # 导读目录一个文件都没多
    assert sorted(p.name for p in data_dir.iterdir()) == ["novel-status.json"]


@pytest.mark.parametrize("body, status", [
    ({"book": "白鹿原", "status": "finished"}, 400),
    ({"book": "白鹿原"}, 400),
    ({"book": "没有这本", "status": "done"}, 404),
    ({"book": "../白鹿原", "status": "done"}, 404),
    ({"book": "我和多伦多的100件小事", "status": "done"}, 404),
    ({"book": "链接书", "status": "done"}, 404),
    ({"book": 3, "status": "done"}, 404),
])
def test_status_only_for_books_on_the_shelf(shelf, tmp_path, body, status):
    with pytest.raises((APIError, ValueError)) as error:
        novels.set_status(tmp_path / "data", body)
    assert getattr(error.value, "status", 400) == status
    assert not (tmp_path / "data" / "novel-status.json").exists()


def test_static_route_serves_only_guide_types_inside_the_root(shelf):
    assert novels.static_file("白鹿原/index.html")[1] == "text/html"
    assert novels.static_file("白鹿原/img/hero.jpg") == (b"\xff\xd8jpg", "image/jpeg")
    assert novels.static_file("白鹿原/style.css")[1] == "text/css"
    assert novels.static_file("白鹿原/app.js")[1] == "text/javascript"
    assert novels.static_file("index.html")[0] == "<!doctype html>书架".encode()  # 导读里的 ../index.html
    assert novels.static_file("选择对照/人物选择对照.html")[1] == "text/html"
    for rest in ("白鹿原 (陈忠实).epub", "白鹿原/notes.md", "白鹿原/data.json", "白鹿原/.hidden.html", ".cache/index.html",
                 "../outside.html", "白鹿原/../../outside.html", "白鹿原/./index.html", "白鹿原//index.html", "", "白鹿原\\index.html",
                 "链接书/index.html", "白鹿原/nope.html", "白鹿原/img"):
        with pytest.raises(APIError) as error:
            novels.static_file(rest)
        assert error.value.status == 404, rest


def test_novel_routes(shelf, tmp_path, monkeypatch):
    with instance(tmp_path, monkeypatch) as request:
        data = request("/api/learning")
        assert [b["name"] for b in data["novels"]["books"]] == ["古船", "白鹿原", "百年孤独"]
        assert data["error"]  # 学习目录不在也照样列出小说导读
        assert request("/api/learning/novel", {"book": "白鹿原", "status": "reading"})["status"] == "reading"
        assert request("/api/learning")["novels"]["books"][0] == {"name": "白鹿原", "status": "reading"}
        assert (tmp_path / "instance" / "novel-status.json").exists()
        with pytest.raises(HTTPError) as denied:  # 别的来源不能改状态
            request("/api/learning/novel", {"book": "古船", "status": "done"}, {"Origin": "https://evil.example"})
        assert denied.value.code == 403
        port = next(c.cell_contents for c in request.__closure__
                    if isinstance(c.cell_contents, str) and c.cell_contents.startswith("http://127.0.0.1:"))
        opener = build_opener(ProxyHandler({}))
        with opener.open(Request(port + "/novels/" + quote("白鹿原") + "/img/hero.jpg")) as response:
            assert response.read() == b"\xff\xd8jpg" and response.headers["Content-Type"] == "image/jpeg"
            assert "sandbox" in response.headers["Content-Security-Policy"]
            assert "allow-same-origin" not in response.headers["Content-Security-Policy"]
        for bad in ("/novels/" + quote("白鹿原 (陈忠实).epub"), "/novels/" + quote("白鹿原") + "/..%2F..%2Foutside.html",
                    "/novels/%2E%2E/outside.html"):
            with pytest.raises(HTTPError) as error:
                opener.open(Request(port + bad))
            assert error.value.code == 404, bad
