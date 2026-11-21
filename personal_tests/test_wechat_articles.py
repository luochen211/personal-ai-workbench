"""微信插件的公众号文章（personal/plugins/wechat/articles.py）。临时目录里造一个假的明文库，不读真的微信。

Builds a tiny fake vault in a temp dir; nothing reads the real WeChat vault."""

from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest import mock

from personal.plugins.wechat import articles as wa

NOW = datetime(2026, 11, 21, 9, 0, 0)
DAY = 86400
T_NEW = int(datetime(2026, 11, 20, 8, 0, 0).timestamp())
T_OLD = int(datetime(2026, 10, 1, 8, 0, 0).timestamp())


def push(*items: tuple[str, str, str, int]) -> str:
    body = "".join(
        f"<item><title><![CDATA[{t}]]></title><url><![CDATA[{u}]]></url><digest><![CDATA[{d}]]></digest>"
        f"<pub_time>{p}</pub_time></item>" for t, u, d, p in items)
    return f"<msg><appmsg><title>x</title><mmreader><category>{body}</category></mmreader></appmsg></msg>"


def article_url(n: int) -> str:
    return f"http://mp.weixin.qq.com/s?__biz=AAA&mid={n}&idx=1"


class FakeVault(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.vault = self.root / "decrypted"
        (self.vault / "contact").mkdir(parents=True)
        (self.vault / "message").mkdir()
        with sqlite3.connect(self.vault / "contact/contact.db") as c:
            c.execute("CREATE TABLE contact (username TEXT, nick_name TEXT, remark TEXT)")
            c.executemany("INSERT INTO contact VALUES (?, ?, ?)",
                          [("gh_ai", "AI 日报", ""), ("gh_biz", "商业观察", "我的备注名")])
        with sqlite3.connect(self.vault / "message/biz_message_0.db") as c:
            c.execute("CREATE TABLE Name2Id (user_name TEXT, is_session INTEGER)")
            c.executemany("INSERT INTO Name2Id VALUES (?, 1)", [("gh_ai",), ("gh_biz",), ("gh_unknown",)])
            for user in ("gh_ai", "gh_biz", "gh_unknown"):
                c.execute(f'CREATE TABLE "{wa.table_of(user)}" (create_time INTEGER, message_content BLOB)')
            rows = {
                "gh_ai": [
                    (T_NEW, push(("模型发布了", article_url(1), "今天的新模型", T_NEW),
                                 ("第二篇", article_url(2), "副条", T_NEW))),
                    (T_NEW - 10, push(("模型发布了", article_url(1), "重复推送", T_NEW - 10))),  # same article again
                    (T_OLD, push(("一个月前", article_url(3), "", T_OLD))),
                    (T_NEW, "<msg><appmsg><title>服务通知</title><url>https://example.com/x</url></appmsg></msg>"),
                ],
                "gh_biz": [(T_NEW - DAY, push(("长文第一行\n\n后面是正文 AI 相关", article_url(4), "长文第一行", 0)).encode())],
                "gh_unknown": [(T_NEW, "not xml at all")],
            }
            for user, msgs in rows.items():
                c.executemany(f'INSERT INTO "{wa.table_of(user)}" VALUES (?, ?)', msgs)
    def run_main(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = wa.main(["--dir", f"wechat1={self.vault}", *argv], now=NOW)
        return code, out.getvalue()

    def test_lists_recent_articles_newest_first_without_duplicates(self) -> None:
        code, out = self.run_main("--format", "json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        titles = [a["title"] for a in data["articles"]]
        self.assertEqual(titles, ["模型发布了", "第二篇", "长文第一行"])
        first = data["articles"][0]
        self.assertEqual(first["biz"], "AI 日报")
        self.assertEqual(first["digest"], "今天的新模型")
        self.assertTrue(first["url"].startswith("https://mp.weixin.qq.com/s?__biz=AAA&mid=1"))
        self.assertEqual(data["latest_push"], wa.fmt(T_NEW))

    def test_text_post_keeps_body_and_uses_first_line_as_title(self) -> None:
        data = json.loads(self.run_main("--format", "json", "--from", "备注")[1])
        [post] = data["articles"]
        self.assertEqual(post["biz"], "我的备注名")
        self.assertEqual(post["title"], "长文第一行")
        self.assertIn("后面是正文", post["text"])
        self.assertEqual(post["digest"], "")
        self.assertEqual(post["time"], wa.fmt(T_NEW - DAY))  # pub_time 0 falls back to message time

    def test_filters_by_query_window_and_limit(self) -> None:
        q = json.loads(self.run_main("--format", "json", "--query", "ai")[1])
        self.assertEqual([a["title"] for a in q["articles"]], ["长文第一行"])
        old = json.loads(self.run_main("--format", "json", "--since", "2026-09-30", "--until", "2026-10-02")[1])
        self.assertEqual([a["title"] for a in old["articles"]], ["一个月前"])
        code, out = self.run_main("--limit", "1")
        self.assertIn("共 3 篇（只列最新 1 篇）", out)
        self.assertIn("AI 日报｜模型发布了", out)

    def test_accounts_summary(self) -> None:
        code, out = self.run_main("accounts", "--format", "json")
        rows = json.loads(out)["accounts"]
        self.assertEqual([(r["biz"], r["count"]) for r in rows], [("AI 日报", 2), ("我的备注名", 1)])

    def test_compressed_content_needs_zstandard(self) -> None:
        with sqlite3.connect(self.vault / "message/biz_message_0.db") as c:
            c.execute(f'INSERT INTO "{wa.table_of("gh_ai")}" VALUES (?, ?)', (T_NEW, wa.ZSTD_MAGIC + b"xx"))
        with mock.patch.object(wa, "zstd_decompressor", return_value=None), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn("zstandard", err.getvalue())
        fake = lambda data: push(("压缩的", article_url(9), "", T_NEW)).encode()  # noqa: E731
        with mock.patch.object(wa, "zstd_decompressor", return_value=fake):
            data = json.loads(self.run_main("--format", "json")[1])
        self.assertIn("压缩的", [a["title"] for a in data["articles"]])

    def test_missing_vault_and_unknown_account(self) -> None:
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(self.run_main("--account", "wechat9")[0], 1)

    def test_rejects_entity_declarations(self) -> None:
        self.assertEqual(wa.parse_push('<!DOCTYPE x [<!ENTITY a "b">]><msg/>'), [])


class Window(unittest.TestCase):
    def test_default_days(self) -> None:
        args = wa.parse_args([])
        self.assertEqual(args.days, 7)
        self.assertEqual(wa.parse_args(["accounts"]).days, 30)
        start, end = wa.window(args, NOW)
        self.assertEqual(end - start, 7 * DAY)

    def test_bad_date(self) -> None:
        with mock.patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            wa.parse_args(["--since", "11-01"])


if __name__ == "__main__":
    unittest.main()
