// 新消息：PR 行标记、「有新消息」分组、最近动态排序、项目页未读列表、按项目条目、按 issue 提示条、导航徽标（node --test 直接加载 .ts）
import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { PrList, ProjectUnread, lastLine, newsLabel, parseProjectHash, projectHash, sortPrs } from "../src/ProjectParts.ts";
import { saidLine, unreadCount, unreadView } from "../src/messages.ts";

const html = (component, props) => renderToStaticMarkup(createElement(component, props));
const pr = (number, state, extra = {}) => ({ number, title: `PR ${number}`, url: `https://github.com/up/x/pull/${number}`, state, draft: false, review: "",
  checks: "SUCCESS", created_at: "2026-11-11T00:00:00Z", updated_at: "2026-11-12T00:00:00Z", merged_at: null, closed_at: null, news: null, ...extra });
const news = (extra = {}) => ({ count: 2, more: false, who: "SparkofSpike", what: "评论", at: "2026-11-12T08:42:00Z", text: "建议拆开。",
  url: "https://github.com/up/x/pull/7310#issuecomment-9", ...extra });
const last = (at, who = "", what = "") => ({ at, who, what });
const merged7310 = pr(7310, "merged", { merged_at: "2026-11-12T07:28:47Z", news: news(), last: last("2026-11-12T08:42:00Z", "SparkofSpike", "评论") });
const activity = (extra = {}) => ({
  repo: "up/x", available: true, loaded: true, fetched_at: "2026-11-12T15:40:00+08:00", refreshing: false, limited: "", error: "",
  prs: {
    open: [pr(7322, "open", { last: last("2026-11-12T06:00:00Z") }),
           pr(7321, "open", { last: last("2026-11-12T07:38:00Z", "", "CI 通过") }),
           pr(7315, "open", { news: news({ count: null, who: "Hmbown", text: "thank you so much!" }), last: last("2026-11-12T07:34:23Z", "Hmbown", "评论") })],
    merged: [merged7310, pr(7287, "merged", { merged_at: "2026-11-12T07:29:05Z", last: last("2026-11-12T07:29:05Z", "", "已合并") })],
    closed: [], news: [merged7310],
  },
  counts: { pending: 2, open: 3, merged: 2 }, done_issues: [],
  unread: [
    { number: 7310, title: "fix 7310", kind: "pr", ...news() },
    { number: 7315, title: "fix 7315", kind: "pr", ...news({ who: "Hmbown", text: "thank you so much!", url: "https://github.com/up/x/pull/7315#issuecomment-1" }) },
    { number: 7274, title: "Robustness", kind: "issue", ...news({ who: "", text: "", count: null, url: "https://github.com/up/x/issues/7274" }) },
  ],
  unread_total: 5,
  ...extra,
});
const order = (out, numbers) => numbers.map(n => out.indexOf(`#${n}<`)).every((at, i, all) => at > -1 && (i === 0 || at > all[i - 1]));

