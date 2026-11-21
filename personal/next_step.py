"""收件箱里的 issue 按「这条 issue 的下一步该谁做」归类（用户 2026-11-18 定的思路二）。规则按顺序，先中先停：

1. Agent 流程中（flow）：「处理 issue」「审查 PR」有轮次、交给记录或 agent 标签；「处理 issue 留言」还在派、在跑或等你看。
2. PR 评审中（pr）：有引用这条 issue 的 open PR。
3. 待关闭（close）：写了关闭这条的 PR 已合并；或你最后一条留言写了「请验收并关闭」一类。
4. 待你回（reply）：对方（真人）最后说话、这条与你有关；或别人开的 issue 只有正文。上游仓库别人最后说话、你没说过话也没被 @ 的归「待你看」。

0. 先于上面几条（用户 2026-11-20 定，news / noticed / answer）：别人（真人，不是你、不是机器人；Agent 用你的账号发的算你）
   在你最后一次动作之后留言或 @了你，或未读的 GitHub 通知比读到的数据新（personal/fresh.py），PR 评审中、待关闭、等对方、
   已处理完的 Agent 流程都改归待你回，行上写「谁 几点 @了你：第一句」；Agent 正在处理或等你决定、等你看的仍归 Agent 流程，只标新增几条。
   Agent 上一轮看过这句、说不用动的仍在待你回，只是不再派（settle）。
5. 等对方回（wait）：你最后说话且在问人（@ 了人、请对方定口径如「回 1A」）；上游仓库里你最后说话。
6. 待动手（todo）：你开的、你最后说话、没在问人、没有 open PR，在能让 Agent 改代码的仓库（自己的）。
7. 待你看（look）：都不中的。文字规则判不准时落这里，不落「待动手」。

谁最后说话用 conversation.assess（PR 行用的同一个函数，机器人和邮件自动回复不算），收件箱里同一条结论一致。
按钮：flow 沿用原来的「已交给 / 再交给 Agent」；reply 在所有仓库出「帮我回复」（issue 留言流程，Agent 写回复草稿，你确认后才发）；
todo 出「交给 Agent」；look 只在详情里出「仍交给 Agent」；其余不出。交给接口按同一结果把关（allowed）。

收件箱左列的筛选是合并后的（bucket，三位评审的共识，用户 2026-11-18 定）：PR 评审中、待关闭、等对方回并成「等对方」，
Agent 流程中里等你决定、等你看和派发卡住的并进「待你回」（点开就能回答或再交），其余流程中的是「Agent 处理中」。
细类（key）照旧留着，行上那一句话（line）写出区别。

待动手的漏判（用户 2026-11-18 按三份逐条审计定的，规则见 demote）：上面落进「待动手」或「待你看」、又没有别人等你回的，
再按这些信号改判，先中先停：记录类（总表、跟踪、调研、epic……或自动生成的规划标记）→「记录」；写了重复 →「待关闭」；
引用它的 open issue 里有人写了它「暂停 / 请验收后关」→「搁置 / 待关闭」；正文、标题、标签写了依赖或待确认、指派给了别人 →「等对方回」；
已合并到主线的 PR 提到了它 →「待关闭」；超过 60 天没动静且主线 30 天没有提交 →「搁置」；没有留言的只算近 FRESH_DAYS 天新开的，
其余落「待你看」。projects.toml 标了 paused 的项目，除了 Agent 流程中的，全归「搁置」。「搁置」「记录」不出按钮。

Agent 上一轮已有结论（要你定、不用动）、之后没有新动静的（personal/settled.py，用户 2026-11-19 定，见 settle）：
落在待你回、待动手、待你看、Agent 处理中的改判成「待你定」（归待你回）或「Agent 看过」（归待你看），只在详情里留「仍交给 Agent」，
交给接口不带 force 就拒绝（allowed）。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from . import pr_feedback
from .pr_news import first_sentence
from .upstream import LOGIN

NAMES = {"flow": "Agent 流程中", "pr": "PR 评审中", "close": "待关闭", "reply": "待你回", "wait": "等对方回",
         "todo": "待动手", "look": "待你看", "hold": "搁置", "record": "记录", "decide": "待你定", "seen": "Agent 看过"}
ORDER = ["reply", "decide", "todo", "wait", "pr", "close", "look", "seen", "hold", "record", "flow"]  # 页面上标签的顺序
MENTION = re.compile(r"(?<![\w.@/-])@([A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38})(?![\w/@-])")
NOT_PEOPLE = {"media", "param", "returns", "return", "types", "type", "import", "keyframes", "property", "decorator",
              "staticmethod", "classmethod", "dataclass", "pytest", "override", "deprecated", "font-face", "supports",
              "container", "layer", "apply", "tailwind", "mention", "username", "user", "example", "here", "all",
              # 代码里的注解和装饰器（正文是纯文本，代码块里的也读得到）
              "transactional", "autowired", "component", "service", "repository", "controller", "restcontroller", "bean",
              "configuration", "entity", "table", "column", "data", "getter", "setter", "test", "before", "after",
              "requestmapping", "getmapping", "postmapping", "putmapping", "deletemapping", "pathvariable", "requestbody",
              "requestparam", "valid", "value", "slf4j", "builder", "injectable", "input", "output", "click", "submit",
              "property", "abstractmethod", "cached_property", "lru_cache", "wraps", "fixture", "mark", "router", "get", "post",
              "latest", "next", "beta", "alpha", "v1", "v2"}
# 在问人、在等对方定：「要你定」「回“1A”」「请确认」「哪个方案」……
ASK = re.compile(
    r"要你们?(?:来)?定|你们?定一下|你们?来定|请(?:你们?)?(?:先)?(?:确认|决定|定夺|拍板|回复|答复|过目|看一下|选一个|选择|定一下)"
    r"|回[“\"「『]?\s*[1-9][0-9]?[A-Za-z]|回[“\"「『]?[1-9][0-9]?(?:是|否)|等你们?(?:回|定|确认|答复|决定|拍板)"
    r"|麻烦.{0,12}?(?:确认|回复|看一下|定一下)|哪个方案|选哪|用哪(?:个|版|一版|种)|是否同意|同意吗|行不行|可不可以"
    r"|\b(?:could|can|would) you\b|\bplease (?:confirm|review|advise|let (?:me|us) know|take a look|decide)\b"
    r"|\blet (?:me|us) know\b|\bwdyt\b|\bany thoughts\b", re.I)
# 你最后一条写明做完了、等对方验收关闭
DONE = re.compile(r"请(?:你们?)?验收|验收(?:并|后|通过后)?关闭|待验收|可以关(?:闭|掉)|请(?:你们?)?关闭|关闭即可|可关闭"
                  r"|\b(?:can|could) be closed\b|\bfeel free to close\b|\bready to close\b|\bplease close\b", re.I)
QUESTION = re.compile(r"[?？]")
# 你最后一条写了先不做、暂缓，或只是回一句「收到」：下一步看不准，不落「待动手」
HOLD = re.compile(r"暂时不做|暂不做|先不做|先不跑|先不开|不做了|搁置|暂缓|延后|等.{1,12}再(?:开|做|跑|说)|\bon hold\b|\bnot now\b", re.I)
ACK = re.compile(r"\s*(?:收到|好的|好|谢谢|感谢|ok|okay|thanks|thank you|got it)[。！!.，, ]*\s*", re.I)
CLAUSE = "，,：:；;。！？!?\n"
# 记录类：标题或标签里有这些词（英文按整词）；正文带自动生成的规划标记由 upstream 认（plan）。
# 「记录」「进度」要单独成词（标题开头、分隔符后，后面接结尾、分隔符或「和」），「处理记录表」「自测记录」这类功能名不算
RECORD = re.compile(r"总表|总览|跟踪|调研|(?:^|(?<=[：:\]】·|｜\s]))(?:进度|记录)(?=$|[：:和与、\s])"
                    r"|(?<![A-Za-z])(?:overview|epic|tracking|roadmap)(?![A-Za-z])", re.I)
# 正文写了依赖、要别人先定：「依赖：无」不算，「依赖：#4 #9」里的 issue 都已关闭也不算；「待确认草稿」这类功能名不算
DEPENDS = re.compile(r"依赖\s*[：:]\s*(?!无|没有|none\b)|依赖王子|请总头|等[^，,。；;\n]{0,8}注册|营收[^，,。；;\n]{0,8}?\d[\d,.]*\s*万元?(?:之)?后"
                     r"|待客户确认|待确认(?!草稿|状态|列表|的)", re.I)
ONLY_ISSUES = re.compile(r"(?:#\d+|[\s、，,和与及到至-])+")
WAIT_TITLE = re.compile(r"\s*待确认\s*[：:]")
WAIT_LABELS = {"question", "needs-backend", "needs-info", "blocked", "waiting", "待客户确认"}
DUPLICATE = re.compile(r"重复(?:于|了)?\s*#(\d+)|(?:duplicate|dup) of\s*#(\d+)|(?:与|和|同)\s*#(\d+)\s*(?:重复|重合)", re.I)
# 引用它的 open issue 里点了它的编号、写了请验收关闭 / 暂停的那一句
CLOSE_NOTE = re.compile(DONE.pattern + r"|能不能关|能关了|可以关", re.I)
HOLD_NOTE = re.compile(r"暂停|暂不|先不做|先不测|这波不测|不测了|搁置|暂缓|延后|\bon hold\b", re.I)
NUMBER = re.compile(r"#(\d+)\b")
ROW_START = re.compile(r"\s*#(\d+)\b")
CITE = re.compile(r"(?:按|见|据|照|依|参见|如|同|在|PR|[A-Za-z0-9_.-])\s*$")  # 「按 #34」「PR #95」「repo #1155」不是在说这条
STALE_DAYS = 60  # issue 这么久没动静
QUIET_DAYS = 30  # 且主线这么久没有提交 → 搁置
FRESH_DAYS = 14  # 只有正文、没有留言的，这么多天内新开的才算待动手


def now():
    return datetime.now(timezone.utc)


def parse(at):
    try:
        return datetime.fromisoformat((at or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def days_since(at, at_now):
    found = parse(at)
    return (at_now - found).total_seconds() / 86400 if found else None


def statements(rows, me=LOGIN):
    """引用别的 issue 的那几句：同一仓库的 open issue（列表里已读到的正文和最近的留言，不另发请求）里，点了别的 issue 编号、
    又写了请验收关闭或暂停的。表格（GitHub 文本里一格一行）看「#N 开头的那一行」下面几格，到下一个 #N 开头的行为止，最多 3 行；
    句子里只认编号后面写的，#N 开头的一句只认冒号后面的，「按 #34」「PR #95」这类引用不算。返回 {"owner/repo#N"（小写）: [{kind: close / hold, what, src, who, at, url}]}。"""
    out = {}
    for row in rows:
        thread_ = row.get("thread") or {}
        repo = row["repository"]["nameWithOwner"]
        texts = [((thread_.get("author") or {}), thread_.get("createdAt") or "", row.get("text") or thread_.get("bodyText") or "",
                  thread_.get("url") or "")]
        texts += [((c.get("author") or {}), c.get("createdAt") or "", c.get("bodyText") or "", c.get("url") or "")
                  for c in ((thread_.get("comments") or {}).get("nodes") or []) if c]
        for author, at, text, url in texts:
            if pr_feedback.is_bot(author) or not text:
                continue
            found = {}
            lines = [line for line in text.split("\n") if line.strip()]
            for i, line in enumerate(lines):
                start = ROW_START.match(line)
                if not start:
                    continue
                window = []  # 第一行是编号和标题（标题里的「暂停」常是功能名），只看后面几格；同一行里写的由下面按句子认
                for nxt in lines[i + 1:i + 4]:
                    if ROW_START.match(nxt):
                        break
                    window.append(nxt)
                cell = "\n".join(window)
                for kind, pattern in (("close", CLOSE_NOTE), ("hold", HOLD_NOTE)):
                    hit = pattern.search(cell)
                    if hit:
                        found.setdefault(int(start[1]), (kind, clause_of(cell, hit)))
                        break
            for sentence in re.split(r"[。！？!?；;\n]", text):
                listed = ROW_START.match(sentence)  # 「#58 标题……」开头的一句：只认冒号后面写的状态，标题里的词不算
                for mention in NUMBER.finditer(sentence):
                    if CITE.search(sentence[:mention.start()]):
                        continue
                    rest = sentence[mention.end():]
                    if listed:
                        colon = re.search(r"[：:]", rest)
                        rest = rest[colon.end():] if colon else ""
                    for kind, pattern in (("close", CLOSE_NOTE), ("hold", HOLD_NOTE)):
                        hit = pattern.search(rest)
                        if hit:
                            found.setdefault(int(mention[1]), (kind, " ".join(sentence.split())[:60]))
                            break
            for number, (kind, what) in found.items():
                if number == row["number"]:
                    continue
                out.setdefault(f"{repo}#{number}".lower(), []).append(
                    {"kind": kind, "what": what, "src": row["number"], "who": author.get("login") or "", "at": at, "url": url})
    return out


