"""项目详情「我的 PR」的新消息：按通知主题对上 PR、已合并带未读的提出来、谁说了什么的批量读取和缓存、最近动态排序。"""
import re

import pytest

from personal import github, pr_news

REPO = "up/x"


@pytest.fixture(autouse=True)
def fresh():
    github.reset(); pr_news.reset()
    yield
    github.reset(); pr_news.reset()


def thread(number, kind="PullRequest", unread=True, updated="2026-11-12T08:42:29Z", repo="Up/X", last_read="2026-11-12T07:00:00Z"):
    path = "pulls" if kind == "PullRequest" else "issues"
    raw = {"id": str(number), "unread": unread, "reason": "mention", "updated_at": updated, "repository": {"full_name": repo},
           "subject": {"title": f"标题 {number}", "type": kind, "url": f"https://api.github.com/repos/{repo}/{path}/{number}",
                       "latest_comment_url": None}}
    if last_read is not ...:
        raw["last_read_at"] = last_read
    return raw


def pr(number, state="open", **extra):
    return {"number": number, "title": f"PR {number}", "url": f"https://github.com/up/x/pull/{number}", "state": state, "draft": False,
            "review": "", "checks": "SUCCESS", "created_at": "2026-11-11T00:00:00Z", "updated_at": "2026-11-12T07:00:00Z",
            "merged_at": "2026-11-12T07:28:47Z" if state == "merged" else None,
            "closed_at": "2026-11-12T07:28:47Z" if state != "open" else None, **extra}


def report(open_=(), merged=(), closed=()):
    return {"repo": "up/x", "available": True, "prs": {"open": list(open_), "merged": list(merged), "closed": list(closed)},
            "counts": {"pending": 0, "open": len(open_), "merged": len(merged)}}


def comment(who, at, text="thanks!", n=1):
    return {"author": {"login": who}, "createdAt": at, "bodyText": text, "url": f"https://github.com/up/x/pull/7310#issuecomment-{n}"}


def node(comments=(), reviews=()):
    return {"__typename": "PullRequest", "comments": {"nodes": list(comments)}, "reviews": {"nodes": list(reviews)}}


def test_unread_notification_marks_the_matching_pr_and_merged_ones_are_lifted_out():
    rows = [thread(7310), thread(7315, updated="2026-11-12T07:38:46Z"), thread(7287, unread=False),
            thread(7999, repo="up/x-old", updated="2026-11-10T00:00:00Z"), thread(7274, kind="Issue", updated="2026-11-11T15:45:42Z")]
    found = {"up/x#7310": {"count": 2, "more": False, "who": "SparkofSpike", "what": "评论", "at": "2026-11-12T08:42:00Z",
                           "text": "建议拆开。", "url": "https://github.com/up/x/pull/7310#issuecomment-9"}}
    base = report(open_=[pr(7321), pr(7315)], merged=[pr(7310, "merged"), pr(7287, "merged")], closed=[pr(7155, "closed")])
    out = pr_news.annotate(base, rows, found)
    news = {p["number"]: p["news"] for group in ("open", "merged", "closed") for p in out["prs"][group]}
    assert news[7310]["count"] == 2 and news[7310]["who"] == "SparkofSpike" and news[7310]["url"].endswith("issuecomment-9")
    assert news[7315] == {"count": None, "more": False, "who": "", "what": "", "at": "2026-11-12T07:38:46Z", "text": "",
                          "url": "https://github.com/Up/X/pull/7315"}  # 没读到是谁：只标有新消息
    assert news[7287] is None and news[7321] is None and news[7155] is None  # 已读的、没有通知的不标
    assert [p["number"] for p in out["prs"]["news"]] == [7310]  # 已合并带未读的提出来；开着的留在原组
    assert [p["number"] for p in out["prs"]["merged"]] == [7310, 7287]  # 原分组和计数不动
    assert out["counts"] == base["counts"]
    assert out["unread_total"] == 4  # 归到这个项目的旧仓库名也算未读，但对不上 PR 行
    assert [(u["number"], u["kind"]) for u in out["unread"]] == [(7310, "pr"), (7315, "pr"), (7274, "issue")]
    assert out["unread"][0]["text"] == "建议拆开。" and out["unread"][0]["title"] == "标题 7310"
    assert "news" not in base["prs"]["open"][0]  # 不改缓存里的原值


def test_reading_the_thread_on_github_clears_the_mark_after_the_next_sync():
    base = report(merged=[pr(7310, "merged")])
    assert [p["number"] for p in pr_news.annotate(base, [thread(7310)])["prs"]["news"]] == [7310]
    after = pr_news.annotate(base, [thread(7310, unread=False)])
    assert after["prs"]["news"] == [] and after["prs"]["merged"][0]["news"] is None and after["unread"] == []


def test_top_list_keeps_three_newest_unread_threads():
    rows = [thread(n, updated=f"2026-11-12T0{n % 10}:00:00Z") for n in (7301, 7305, 7303, 7302)]
    out = pr_news.annotate(report(), rows)
    assert [u["number"] for u in out["unread"]] == [7305, 7303, 7302] and out["unread_total"] == 4


