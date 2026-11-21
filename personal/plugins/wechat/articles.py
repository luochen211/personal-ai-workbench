#!/usr/bin/env python3
"""微信插件：查订阅的公众号推送过哪些文章（标题、摘要、链接、时间）。只读，不写任何文件。

数据来自读取工具解密好的明文库里的公众号消息库 message/biz_message_*.db。库里只有推送卡片，没有正文；
正文要按链接去网页上读，这里不做。例外是纯文字推送：整段正文就在卡片里，放在 JSON 的 text 字段，标题取它的第一行。
读的是上一次刷新明文库时的记录，输出第一行写着库里最新一条推送的时间。

平时用插件命令（在工作台仓库根目录运行，装了微信插件才能用；它找好各微信号的明文库，再用 wechat.json 里读取工具的
python 跑本文件，因为解压要 zstandard、解析要 expat）：
    python3 -m personal.plugins wechat articles                        # 所有号最近 7 天的文章，最新的在前
    python3 -m personal.plugins wechat articles --days 30 --query AI   # 最近 30 天标题或摘要里带 AI 的
    python3 -m personal.plugins wechat articles --from 某公众号 --limit 20
    python3 -m personal.plugins wechat articles --since 2026-11-01 --until 2026-11-15 --account wechat2 --format json
    python3 -m personal.plugins wechat articles accounts               # 列出公众号：最近 30 天推了几篇、最新一篇的时间
直接跑本文件要自己给明文库：python articles.py --dir 名字=<明文库目录> [--dir ...] [同上的参数]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
MAX_XML = 4 << 20  # 一条推送解压后最多这么多字节
MAX_DIGEST = 80  # 文字输出里摘要最多这么多字
MAX_TITLE = 64  # 纯文字推送的标题取第一行，最多这么多字
ARTICLE_HOST = "mp.weixin.qq.com"


def connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def biz_names(decrypted_dir: Path) -> dict[str, dict]:
    """公众号消息表名 → 公众号。表名是 Msg_ 加 username 的 md5。名字优先用备注，再用昵称。"""
    contacts: dict[str, dict] = {}
    contact_db = decrypted_dir / "contact" / "contact.db"
    if contact_db.exists():
        with connect(contact_db) as conn:
            for username, nick, remark in conn.execute("SELECT username, nick_name, remark FROM contact"):
                if username:
                    contacts[username] = {"username": username, "name": remark or nick or username}
    names: dict[str, dict] = {}
    for db_path in biz_dbs(decrypted_dir):
        with connect(db_path) as conn:
            for (username,) in conn.execute("SELECT user_name FROM Name2Id"):
                if username:
                    names[table_of(username)] = contacts.get(username) or {"username": username, "name": username}
    return names


def table_of(username: str) -> str:
    return "Msg_" + hashlib.md5(username.encode("utf-8")).hexdigest()


def biz_dbs(decrypted_dir: Path) -> list[Path]:
    return sorted((decrypted_dir / "message").glob("biz_message_*.db"))


def decode(content: Any, decompress: Callable[[bytes], bytes] | None) -> str:
    if isinstance(content, bytes):
        if content.startswith(ZSTD_MAGIC):
            if decompress is None:
                raise RuntimeError("推送内容是压缩过的，要装了 zstandard 的 python 才能读")
            content = decompress(content)
        return content.decode("utf-8", "ignore")
    return content or ""


def parse_push(xml: str) -> list[dict]:
    """一条推送里的文章（一次推送可以有好几篇）。只要 mp.weixin.qq.com 的文章链接，服务通知、小程序卡片不要。"""
    start = xml.find("<")
    if start < 0 or "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        return []
    try:
        root = ET.fromstring(xml[start:])
    except ET.ParseError:
        return []
    items = root.findall(".//mmreader/category/item")
    if not items:  # 少数推送只有 appmsg 本身
        appmsg = root.find(".//appmsg")
        items = [appmsg] if appmsg is not None else []
    articles = []
    for item in items:
        url = text(item, "url")
        title = text(item, "title")
        if not title or ARTICLE_HOST not in url:
            continue
        pub = text(item, "pub_time")
        digest = text(item, "digest") or text(item, "summary") or text(item, "des")
        body = ""
        if "\n" in title or len(title) > MAX_TITLE:  # 纯文字推送：整段正文就在 title 里
            body = title
            first = next(line.strip() for line in title.splitlines() if line.strip())
            title = first if len(first) <= MAX_TITLE else first[:MAX_TITLE] + "…"
            if body.startswith(digest.rstrip("…")):
                digest = ""
        articles.append({
            "title": title,
            "digest": digest,
            "text": body,
            "url": "https://" + url.split("://", 1)[1] if url.startswith("http://") else url,
            "author": text(item, "author"),
            "pub_time": int(pub) if pub.isdigit() else 0,
        })
    return articles


def text(node: ET.Element, tag: str) -> str:
    return (node.findtext(tag) or "").strip()


def collect(accounts: dict[str, Path], start: int, end: int, decompress: Callable[[bytes], bytes] | None) -> tuple[list[dict], int]:
    """所有号在 [start, end) 之间推送的文章，按时间从新到旧，同一篇只留一次。另返回库里最新一条推送的时间。"""
    found: dict[tuple[str, str], dict] = {}
    latest = 0
    for account, decrypted_dir in accounts.items():
        names = biz_names(decrypted_dir)
        for db_path in biz_dbs(decrypted_dir):
            with connect(db_path) as conn:
                tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")]
                for table in tables:
                    biz = names.get(table) or {"username": "", "name": table}
                    for (newest,) in conn.execute(f'SELECT MAX(create_time) FROM "{table}"'):
                        latest = max(latest, newest or 0)
                    rows = conn.execute(f'SELECT create_time, message_content FROM "{table}" WHERE create_time >= ? AND create_time < ?',
                                        (start, end))
                    for create_time, content in rows:
                        for art in parse_push(decode(content, decompress)):
                            when = art.pop("pub_time") or create_time
                            key = (biz["name"], art["title"])
                            if key in found and found[key]["timestamp"] >= when:
                                continue
                            found[key] = {"account": account, "biz": biz["name"], "biz_username": biz["username"],
                                          "timestamp": when, "time": fmt(when), **art}
    return sorted(found.values(), key=lambda a: a["timestamp"], reverse=True), latest


def fmt(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""


def matches(article: dict, query: str, biz: str) -> bool:
    if biz and biz.lower() not in f"{article['biz']} {article['biz_username']}".lower():
        return False
    return not query or query.lower() in f"{article['title']} {article['digest']} {article['text']}".lower()


def summarize_accounts(articles: Iterable[dict]) -> list[dict]:
    by_biz: dict[str, dict] = {}
    for art in articles:
        row = by_biz.setdefault(art["biz"], {"biz": art["biz"], "account": art["account"], "count": 0, "latest": 0, "latest_title": ""})
        row["count"] += 1
        if art["timestamp"] > row["latest"]:
            row["latest"], row["latest_title"] = art["timestamp"], art["title"]
    return sorted(by_biz.values(), key=lambda r: (r["count"], r["latest"]), reverse=True)


def window(args: argparse.Namespace, now: datetime) -> tuple[int, int]:
    end = datetime.strptime(args.until, "%Y-%m-%d") + timedelta(days=1) if args.until else now + timedelta(minutes=1)
    start = datetime.strptime(args.since, "%Y-%m-%d") if args.since else end - timedelta(days=args.days)
    return int(start.timestamp()), int(end.timestamp())


def zstd_decompressor() -> Callable[[bytes], bytes] | None:
    try:
        import zstandard
    except ImportError:
        return None
    reader = zstandard.ZstdDecompressor()
    return lambda data: reader.decompress(data, max_output_size=MAX_XML)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="查微信里订阅的公众号推送过哪些文章（只读）")
    p.add_argument("command", nargs="?", choices=["list", "accounts"], default="list",
                   help="list：列文章（默认）；accounts：列公众号")
    p.add_argument("--days", type=int, default=None, help="最近几天（list 默认 7，accounts 默认 30）")
    p.add_argument("--since", help="起始日期 YYYY-MM-DD（含）")
    p.add_argument("--until", help="截止日期 YYYY-MM-DD（含）")
    p.add_argument("--query", default="", help="标题、摘要（纯文字推送连正文）里的关键词")
    p.add_argument("--from", dest="biz", default="", help="公众号名字、备注里的关键词")
    p.add_argument("--account", help="只读这个微信号（--dir 里的名字）")
    p.add_argument("--dir", action="append", default=[], metavar="名字=目录", help="一个微信号的明文库（插件命令会自动给）")
    p.add_argument("--limit", type=int, default=50, help="最多列这么多条（默认 50）")
    p.add_argument("--format", choices=["text", "json"], default="text")
    args = p.parse_args(argv)
    if args.days is None:
        args.days = 30 if args.command == "accounts" else 7
    for value in (args.since, args.until):
        if value:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                p.error(f"日期要写成 YYYY-MM-DD：{value}")
    return args


def main(argv: list[str] | None = None, now: datetime | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    decompress = zstd_decompressor()
    accounts = {}
    for item in args.dir:
        name, _, folder = item.partition("=")
        if name and folder and Path(folder).expanduser().is_dir():
            accounts[name] = Path(folder).expanduser()
    if args.account:
        accounts = {k: v for k, v in accounts.items() if k == args.account}
    if not accounts:
        print("找不到微信明文库：先让读取工具解密一遍，或检查 --account", file=sys.stderr)
        return 1
    start, end = window(args, now or datetime.now())
    try:
        articles, latest = collect(accounts, start, end, decompress)
    except (RuntimeError, sqlite3.Error) as exc:
        print(f"读公众号消息库失败：{exc}", file=sys.stderr)
        return 1
    articles = [a for a in articles if matches(a, args.query, args.biz)]
    span = f"{fmt(start)[:10]} 至 {fmt(end - 1)[:10]}"
    if args.command == "accounts":
        rows = summarize_accounts(articles)[: args.limit]
        if args.format == "json":
            print(json.dumps({"range": span, "latest_push": fmt(latest), "accounts": rows}, ensure_ascii=False, indent=2))
            return 0
        print(f"公众号：{span} 推过文章的 {len(rows)} 个 · 库里最新一条推送 {fmt(latest)}")
        for r in rows:
            print(f"{r['count']:>4} 篇  {r['biz']}（{r['account']}）  最新 {fmt(r['latest'])}：{r['latest_title']}")
        return 0
    shown = articles[: args.limit]
    if args.format == "json":
        print(json.dumps({"range": span, "latest_push": fmt(latest), "total": len(articles), "articles": shown},
                         ensure_ascii=False, indent=2))
        return 0
    more = f"（只列最新 {len(shown)} 篇）" if len(articles) > len(shown) else ""
    print(f"公众号文章：{span} 共 {len(articles)} 篇{more} · 库里最新一条推送 {fmt(latest)}")
    for a in shown:
        print(f"\n{a['time'][5:]}  {a['biz']}｜{a['title']}")
        if a["digest"]:
            digest = a["digest"].replace("\n", " ")
            print(f"   {digest[:MAX_DIGEST]}{'…' if len(digest) > MAX_DIGEST else ''}")
        print(f"   {a['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