def mentions(text, me=LOGIN):
    """正文里 @ 的人（去掉自己、机器人和像代码的写法），按出现顺序。"""
    out = []
    for name in MENTION.findall(text or ""):
        low = name.lower()
        if low == me.lower() or low in NOT_PEOPLE or low in pr_feedback.BOTS or name in out:
            continue
        out.append(name)
    return out


def clause_of(text, found, limit=40):
    """匹配处所在的那个分句（逗号、冒号、句号都断开），压成一行。"""
    text = text or ""
    start = max(text.rfind(c, 0, found.start()) for c in CLAUSE) + 1
    ends = [i for i in (text.find(c, found.end()) for c in CLAUSE) if i >= 0]
    clause = " ".join(text[start:min(ends) if ends else len(text)].split())
    return clause if len(clause) <= limit else clause[:limit - 1].rstrip() + "…"


def asking(mine, me=LOGIN):
    """你最后一句（last_mine）在不在问人：{"who": [@ 的人], "what": 问的那个分句, "done": 写了验收关闭,
    "unsure": 判不准下一步的原因（有问句但看不出问谁、写了先不做、只回了一句收到）}。
    正文只认 @，问句、验收关闭都不认（需求正文里常有「口径」「验收标准」这类词）。"""
    text = (mine or {}).get("text") or ""
    body = bool((mine or {}).get("body"))
    who = mentions(text, me)
    out = {"who": who, "what": "", "done": False, "unsure": ""}
    if body:
        return out
    done = DONE.search(text)
    if done:
        out.update(done=True, what=clause_of(text, done))
        return out
    asks = list(ASK.finditer(text))
    if asks:  # 「回“1A”」这种直接说怎么回的优先
        pick = next((m for m in asks if m.group(0).startswith("回")), asks[0])
        out["what"] = clause_of(text, pick)
    elif not who:
        hold = HOLD.search(text)
        if hold:
            out["unsure"] = f"你最后一条写了「{clause_of(text, hold)}」，先不做还是要做判不准"
        elif ACK.fullmatch(text):
            out["unsure"] = "你最后一条只回了一句收到，下一步是谁的判不准"
        elif QUESTION.search(text):
            out["unsure"] = "你最后一条有问句，判不准是在问人还是自己记的"
    return out