def test_summary_counts_comments_and_reviews_by_others_since_last_read():
    data = node(comments=[comment("luochen211", "2026-11-12T08:00:00Z", "me"), comment("Hmbown", "2026-11-12T06:00:00Z", "old"),
                          comment("Hmbown", "2026-11-12T08:10:00Z", "Merged, thanks. More below.", 2)],
                reviews=[{"author": {"login": "SparkofSpike"}, "bodyText": "", "submittedAt": "2026-11-12T08:20:00Z",
                          "url": "https://github.com/up/x/pull/7310#pullrequestreview-3", "state": "CHANGES_REQUESTED"},
                         {"author": {"login": "x"}, "bodyText": "", "submittedAt": "2026-11-12T09:00:00Z", "url": "", "state": "PENDING"}])
    out = pr_news.summarize(data, "2026-11-12T07:00:00Z")
    assert out == {"count": 2, "more": False, "who": "SparkofSpike", "what": "要求修改", "at": "2026-11-12T08:20:00Z", "text": "",
                   "url": "https://github.com/up/x/pull/7310#pullrequestreview-3"}
    assert pr_news.summarize(data, None)["count"] == 3  # 从没读过：别人留的都算
    assert pr_news.summarize(data, "2026-11-12T07:00:00Z", known=False)["count"] is None  # 旧记录没有 last_read_at：不写条数
    assert pr_news.first_sentence("Merged, thanks. More below.") == "Merged, thanks."
    assert pr_news.first_sentence("很好。还有") == "很好。"
    assert pr_news.first_sentence("a" * 100).endswith("…") and len(pr_news.first_sentence("a" * 100)) == 60


def test_details_read_in_one_graphql_request_and_are_cached_until_the_thread_changes(monkeypatch):
    calls = []

    def fake(query, kind="graphql", **_):
        calls.append((kind, query))
        return {"r0": {"s7310": node([comment("Hmbown", "2026-11-12T08:30:00Z", "LGTM")]), "s7274": None}}
    monkeypatch.setattr(github, "graphql", fake)
    rows = [thread(7310), thread(7274, kind="Issue"), thread(7287, unread=False)]
    found, note = pr_news.details(rows)
    assert note == "" and len(calls) == 1 and calls[0][0] == "graphql:pr-news"
    assert "s7310: issueOrPullRequest(number: 7310)" in calls[0][1] and "7287" not in calls[0][1]  # 只读未读的
    assert found["up/x#7310"]["who"] == "Hmbown" and found["up/x#7310"]["count"] == 1
    assert found["up/x#7274"]["count"] == 0  # 读到了、没有别人的评论
    pr_news.details(rows)
    assert len(calls) == 1  # 通知没有新动态：不重读
    pr_news.details([thread(7310, updated="2026-11-12T09:00:00Z")])
    assert len(calls) == 2 and "7274" not in calls[1][1]  # 只重读有新动态的


def test_rate_limited_sends_nothing_and_marks_without_names(monkeypatch):
    monkeypatch.setattr(github, "graphql", lambda *a, **k: pytest.fail("限流时不该发请求"))
    github.strike("API rate limit exceeded")
    found, note = pr_news.details([thread(7310)])
    assert found == {} and "限流" in note
    out = pr_news.attach(report(merged=[pr(7310, "merged")]), [thread(7310)])
    assert out["prs"]["news"][0]["news"]["count"] is None and "限流" in out["news_note"]


def test_failed_read_is_not_retried_for_five_minutes(monkeypatch):
    calls = []

    def broken(*a, **k):
        calls.append(1)
        raise RuntimeError("boom")
    monkeypatch.setattr(github, "graphql", broken)
    found, note = pr_news.details([thread(7310)], now=1000.0)
    assert found == {} and "boom" in note
    pr_news.details([thread(7310)], now=1100.0)
    assert len(calls) == 1
    pr_news.details([thread(7310)], now=1400.0)
    assert len(calls) == 2


def test_last_activity_picks_the_latest_and_says_who_did_what():
    detail = {"who": "Hmbown", "what": "评论", "at": "2026-11-12T08:34:00Z"}
    assert pr_news.last_activity(pr(7315, updated_at="2026-11-12T08:34:20Z"), thread(7315, updated="2026-11-12T08:34:05Z"), detail) == \
        {"at": "2026-11-12T08:34:00Z", "who": "Hmbown", "what": "评论"}  # 和最晚时刻差不到一分钟：用有说明的
    assert pr_news.last_activity(pr(7310, "merged", updated_at="2026-11-12T07:28:50Z")) == \
        {"at": "2026-11-12T07:28:47Z", "who": "", "what": "已合并"}
    assert pr_news.last_activity(pr(7321, checks_at="2026-11-12T07:38:00Z", updated_at="2026-11-12T07:37:00Z")) == \
        {"at": "2026-11-12T07:38:00Z", "who": "", "what": "CI 通过"}
    assert pr_news.last_activity(pr(7321, checks="PENDING", checks_at="2026-11-12T07:38:00Z", updated_at="2026-11-12T07:00:00Z")) == \
        {"at": "2026-11-12T07:00:00Z", "who": "", "what": ""}  # 运行中的 CI 不当动态；说不清谁做了什么只给时间
    assert pr_news.last_activity(pr(7321), thread(7321, updated="2026-11-12T09:00:00Z", unread=False))["at"] == "2026-11-12T09:00:00Z"