test("merged PRs with unread messages are lifted into a 有新消息 group above the open ones", () => {
  const out = html(PrList, { activity: activity(), failed: "", expanded: false, toggle: () => {} });
  assert.match(out, /有新消息<small>1<\/small>/);
  assert.ok(out.indexOf("有新消息<small>") < out.indexOf("开着<small>"));
  assert.match(out, /href="https:\/\/github.com\/up\/x\/pull\/7310#issuecomment-9"/);  // 点开去评论位置
  assert.match(out, /2 条新评论/);
  assert.match(out, /新消息：SparkofSpike「建议拆开。」/);
  assert.match(out, /近 30 天已合并 2 条/);  // 计数不变，明细照样收起
  assert.doesNotMatch(out, /#7287/);
  assert.doesNotMatch(out, /卡/);
});

test("an open PR with an unread message is marked on its own row and sorted first", () => {
  const out = html(PrList, { activity: activity(), failed: "", expanded: false, toggle: () => {} });
  const row = out.slice(out.indexOf("#7315<"), out.indexOf("#7315<") + 900);
  assert.match(row, /有新消息/);  // 条数不明时不写条数
  assert.match(row, /新消息：Hmbown/);
  const open = out.slice(out.indexOf("开着<small>"));
  assert.ok(order(open, [7315, 7321, 7322]), "有未读的第一，其余按最近动态从新到旧");
});

test("read everywhere: no 有新消息 group, PRs fall back into place", () => {
  const plain = activity();
  plain.prs = { ...plain.prs, news: [], merged: [pr(7310, "merged"), plain.prs.merged[1]], open: plain.prs.open.map(p => ({ ...p, news: null })) };
  const out = html(PrList, { activity: plain, failed: "", expanded: false, toggle: () => {} });
  assert.doesNotMatch(out, /有新消息|条新评论/);
  assert.ok(order(out.slice(out.indexOf("开着<small>")), [7321, 7315, 7322]));
});

test("rows say 最近动态 with who did what, or only the time", () => {
  assert.equal(lastLine(last("2026-11-12T08:34:00Z", "Hmbown", "评论")).replace(/\d+\/\d+ \d+:\d+/, "T"), "最近动态：Hmbown 评论 · T");
  assert.equal(lastLine(last("2026-11-12T07:38:00Z", "", "CI 通过")).replace(/\d+\/\d+ \d+:\d+/, "T"), "最近动态：CI 通过 · T");
  assert.equal(lastLine(last("2026-11-12T07:38:00Z")).replace(/\d+\/\d+ \d+:\d+/, "T"), "最近动态：T");
  assert.equal(lastLine(undefined), "");
  const out = html(PrList, { activity: activity(), failed: "", expanded: true, toggle: () => {} });
  assert.match(out, /最近动态：CI 通过/);
  assert.match(out, /最近动态：已合并/);
  assert.doesNotMatch(out, /更新于/);
  assert.equal(newsLabel(news({ count: 3, more: true })), "3+ 条新评论");
  assert.equal(newsLabel(news({ count: 0 })), "有新消息");
});

test("sorting: latest activity by default with unread first, or by number", () => {
  const items = activity().prs.open;
  assert.deepEqual(sortPrs(items, "recent").map(p => p.number), [7315, 7321, 7322]);
  assert.deepEqual(sortPrs(items, "number").map(p => p.number), [7322, 7321, 7315]);
  const out = html(PrList, { activity: activity(), failed: "", expanded: false, toggle: () => {}, sort: "number" });
  assert.ok(order(out.slice(out.indexOf("开着<small>")), [7322, 7321, 7315]));
  assert.match(out, /aria-pressed="true"[^>]*>编号</);
  assert.equal(projectHash("codewhale"), "#/p/codewhale");
  assert.equal(projectHash("codewhale", "number"), "#/p/codewhale?sort=number");
  assert.deepEqual(parseProjectHash("#/p/codewhale?sort=number"), { id: "codewhale", sort: "number" });
  assert.deepEqual(parseProjectHash("#/p/codewhale"), { id: "codewhale", sort: "recent" });
  assert.equal(parseProjectHash("#/?v=project"), null);
});

test("the project page lists up to three unread messages linking to GitHub", () => {
  const out = html(ProjectUnread, { activity: activity() });
  assert.match(out, /未读消息<small>5<\/small>/);
  assert.match(out, /#7310 fix 7310/);
  assert.match(out, /SparkofSpike：建议拆开。/);
  assert.match(out, /href="https:\/\/github.com\/up\/x\/pull\/7315#issuecomment-1"/);
  assert.match(out, /#7274 Robustness<\/a><span class="meta"> — 有新消息/);
  assert.match(out, /另有 2 条/);
  assert.equal(html(ProjectUnread, { activity: activity({ unread: [], unread_total: 0 }) }), "");
});

const item = (extra = {}) => ({ id: "1", project_id: "channel", project_name: "通道", repository: "w/channel", number: 72, kind: "pr",
  title: "feat: 72", category: "与你有关", count: 1, more: false, who: "octocat", what: "评论", text: "已合并，辛苦了。",
  at: "2026-11-12T08:49:00Z", url: "https://github.com/w/channel/pull/72#issuecomment-1", ...extra });
test("one message reads as who said what where; without a name it is the subject and title", () => {
  assert.equal(saidLine(item()), "octocat 在 PR #72：已合并，辛苦了。");
  assert.equal(saidLine(item({ who: "" })), "PR #72 feat: 72");
});

test("unread messages are counted except archived ones, same as the 未读 filter", () => {
  const rows = [{ unread: true, classification: "workspace", done: true }, { unread: true, classification: "other" },
    { unread: true, classification: "archived" }, { unread: false, classification: "workspace" }];
  assert.equal(unreadCount(rows), 2);
  assert.equal(unreadView(rows.filter(n => n.unread), "all").length, 2);  // 已合并 PR 下的未读也列出
  assert.equal(unreadView(rows.filter(n => n.unread), "other").length, 3);  // 「其他消息」里照常列出已归档项目的
});