def waits_on_others(info, me=LOGIN):
    """你最后一句在问人或请对方验收关闭：消息页据此把「你开的、还没别人留言」的 issue 列进「等对方」，纯自记的仍不列。"""
    ask = asking((info or {}).get("last_mine"), me)
    return bool(ask["who"] or ask["what"] or ask["done"])


def when(at):
    """'2026-11-18T03:02:16Z' → '11-18 11:02'（本机时区）。"""
    if not at:
        return ""
    from datetime import datetime
    try:
        return datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone().strftime("%m-%d %H:%M")
    except ValueError:
        return at[5:16].replace("T", " ")


def result(key, line, next_, why, *, who="", at="", quote="", url="", prs=None, action="", drawer_only=False, button=""):
    return {"key": key, "name": NAMES[key], "line": line, "next": next_, "why": why, "who": who, "at": at,
            "quote": quote, "url": url, "prs": prs or [], "action": action, "drawer_only": drawer_only, "button": button}


def on_main(ref, main):
    """这个已合并的 PR 合进了项目主线：知道主线（main_branch，没配是默认分支）和 PR 的目标分支就比分支名；不知道的只认写了关闭的。"""
    if ref.get("state") != "MERGED":
        return False
    branch = (main or {}).get("branch") or ""
    if branch and ref.get("base"):
        return ref["base"].lower() == branch.lower()
    return bool(ref.get("closes"))


