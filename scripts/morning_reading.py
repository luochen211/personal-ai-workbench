#!/usr/bin/env python3
"""生财晨读：每天早上中台规则「生财晨读」运行这个脚本（用户 2026-11-21 要求做成每天早上的日报）。

它在工作台「对话」页开一个新对话，让只读的助理按本人最近在做的事推荐生财上适合读的内容
（和在对话页里手动问是同一个助理、同一个沙箱，见 personal/chat.py），等它答完，
用个人飞书机器人把回答发给本人（和每日简报同一个 lark-personal；中台的飞书通道只转「问你」卡片，
「仅告知」的通知不发飞书，所以这里自己发）。对话留在对话页里，可以接着追问。

最后一条输出是中台的回执：成功时一行结论；飞书没发出去时把内容作为「仅告知」通知交给中台（桌面能看到）；
工作台没开、助理没答完时第一行写「没跑成：原因」，中台会过一会儿重派。

  python3 scripts/morning_reading.py [--port 8790]
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

ASK = """生财晨读 {day}：按我最近在做的事，推荐几篇生财上适合我今天读的内容。

这份会推到飞书，手机上读：只推 2～4 篇最值得的，每篇写标题、作者、链接，再用两三句话说为什么现在适合读、对应哪个事项；不要分节标题以外的长铺垫，最后不用写说明段。挑不出好的就直说今天没有。"""
WAIT = 20 * 60
CARD_LIMIT = 3500
HOME = Path(os.environ.get("AGENT_AUTOLOOP_HOME", Path.home() / ".agent-autoloop"))
DRAFT = re.compile(r"```issue-draft\s*\n[\s\S]*?(\n```|$)")


def call(port, path, body=None):
    origin = f"http://127.0.0.1:{port}"
    request = urllib.request.Request(origin + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Origin": origin, "X-Paihuo-Request": "1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def message(text):
    text = DRAFT.sub("", text).strip()
    if len(text) > CARD_LIMIT:
        text = text[:CARD_LIMIT].rstrip() + "…"
    return text + "\n\n全文和追问：工作台「对话」页。"


def send_feishu(title, text, key, run=subprocess.run):
    """个人飞书机器人发给本人（收件人是 rules.json 里 channels.feishu.answerers 的第一个，那里不放密钥）。"""
    lark = HOME / "bin" / "lark-personal"
    owner = json.loads((HOME / "rules.json").read_text(encoding="utf-8"))["channels"]["feishu"]["answerers"][0]
    done = run([str(lark), "im", "+messages-send", "--user-id", owner, "--as", "bot", "--idempotency-key", key[:50],
                "--markdown", f"**{title}**\n\n{text}"], capture_output=True, text=True, timeout=60)
    try:
        out = json.loads(done.stdout or "{}")
    except ValueError:
        out = {}
    if done.returncode != 0 or out.get("ok") is False or out.get("error"):
        raise RuntimeError(f"lark-cli 退出码 {done.returncode}：{(json.dumps(out.get('error'), ensure_ascii=False) if out.get('error') else (done.stderr or done.stdout)).strip()[:300]}")


def receipt(day, text, send=send_feishu):
    title, text = f"生财晨读 {day:%m-%d}", message(text)
    key = f"scys-{day:%Y%m%d}-{hashlib.sha1(text.encode()).hexdigest()[:10]}"
    try:
        send(title, text, key)
        return f"{title} 已发个人飞书。"
    except (OSError, ValueError, KeyError, IndexError, RuntimeError, subprocess.TimeoutExpired) as exc:
        notice = {"title": title, "text": text, "key": f"scys-reading-{day.isoformat()}", "fyi": True}
        why = " ".join(str(exc).split())[:200]
        return f"{title} 飞书没发出去：{why}\n\n```agent-autoloop\n{json.dumps({'notice': [notice]}, ensure_ascii=False)}\n```"


def main(argv=None, *, request=call, sleep=time.sleep, today=date.today, send=send_feishu):
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default=os.environ.get("WORKBENCH_PORT", "8790"))
    port = parser.parse_args(argv).port
    day = today()
    try:
        cid = request(port, "/api/chat/send", {"text": ASK.format(day=f"{day:%m-%d}")})["id"]
        waited = 0
        while True:
            chat = request(port, f"/api/chats/{cid}")
            if not chat.get("running"):
                break
            if waited >= WAIT:
                request(port, "/api/chat/stop", {"id": cid})
                print(f"没跑成：助理 {WAIT // 60} 分钟没答完，已停止")
                return 1
            sleep(5)
            waited += 5
    except (OSError, ValueError, KeyError) as exc:
        print(f"没跑成：工作台没连上或返回不对（{exc}）")
        return 1
    reply = (chat.get("messages") or [{}])[-1]
    if reply.get("role") != "assistant" or reply.get("error") or not (reply.get("text") or "").strip():
        print(f"没跑成：助理没答完（{reply.get('error') or '没有回复'}）")
        return 1
    print(receipt(day, reply["text"], send))
    return 0


if __name__ == "__main__":
    sys.exit(main())