def test_groups_default_to_unread_first_then_latest_activity():
    base = report(open_=[pr(7322, updated_at="2026-11-12T06:00:00Z"), pr(7321, updated_at="2026-11-12T08:00:00Z"),
                         pr(7315, updated_at="2026-11-12T05:00:00Z")],
                  merged=[pr(7287, "merged", merged_at="2026-11-12T07:00:00Z", updated_at="2026-11-12T07:00:00Z"),
                          pr(7314, "merged", merged_at="2026-11-12T07:30:00Z", updated_at="2026-11-12T07:30:00Z")])
    out = pr_news.annotate(base, [thread(7315, updated="2026-11-12T07:38:46Z")])
    assert [p["number"] for p in out["prs"]["open"]] == [7315, 7321, 7322]  # 有未读的排第一，其余按最近动态
    assert [p["number"] for p in out["prs"]["merged"]] == [7314, 7287]


def page_row(raw, project="channel", classification="workspace"):
    return {"id": raw["id"], "repository": raw["repository"]["full_name"], "title": raw["subject"]["title"], "category": "与你有关",
            "unread": raw["unread"], "updated_at": raw["updated_at"], "url": f"https://github.com/{raw['repository']['full_name']}/pull/{raw['id']}",
            "classification": classification, "project_id": project if classification == "workspace" else None,
            "project_name": "通道" if classification == "workspace" else None}


def test_digest_counts_unread_per_project_and_says_who_said_what(monkeypatch):
    def fake(query, **_):
        alias = re.search(r'(r\d+): repository\(owner: "w", name: "channel"\)', query)[1]
        return {alias: {"s72": node([comment("octocat", "2026-11-12T08:49:00Z", "已合并，辛苦了。下一步")])}}
    monkeypatch.setattr(github, "graphql", fake)
    ci = thread(9, kind="CheckSuite", updated="2026-11-12T09:00:00Z", repo="w/channel")
    ci["subject"]["url"] = None
    pairs = [(page_row(r), r) for r in (thread(72, updated="2026-11-12T08:50:43Z", repo="w/channel"), ci,
                                         thread(70, unread=False, updated="2026-11-12T10:00:00Z", repo="w/channel"))]
    archived = thread(5, repo="old/done")
    pairs.append((page_row(archived, classification="archived"), archived))
    other = thread(6, repo="some/where", updated="2026-11-01T00:00:00Z")
    pairs.append((page_row(other, classification="other"), other))
    out = pr_news.digest(pairs)
    project = out["projects"]["channel"]
    assert project["unread"] == 2 and project["latest_at"] == "2026-11-12T10:00:00Z"  # 最近一条消息含已读的
    latest = project["latest"]  # 最近一条有 PR / issue 的未读，CI 排后
    assert (latest["number"], latest["who"], latest["text"], latest["kind"]) == (72, "octocat", "已合并，辛苦了。", "pr")
    assert latest["url"].endswith("#issuecomment-1")
    assert out["unread"] == 3  # 已归档项目的不算
    assert [i["id"] for i in out["latest"]] == ["9", "72", "6"]
    assert "old" not in {i["repository"].split("/")[0] for i in out["latest"]}


def test_threads_are_read_in_chunks_and_the_page_reuses_the_cache_without_new_requests(monkeypatch):
    queries = []
    def fake(query, **_):
        queries.append(query)
        out = {}
        for alias, number in re.findall(r"(r\d+): repository[^{]*\{ s(\d+):", query):
            out[alias] = {f"s{number}": node([comment("someone", "2026-11-12T08:00:00Z")])}
        return out
    monkeypatch.setattr(github, "graphql", fake)
    monkeypatch.setattr(pr_news, "CHUNK", 2)
    rows = [thread(n, repo=f"o/r{n}") for n in range(1, 6)]
    found, note = pr_news.details(rows)
    assert len(queries) == 3 and note == "" and len(found) == 5
    assert pr_news.cached(rows[0])["count"] == 1 and pr_news.cached(rows[0])["who"] == "someone"
    assert pr_news.cached({**rows[0], "unread": False}) is None  # 已读的不标
    assert pr_news.cached({**rows[0], "updated_at": "2026-11-13T00:00:00Z"}) is None  # 有了新动态、还没读到
    assert len(queries) == 3  # cached 不发请求


def test_digest_reads_every_unread_thread_but_not_archived_ones(monkeypatch):
    asked = []
    def read(keys):
        asked.extend(keys)
        return {k: node([comment("a", "2026-11-12T08:00:00Z")]) for k in keys}
    rows = [thread(n, repo="w/x") for n in range(1, 6)]
    old = thread(9, repo="old/done")
    pairs = [(page_row(r), r) for r in rows] + [(page_row(old, classification="archived"), old)]
    pr_news.digest(pairs, read=read)
    assert sorted(asked) == [f"w/x#{n}" for n in range(1, 6)]
