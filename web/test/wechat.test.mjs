// 收件箱的「微信」范围（WechatParts.ts，后端 personal/plugins/wechat/inbox.py）：没启用不出现；只有后端判成待你回的行计入角标；
// 微信范围只列微信的行，仓库范围不列；暂时不处理和已处理送给后端的一条；新鲜度那一句。文字都是编的。
import { test } from "node:test";
import assert from "node:assert/strict";
import { canSnooze, handVia, inScope, inTab, inboxHash, parseInboxHash, replyCount, sideCounts, searchText, snoozeItem, snoozeOf, statusLine,
  withIssues } from "../src/InboxParts.ts";
import { doneItem, freshness, signalText, wechatThreads } from "../src/WechatParts.ts";

const HELD = { at: "2026-11-18T10:00:00+08:00", until: "", line: "你 11-18 设为暂时不处理", from: "reply" };
const row = (key, kind, bucket, extra = {}) => ({ key: `wechat:${kind}:${key}`, kind, label: { private: "私聊待回", at_me: "群里 @我", at_all: "@所有人", pending_to_me: "待收转账", red_packets: "红包" }[kind],
  icon: "chat", chat: `会话${key}`, ts: 1763435000, at: "2026-11-18T11:20:00+08:00", text: ["样片能先发我吗"], notes: [], hits: [], count: 1,
  low_signal: false, bucket, snooze: null, ...extra });
const report = { enabled: true, updated_at: "2026-11-18T11:37:00+08:00", rows: [
  row("aaaaaa0001", "private", "reply"), row("aaaaaa0002", "private", "look", { low_signal: true }), row("aaaaaa0003", "at_me", "reply", { count: 2 }),
  row("aaaaaa0004", "pending_to_me", "reply"), row("aaaaaa0005", "at_all", "look"), row("aaaaaa0006", "red_packets", "hold", { snooze: HELD }),
] };
const issue = { subject: "o/r#1", repo: "o/r", number: 1, project_id: "app", project_name: "App", title: "issue", url: "https://github.com/o/r/issues/1",
  labels: [], author: "boss", comments: 1, created_at: "2026-11-01T00:00:00Z", updated_at: "2026-11-16T00:00:00Z", scope: "", stage: "未交给 Agent",
  stalled: "", handed_at: null, history: [], waiting: [], run: null,
  next: { key: "reply", name: "待你回", line: "boss 留言", next: "", why: "", who: "", at: "", quote: "", url: "", prs: [], action: "feedback",
    drawer_only: false, button: "帮我回复", bucket: "reply" } };

test("disabled or missing WeChat adds nothing", () => {
  assert.deepEqual(wechatThreads(null), []);
  assert.deepEqual(wechatThreads({ enabled: false }), []);
  assert.equal(freshness({ enabled: false }), "");
});

test("only 私聊待回, 群里 @我 and 待收转账 count toward the badge; the rest sit in 待你看 or 搁置", () => {
  const rows = [...withIssues([], [issue]), ...wechatThreads(report)];
  assert.equal(replyCount(rows), 4);  // issue 一条 + 微信三条
  assert.deepEqual(rows.filter(t => inTab(t, "reply") && t.kind === "wechat").map(t => t.wechat.kind).sort(), ["at_me", "pending_to_me", "private"]);
  assert.deepEqual(rows.filter(t => inTab(t, "look")).map(t => t.key), ["wechat:private:aaaaaa0002", "wechat:at_all:aaaaaa0005"]);
  assert.deepEqual(rows.filter(t => inTab(t, "hold")).map(t => t.key), ["wechat:red_packets:aaaaaa0006"]);
  assert.ok(rows.filter(t => t.kind === "wechat").every(t => handVia(t) === null));  // 不交给 Agent
});

test("the 微信 scope lists only WeChat rows and repo scopes never do", () => {
  const rows = [...withIssues([], [issue]), ...wechatThreads(report)];
  assert.equal(rows.filter(t => inScope(t, "wechat")).length, 6);
  assert.deepEqual(rows.filter(t => inScope(t, "o/r")).map(t => t.key), ["o/r#1"]);
  const side = sideCounts(rows, { tab: "reply", scope: "" });
  assert.equal(side.wechat, 3);
  assert.deepEqual(side.repos.map(r => r.repo), ["o/r"]);  // 微信的行不变成一个空名字的仓库
  assert.equal(sideCounts(rows, { tab: "reply", scope: "wechat" }).tabs.reply, 3);
  assert.equal(parseInboxHash("#/inbox?s=wechat").scope, "wechat");
  assert.equal(inboxHash({ tab: "look", scope: "wechat" }), "#/inbox?f=look&s=wechat");
  assert.equal(parseInboxHash("#/inbox?s=nonsense").scope, "");
});

test("snooze and 已处理 send the row key and its latest message time", () => {
  const [first] = wechatThreads(report);
  assert.equal(canSnooze(first), true);
  assert.deepEqual(snoozeItem(first), { subject: "wechat:private:aaaaaa0001", their_at: "2026-11-18T11:20:00+08:00" });
  assert.deepEqual(doneItem(first.wechat), { key: "wechat:private:aaaaaa0001", ts: 1763435000 });
  const held = wechatThreads(report).at(-1);
  assert.equal(snoozeOf(held), HELD);
  assert.equal(canSnooze(held), false);
  assert.equal(statusLine(held), HELD.line);
  assert.equal(statusLine(first), "样片能先发我吗");
  assert.ok(searchText(first).includes("会话aaaaaa0001") && searchText(first).includes("微信"));
});

test("signal label and freshness line", () => {
  const rows = wechatThreads(report);
  assert.equal(signalText(rows[2].wechat), "群里 @我 ×2");
  assert.equal(signalText(rows[0].wechat), "私聊待回");
  const now = Date.parse("2026-11-18T15:00:00+08:00");
  assert.match(freshness(report, now), /^微信数据更新于 \d{2}:\d{2}$/);
  assert.match(freshness(report, Date.parse("2026-11-20T15:00:00+08:00")), /^微信数据更新于 11-18 \d{2}:\d{2}$/);
  assert.equal(freshness({ enabled: true, running: true }), "正在读取微信数据…");
  assert.equal(freshness({ enabled: true }), "还没有微信数据");
});