def classify(*, repo, author, thread, refs, flow=None, upstream=False, involved=False, seen=None, me=LOGIN, assess=None,
             title="", labels=(), assignees=(), plan=False, unchecked=0, text="", updated_at="", main=None, paused=None, notes=(),
             open_numbers=None, at_now=None, notice=None):
    """一条 issue 的下一步。thread 是列表里顺带读的 issue 节点（upstream.thread），没读到时为 None；
    refs 是引用它的 PR；flow 是 Agent 流程（{stage, label, kind, at}，kind 是 issue / feedback），没有为 None。
    involved：这条在「与我有关」的跨仓库搜索里（消息页的来源）。
    seen：「处理 issue 留言」已处理完的那一轮（{"their_at": 交给时对方最后一条的时间, "run": 轮次, "result": 结果, "created_at"}）；
    对方没有更晚的留言就不算待你回，和消息页一样归等对方（回执说回复了、你的账号却没有更晚的留言时照算待你回）。assess 缺省是 conversation.assess。
    其余给改判（demote）用：title、labels、assignees、plan（正文带自动生成的规划标记）、unchecked（正文没勾的清单项数）、
    text（正文全文）、updated_at、main（{branch, committed_at}：项目主线和它最近一次提交）、paused（项目暂停时是备注，没暂停是 None）、
    notes（引用它的 open issue 里点了它的那几句，见 statements）、open_numbers（同仓库 open issue 的编号，认「依赖：#N」满足没有）、
    at_now（现在，测试用）。"""
    if assess is None:
        from .conversation import assess
    if not flow and paused is not None:
        note = str(paused or "").strip()
        return result("hold", "项目已暂停" + (f"：{note}" if note else ""), "项目恢复后再看",
                      "projects.toml 里这个项目标了 paused" + (f"：{note}" if note else ""))
    step = base(repo=repo, author=author, thread=thread, refs=refs, flow=flow, upstream=upstream, involved=involved, seen=seen, me=me, assess=assess, main=main)
    heard = news(thread, author=author, upstream=upstream, involved=involved, me=me, assess=assess) or noticed(notice)
    if heard:
        return answer(step, heard, flow)
    if step["key"] not in ("todo", "look") or thread is None or assess(thread, me)["pending"]:
        return step
    return demote(step, repo=repo, thread=thread, refs=refs, me=me, assess=assess, title=title, labels=labels, assignees=assignees,
                  plan=plan, unchecked=unchecked, text=text or thread.get("bodyText") or "", updated_at=updated_at, main=main,
                  notes=notes, open_numbers=open_numbers, at_now=at_now or now()) or step


