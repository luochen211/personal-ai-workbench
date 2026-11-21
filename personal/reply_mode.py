"""「待你回」交给 Agent 的两种模式（用户 2026-11-18 要求：有的只需要回复，有的要 Agent 处理后再回复）：

- reply「帮我回复」：只回复。Agent 读全讨论和必要的代码、文档，只把回复写成草稿；不改代码、不建工作区、不推送、不开或关 PR。
- handle「处理并回复」：按留言改代码、推送或开 PR，再把回复写成草稿（PR #80 之前「帮我回复」做的完整流程）。

模式记进交给记录（pr-handoffs.json、issue-feedback-handoffs.json 的 mode），监看派活时写在事件 detail 第一行（见 agent_watch.ledger_events），
技能 handle-pr-feedback、handle-issue-feedback 照它走。没有 mode 的旧记录按「处理并回复」。

每行还有一个建议（hint）：对方最后几条在提问、确认口径、让你选 1A 1B 的建议「只需回复」；要求改代码、补功能、修 bug、
评审要求修改、带代码建议或修改意见的行内评论建议「需要处理」；判不准建议「只需回复」。只用已经读到的留言，不另发 GitHub 请求。
"""
from __future__ import annotations

import re

REPLY, HANDLE = "reply", "handle"
NAMES = {REPLY: "帮我回复", HANDLE: "处理并回复"}
HINTS = {REPLY: "只需回复", HANDLE: "需要处理"}
LINES = {
    REPLY: "模式：只回复（只读讨论和代码，把回复写成草稿；不改代码、不建工作区、不推送、不开或关 PR）",
    HANDLE: "模式：处理并回复（按留言改代码、推送或开 PR，再把回复写成草稿）",
}

# 带代码建议：GitHub 的 suggestion 块（bodyText 里写成 Suggested change）
SUGGESTION = re.compile(r"```\s*suggestion|suggested change", re.I)
# 让你选、确认口径：「1A 还是 1B」「选 A」「方案二」「用哪版」「口径」
CHOICE = re.compile(r"(?<![A-Za-z0-9])[1-9][0-9]?[A-Fa-f](?![A-Za-z0-9])|选\s*[A-Fa-f1-9一二三四]|方案\s*[A-Fa-f一二三四1-9]"
                    r"|还是.{0,20}[?？]|二选一|选哪|用哪(?:个|版|一版|种)|哪个方案|口径", re.I)
# 要求改代码、补功能、修 bug。「补充」「新增」「删除」这类词也常用来汇报（「补充检查结果」），前面有请、要、把这类要求才算
CHANGE = re.compile(
    r"改一下|改下|改成|改为|改掉|修一下|修下|修复|修掉|修好|补上|补一下|补个|加一下|删掉|删了吧|去掉|实现一下|支持一下|调整一下"
    r"|(?:请|麻烦|帮忙|需要|要|能不能|能否|可以|得|应该|建议|希望|把)[^。！？!?\n]{0,12}?(?:补充|加上|加个|新增|增加|删除|移除|换成|重构|实现|支持)"
    r"|报错|出错|崩溃|闪退|不生效|没生效|跑不起来|打不开|挂了|失败了|异常了"
    r"|\bbug\b|\brebase\b|\bnit\b|\bregression\b|\bbroken\b|\bcrash(?:es|ed)?\b"
    r"|\b(?:please|pls|can you|could you|would you|need to|needs to|should)\b.{0,40}?"
    r"\b(?:fix|change|update|add|remove|rename|refactor|implement|support|move|drop|revert|split|bump|use)\b"
    r"|\bfix(?:es|ed)?\b", re.I)
# 问为什么：「为什么要改成这样」是要个说法，不是要你改
WHY = re.compile(r"为什么|为啥|怎么会|\bwhy\b|\bhow come\b", re.I)
REQUEST = re.compile(r"请|麻烦|帮忙|需要|\bplease\b|\bpls\b|\bneed", re.I)
QUESTION = re.compile(r"[?？]|是不是|是否|有没有|能否|怎么|如何|吗|呢|哪(?:个|种|版|里|些)|确认一下|确认下|说明一下|解释一下"
                      r"|\b(?:why|how|what|which|whether|is it|does it|do you)\b", re.I)


def mode_of(value):
    """交给接口里的模式字段：空（旧页面、旧记录）是处理并回复；reply、handle 照写；别的报错。"""
    if value in (None, ""):
        return HANDLE
    if value in (REPLY, HANDLE):
        return value
    raise ValueError(f"模式写法不对：{str(value)[:40]}（只认 reply 或 handle）")


def line(mode):
    """派活提示里那一行（事件 detail 第一行）；没有模式是空串，技能按处理并回复走。"""
    return LINES.get(mode or "", "")


def judge(text, kind=""):
    """一条留言的建议：返回 (reply / handle, 依据)。kind：changes_requested（评审要求修改）、inline（行内意见）、其他。"""
    text = text or ""
    if kind == "changes_requested":
        return HANDLE, "评审要求修改"
    if SUGGESTION.search(text):
        return HANDLE, "带代码建议"
    found = CHOICE.search(text)
    if found:
        return REPLY, f"让你选或确认口径：「{found[0].strip()}」"
    found = CHANGE.search(text)
    if found and not (WHY.search(text) and not REQUEST.search(text)):
        return HANDLE, f"要求改动：「{found[0].strip()}」"
    if kind == "inline" and not QUESTION.search(text):
        return HANDLE, "行内修改意见"
    if QUESTION.search(text):
        return REPLY, "在提问"
    return REPLY, "判不准，默认只需回复"


def hint(items):
    """对方最后几条（[{who, text, kind}]，旧的在前）合起来的建议 {mode, label, why}：有一条要处理就是需要处理，否则只需回复。"""
    picked = None
    for item in items or []:
        mode, why = judge(item.get("text"), item.get("kind") or "")
        who = item.get("who") or ""
        if mode == HANDLE:
            picked = (HANDLE, f"{who} {why}".strip())
        elif picked is None:
            picked = (REPLY, f"{who} {why}".strip())
    mode, why = picked or (REPLY, "判不准，默认只需回复")
    return {"mode": mode, "label": HINTS[mode], "why": why}