def demote(step, *, repo, thread, refs, me, assess, title, labels, assignees, plan, unchecked, text, updated_at, main, notes, open_numbers,
           at_now):
    """落进待动手或待你看、又没有别人等你回的 issue，按审计找出的信号改判；都不中返回 None（照原来的）。"""
    labels = [str(x) for x in labels or []]
    hit = RECORD.search(title or "")
    label = next((x for x in labels if RECORD.search(x)), "")
    if hit or label or plan:
        line = f"标题写了「{hit[0]}」" if hit else f"标签 {label}" if label else "正文带自动生成的规划标记"
        return result("record", f"{line}，是记录", "不用动手，留作记录",
                      f"{line}：总表、跟踪、调研、epic、规划这类是记录，不是一件活", url=thread.get("url") or "")
    comments = [c for c in ((thread.get("comments") or {}).get("nodes") or []) if c and not pr_feedback.is_bot(c.get("author"))]
    dup = None if "duplicate" not in [x.lower() for x in labels] else ""
    for words in [text] + [c.get("bodyText") or "" for c in comments]:
        found = DUPLICATE.search(words or "")
        if found:
            dup = next(g for g in found.groups() if g)
            break
    if dup is not None:
        line = f"和 #{dup} 重复" if dup else "标了 duplicate"
        return result("close", f"{line}，可关闭", "核对后关闭", f"{line}：重复的 issue 留一条就够")
    said_ = sorted(notes or [], key=lambda n: n.get("at") or "")
    if said_:
        n = said_[-1]
        who = n.get("who") or "有人"
        line = f"#{n['src']} 里 {who} {when(n.get('at'))[:5]} 写了「{n['what']}」"
        why = f"引用这条的 #{n['src']} 里点了它的编号：{n['what']}"
        if n["kind"] == "close":
            return result("close", line, "验收后关闭", why, who=who, at=n.get("at") or "", quote=n["what"], url=n.get("url") or "")
        return result("hold", line, "暂停中，恢复后再做", why, who=who, at=n.get("at") or "", quote=n["what"], url=n.get("url") or "")
    others = [a for a in assignees or [] if a and a.lower() != me.lower()]
    tags = [x for x in labels if x.lower() in WAIT_LABELS or x.lower().rsplit(":", 1)[-1] in WAIT_LABELS]
    depends = next((m for m in DEPENDS.finditer(text or "") if not settled(text, m, open_numbers)), None)
    if others or tags or depends or WAIT_TITLE.match(title or ""):
        if others:
            what, next_ = f"指派给了 {'、'.join(others[:3])}", f"等 {others[0]}"
        elif tags:
            what, next_ = f"标签 {tags[0]}", "等对方"
        elif depends:
            what, next_ = f"正文写了「{phrase(text, depends)}」", "等对方"
        else:
            what, next_ = "标题写了待确认", "等对方确认"
        return result("wait", f"等对方：{what}", next_, f"{what}，先等别人", url=thread.get("url") or "")
    merged = [r for r in refs or [] if on_main(r, main)]
    if merged:
        r = next((x for x in merged if x.get("closes")), merged[0])
        partial = not r.get("closes") and unchecked
        line = f"PR #{r['number']} 已合并，" + (f"部分已做（正文还有 {unchecked} 项没勾）" if partial else "可验收关闭")
        return result("close", line, "核对剩余项后关闭" if partial else "验收后关闭",
                      f"提到这条的 PR #{r['number']} 已合并到主线{(main or {}).get('branch') and ' ' + main['branch'] or ''}，issue 还开着",
                      url=r["url"], prs=[{"number": x["number"], "title": x["title"], "url": x["url"]} for x in merged])
    idle = days_since(updated_at or thread.get("createdAt"), at_now)
    quiet = days_since((main or {}).get("committed_at"), at_now)
    if idle is not None and quiet is not None and idle > STALE_DAYS and quiet > QUIET_DAYS:
        last = (updated_at or thread.get("createdAt") or "")[:10]
        pushed = main["committed_at"][:10]
        return result("hold", f"最后动静 {last}，主线 {pushed} 后没有提交", "久未动，确认还要不要",
                      f"超过 {STALE_DAYS} 天没有动静，主线 {QUIET_DAYS} 天没有提交", at=updated_at or "")
    if step["key"] == "todo" and not comments and not assess(thread, me)["replies"]:
        age = days_since(thread.get("createdAt"), at_now)
        if age is not None and age > FRESH_DAYS:
            return result("look", f"你 {when(thread.get('createdAt'))[:5]} 开的，只有正文、没有留言", "看不准：还做不做先看一眼",
                          f"只有正文、没有留言，开了 {int(age)} 天，判不出还是不是活", who=me, at=thread.get("createdAt") or "",
                          url=thread.get("url") or "", button="仍交给 Agent", drawer_only=True, action="issue")
    return None


BUSY = {"已交给 Agent", "处理中", "审查中", "返工中"}  # Agent 正在做：新留言会接进这一轮，不另归待你回
ASKING = {"等你决定", "等你看"}  # 本来就在待你回，行上写 Agent 问了什么


def news(thread, *, author, upstream, involved, me, assess):
    """别人（真人，不是你、不是机器人、不是邮件自动回复）在你最后一次动作之后说了话（用户 2026-11-20 定）：
    返回 {line, why, who, at, quote, url, new}；不算的是 None。你最后一次动作包括 Agent 用你的账号发出的（你确认过的草稿）。
    要和你有关：issue 是你开的、你留过言、新留言 @了你，或（自己的仓库）在与我有关的搜索里。只有正文的 issue 照原来的规则。"""
    if thread is None:
        return None
    info = assess(thread, me)
    if not info["pending"] or info.get("body_only"):
        return None
    new = [e for e in info.get("replies") or [] if e["at"] > (info.get("mine_at") or "")]
    if not new:
        return None
    opener = ((thread.get("author") or {}).get("login") or author or "").lower()
    tagged = [e for e in new if me.lower() in [m.lower() for m in MENTION.findall(e.get("text") or "")]]
    if not (opener == me.lower() or info.get("mine_at") or tagged or (involved and not upstream)):
        return None
    last = new[-1]
    verb = "@了你" if last in tagged else "留言"
    words = first_sentence(last.get("text") or "", 60)
    line = f"{last['who']} {when(last['at'])} {verb}" + (f"：{words}" if words else "")
    why = f"{last['who']} 在你最后一次动作之后说了话" + (f"（共 {len(new)} 条）" if len(new) > 1 else "")
    return {"line": line, "why": why, "who": last["who"], "at": last["at"], "quote": first_sentence(last.get("text") or "", 200),
            "url": last.get("url") or "", "new": len(new)}


def noticed(notice):
    """未读的 GitHub 通知比读到的数据新（fresh.signals 的一条）：重读完成前先凭它归待你回。"""
    if not notice:
        return None
    who, at = notice.get("who") or "", notice.get("at") or notice.get("updated_at") or ""
    head = f"{who} {when(at)}" if who else f"GitHub 通知 {when(notice.get('updated_at'))}"
    line = f"{head} {notice.get('says') or '有新动静'}" + (f"：{notice['text']}" if notice.get("text") else "") + "（正在读最新留言）"
    return {"line": line, "why": f"未读的 GitHub 通知（{notice.get('says') or notice.get('reason')}）比列表里的数据新，正在重读这一条",
            "who": who, "at": at, "quote": notice.get("text") or "", "url": "", "new": 1, "notice": True}


def answer(step, heard, flow):
    """别人在你之后说了话：Agent 正在处理、或等你决定、等你看的，留在 Agent 流程里，只标出新增几条；
    其余（PR 评审中、待关闭、等对方、已处理完的流程、待动手、待你看、记录）都归待你回，原来的类别写进 why。"""
    if step["key"] == "flow" and (flow or {}).get("stage") in BUSY | ASKING:
        return {**step, "new": heard["new"]}
    said_before = step["line"] if step["key"] == "flow" else step["why"]
    before = "" if step["key"] in ("reply", "look") else f"；原来是「{step['name']}」：{said_before}"
    out = reply(heard["line"], {"their_at": heard["at"]}, {"who": heard["who"], "text": heard["quote"], "url": heard["url"]},
                why=heard["why"] + before)
    return {**out, "prs": step.get("prs") or [], "new": heard["new"], **({"notice": True} if heard.get("notice") else {})}


def phrase(text, found, limit=40):
    """从匹配处到这一句结束，压成一行。"""
    end = min([i for i in (text.find(c, found.end()) for c in "。；;\n") if i >= 0] or [len(text)])
    words = " ".join(text[found.start():end].split())
    return words if len(words) <= limit else words[:limit - 1].rstrip() + "…"


def settled(text, found, open_numbers):
    """「依赖：#4 #9」只写了同仓库的 issue 编号，又都不在 open 列表里（已关闭）：依赖已经满足。不知道哪些 open 时不算满足。"""
    if open_numbers is None or not found.group(0).startswith("依赖"):
        return False
    rest = phrase(text, found, 200)[len(found.group(0).strip()):].strip(" 。.")
    numbers = [int(n) for n in NUMBER.findall(rest)]
    return bool(numbers) and bool(ONLY_ISSUES.fullmatch(rest)) and not set(numbers) & set(open_numbers)


def base(*, repo, author, thread, refs, flow, upstream, involved, seen, me, assess, main):
    """原来的七条规则（见模块说明）。已合并、写了关闭的 PR 只认合进主线的（不知道主线时照旧）。"""
    mine_repo = not upstream  # 自己的仓库：Agent 能改代码、开 PR
    handoff = "issue"  # 「仍交给 Agent」走的流程
    if flow:
        line = flow["label"] + (f" · {when(flow['at'])} 交给" if flow.get("at") else "")
        why = "「处理 issue 留言」有这条的交给记录或轮次" if flow["kind"] == "feedback" else "中台有这条的轮次、交给记录或 agent 标签"
        return result("flow", line, f"Agent {flow['label']}" if not flow["label"].startswith(("等你", "已")) else flow["label"],
                      why, action=flow["kind"])
    open_prs = [r for r in refs or [] if r.get("state") == "OPEN"]
    if open_prs:
        names = "、".join(f"#{r['number']}" for r in open_prs)
        first = open_prs[0]
        return result("pr", f"PR {names} 开着，等评审", f"等 PR {names} 评审",
                      f"有引用这条的 open PR：#{first['number']} {first['title']}",
                      prs=[{"number": r["number"], "title": r["title"], "url": r["url"]} for r in open_prs], url=first["url"])
    merged = [r for r in refs or [] if r.get("closes") and on_main(r, main)]
    if merged:
        r = merged[0]
        return result("close", f"PR #{r['number']} 已合并，请验收并关闭", "请验收并关闭",
                      f"写了关闭这条的 PR #{r['number']} 已合并，issue 还开着", url=r["url"],
                      prs=[{"number": r["number"], "title": r["title"], "url": r["url"]}])
    if thread is None:
        return result("look", "还没读到留言", "看不准：先看一眼", "列表里没读到这条的留言，判断不了谁最后说话",
                      button="仍交给 Agent", drawer_only=True, action=handoff)
    info = assess(thread, me)
    opener = ((thread.get("author") or {}).get("login") or author or "")
    by_me = opener.lower() == me.lower()
    owner = repo.split("/", 1)[0]
    last = info.get("last")
    said = (info.get("said") or [{}])[0]
    mine = info.get("last_mine")
    ask = asking(mine, me)
    i_spoke_last = bool(info.get("mine_at")) and not info["pending"]
    if i_spoke_last and ask["done"]:
        who = (ask["who"] or [opener if not by_me else owner])[0]
        who = who if who.lower() != me.lower() else "对方"
        return result("close", f"你 {when(mine['at'])} 写了请验收关闭", f"等 {who} 验收后关闭",
                      f"你最后一条写了「{ask['what']}」", who=me, at=mine["at"], quote=first_sentence(mine["text"], 200), url=mine["url"])
    claimed = (seen or {}).get("result") or {}
    unseen = claimed.get("kind") in ("replied", "pr") and not pr_feedback.after(info.get("mine_at"), (seen or {}).get("created_at"))
    if info["pending"] and seen and seen.get("their_at") and info["their_at"] <= seen["their_at"] and not unseen:
        talker = said.get("who") or "对方"
        reason = (seen.get("result") or {}).get("reason") or ""
        return result("wait", f"Agent 第 {seen.get('run')} 轮看过 {talker} 的最后一条", f"等 {talker} 回复",
                      f"「处理 issue 留言」第 {seen.get('run')} 轮已处理 {talker} 的最后一条" + (f"：{reason}" if reason else ""),
                      who=talker, at=info["their_at"], quote=said.get("text") or "", url=said.get("url") or "")
    if info["pending"]:
        if info.get("body_only"):
            text = f"{opener} {when(info['their_at'])} 开的，你还没回"
            return reply(text, info, said, why="别人开的 issue，只有正文，你还没留过言")
        talker = said.get("who") or "对方"
        if upstream:
            return result("look", f"{talker} {when(info['their_at'])} 回了，上游仓库", "看不准：先看一眼",
                          "上游仓库别人最后说话，判不出是要你回、要你关闭还是还有活", who=talker, at=info["their_at"],
                          quote=said.get("text") or "", url=said.get("url") or "", button="仍交给 Agent", drawer_only=True, action=handoff)
        if involved or by_me or me.lower() in [m.lower() for m in MENTION.findall(said.get("text") or "")] or info.get("mine_at"):
            return reply(f"{talker} {when(info['their_at'])} 留言" + ("，@了你" if f"@{me}".lower() in (said.get("text") or "").lower() else ""),
                         info, said, why=f"别人（非机器人）最后说话：{talker}" + ("，在你开的 issue 下" if by_me else ""))
        return result("look", f"{talker} {when(info['their_at'])} 留言，没 @你", "看不准：先看一眼",
                      "别人最后说话，但没有 @你，也不是你开的", who=talker, at=info["their_at"], quote=said.get("text") or "",
                      url=said.get("url") or "", button="仍交给 Agent", drawer_only=True, action=handoff)
    if last and not last["mine"] and not last["reply"] and last["at"] > (info.get("mine_at") or ""):
        return result("look", f"{last['who'] or '自动提醒'} {when(last['at'])} 的自动留言", "看不准：先看一眼",
                      "最后一条是机器人或邮件自动回复", who=last["who"], at=last["at"], url=last["url"],
                      button="仍交给 Agent", drawer_only=True, action=handoff)
    if mine:
        whom = ask["who"] or ([opener] if not by_me else []) or ([said["who"]] if said.get("who") else []) or \
            ([owner] if owner.lower() != me.lower() else [])
        target = "、".join(whom[:3]) or "对方"
        what = (f" {ask['what']}" if ask["what"].startswith("回") else f" 回复：{ask['what']}") if ask["what"] else " 回复"
        quote = first_sentence(mine["text"], 200)
        where = "正文" if mine.get("body") else "留言"
        if ask["who"] or ask["what"]:
            how = f"@{ask['who'][0]}" if ask["who"] else ""
            why = "最后一条是你写的" + (f"，{how}" if how else "") + (f"，写了「{ask['what']}」" if ask["what"] else "")
            return result("wait", f"你 {when(mine['at'])} {where}，等 {target} 回", f"等 {target}{what}", why,
                          who=me, at=mine["at"], quote=quote, url=mine["url"])
        if upstream:
            return result("wait", f"上游仓库，你 {when(mine['at'])} 最后说话", f"等 {target if whom else '上游'} 回复",
                          "上游仓库里你最后说话", who=me, at=mine["at"], quote=quote, url=mine["url"])
        if ask["unsure"]:
            return result("look", f"你 {when(mine['at'])} 留言，下一步看不准", "看不准：先看一眼",
                          ask["unsure"], who=me, at=mine["at"], quote=quote, url=mine["url"],
                          button="仍交给 Agent", drawer_only=True, action=handoff)
        if by_me and mine_repo:
            line = f"你开的，最后一条是你 {when(mine['at'])} 的{'正文' if mine.get('body') else '留言'}"
            return result("todo", line, "你或 Agent 动手", "你开的，你最后说话，没有 @别人，没有 open PR",
                          who=me, at=mine["at"], quote=quote, url=mine["url"], button="交给 Agent", action=handoff)
        return result("look", f"你 {when(mine['at'])} 最后说话，issue 不是你开的", "看不准：先看一眼",
                      "你最后说话，但这条不是你开的，判不准是谁的活", who=me, at=mine["at"], quote=quote, url=mine["url"],
                      button="仍交给 Agent", drawer_only=True, action=handoff)
    return result("look", "没有人留言", "看不准：先看一眼", "不是你开的，也没有人留言",
                  who=opener, at=thread.get("createdAt") or "", url=thread.get("url") or "",
                  button="仍交给 Agent", drawer_only=True, action=handoff)


def reply(line, info, said, *, why):
    """待你回：所有仓库都出「帮我回复」（issue 留言流程，Agent 只写回复草稿、你确认后才发；详情里和「处理并回复」并列，见 reply_mode）。
    改代码的部分按仓库走：自己的仓库在原仓库开分支开 PR，上游仓库按上游流程 fork 后开 PR，都不合并。"""
    who = said.get("who") or ""
    return result("reply", line, "你回复，或交给 Agent 写回复草稿", why, who=who, at=info["their_at"],
                  quote=said.get("text") or "", url=said.get("url") or "", button="帮我回复", action="feedback")


BUCKETS = {"reply": "待你回", "todo": "待动手", "agent": "Agent 处理中", "wait": "等对方", "look": "待你看", "hold": "搁置", "record": "记录"}
NEEDS_YOU = {"等你决定", "等你看"}


def bucket(step, *, stage="", stalled=""):
    """收件箱筛选：待你回、待动手、Agent 处理中、等对方、待你看、搁置、记录。Agent 流程中的按阶段分：等你决定、等你看，
    或交给了却没派出（只有标签、派发可能丢了）要你再交的归待你回，其余归 Agent 处理中。
    Agent 上一轮已有结论、之后没有新动静的（settle）：待你定归待你回，Agent 看过（不用动）归待你看。"""
    key = step["key"]
    if key == "flow":
        return "reply" if stage in NEEDS_YOU or (stage == "已交给 Agent" and stalled) else "agent"
    if key == "seen" and step.get("new"):
        return "reply"  # Agent 看过、说不用动，可对方说的话你还没回：留在待你回，只是不再派（settle）
    return {"pr": "wait", "close": "wait", "decide": "reply", "seen": "look"}.get(key, key)


SETTLE_FROM = {"reply", "todo", "look", "agent"}  # 这几个筛选里的才改判成待你定、Agent 看过；等对方、搁置、记录本来就不出按钮


def settle(step, found, *, stage="", stalled=""):
    """Agent 上一轮已有结论、之后没有新动静（settled.holding 给的 found）：改判成待你定（decide）或 Agent 看过（seen）。
    行上写 Agent 要你定的那一句或它的结论；列表上不出按钮、不能批量交，详情里只留「仍交给 Agent」（交给接口要带 force）。
    原来落在等对方、搁置、记录的不动（返回原来的）。"""
    if not found or bucket(step, stage=stage, stalled=stalled) not in SETTLE_FROM:
        return step
    from .settled import DECIDE, when
    at = when(found.get("finished_at"))
    head = f"Agent 第 {found['run']} 轮" + (f"（{at}）" if at else "")
    if found["kind"] == DECIDE:
        out = result("decide", f"Agent 要你定：{found['line']}", "你来定：回答 Agent 的提问，或在 GitHub 上回复",
                     f"{head}的结论是要你定，之后没有新的留言、评审或提交，再派一轮也是同样的结论")
    elif step.get("new"):  # 对方说的话 Agent 看过、说不用动，你还没回：行上写对方那句和 Agent 的结论，留在待你回
        out = result("seen", f"{step['line']} · Agent 看过，不用动：{found['line']}", "你回复，或暂时不处理",
                     f"{head}已经看过这条、结论是不用动，之后没有新的留言、评审或提交；对方的话你还没回")
    else:
        out = result("seen", f"Agent 看过，不用动：{found['line']}", "看一眼 Agent 的结论：关掉、回复，或暂时不处理",
                     f"{head}已经看过、结论是不用动，之后没有新的留言、评审或提交")
    return {**out, "who": step.get("who") or "", "at": step.get("at") or "", "quote": step.get("quote") or "", "url": step.get("url") or "",
            "prs": step.get("prs") or [], "action": found.get("flow") or step.get("action") or "issue", "drawer_only": True,
            "button": "仍交给 Agent", "settled": found, **({"new": step["new"]} if step.get("new") else {})}


def allowed(step, force=False):
    """交给接口的把关：返回 (走哪个流程, 拒绝原因)。flow 行沿用原来的「再交给」，still 在派、在跑的由各流程自己挡。
    待你定、Agent 看过（settle）的不带 force 就拒绝，说明 Agent 上一轮的结论；force 是详情里的「仍交给 Agent」。"""
    if step.get("settled"):
        if not force:
            from .settled import refusal
            return "", refusal(step["settled"])
        return step["action"] or "issue", ""
    if step["key"] == "flow":
        return step["action"] or "issue", ""
    if step["action"]:
        return step["action"], ""
    reasons = {
        "pr": "这条有引用它的 open PR，下一步在 PR 评审，PR 的跟进在收件箱里那条 PR 上交给 Agent",
        "close": "这条已修好、等验收关闭，不用交给 Agent",
        "wait": "这条在等对方回，对方回了再交给 Agent",
        "hold": "这条在搁置（项目暂停或久未动），恢复后再交给 Agent",
        "record": "这条是记录（总表、跟踪、调研一类），不是一件活，不用交给 Agent",
    }
    return "", reasons.get(step["key"], "这条现在不用交给 Agent")
