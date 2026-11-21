// 回复草稿：挂到收件箱的行上归「草稿待确认」、计入角标、行上那一句、待你回的按钮叫「帮我回复」、发送结果一句话
import { test } from "node:test";
import assert from "node:assert/strict";
import { SIDE_GROUPS, TABS, attachDrafts, confirmText, handLabels, handVia, inTab, mergeThreads, replyCount, sideCounts, sideGroups, statusLine } from "../src/InboxParts.ts";
import { draftLine, draftMeta, sameEvent, sendConfirmText, sendLine, staleText, targetText, withDrafts } from "../src/DraftParts.ts";
import { handoffNotice } from "../src/AgentParts.ts";

const ME = "luochen211";
const row = (subject, extra = {}) => ({ subject, kind: "pr", repo: subject.split("#")[0], number: Number(subject.split("#")[1]), title: subject,
  url: `https://github.com/${subject.replace("#", "/pull/")}`, project_id: "orders", project_name: "订单", stage: "已处理", stalled: "",
  handed_at: "2026-11-18T01:00:00Z", section: "recent", open: true, pending: false, new: 0, their_at: "2026-11-17T00:00:00Z", mine_at: "", said: [],
  waiting: [], run: null, history: [], unseen: false, by_me: false, author: ME, state: "wait", ...extra });
const draft = (subject, extra = {}) => ({ subject, kind: "pr", created_at: "2026-11-18T02:00:00Z", work: "推送了 abc1234",
  replies: [{ id: "r1", to: "boss", body: "改好了", review_comment_id: null, path: "" }], based_on: { id: "9", at: "2026-11-17T00:00:00Z", who: "boss" }, ...extra });

test("a draft moves its row into 草稿待确认, counts on the badge and gets its own row when the list has none", () => {
  const threads = mergeThreads([], { prs: { prs: [row("up/x#7"), row("up/x#8", { state: "reply", section: "active", pending: true })], login: ME } });
  const rows = attachDrafts(threads, [draft("UP/x#7"), draft("o/r#3", { kind: "issue" })]);
  const byKey = Object.fromEntries(rows.map(t => [t.key, t]));
  assert.equal(byKey["up/x#7"].category, "draft");
  assert.equal(byKey["up/x#7"].draft.subject, "UP/x#7");
  assert.equal(byKey["up/x#7"].updated_at, "2026-11-18T02:00:00Z");
  assert.equal(byKey["o/r#3"].kind, "issue");
  assert.equal(byKey["o/r#3"].url, "https://github.com/o/r/issues/3");
  assert.deepEqual(rows.filter(t => inTab(t, "draft")).map(t => t.key).sort(), ["o/r#3", "up/x#7"]);
  assert.equal(replyCount(rows), 3);  // 待你回 1 条 + 草稿待确认 2 条
  assert.equal(sideCounts(rows, { tab: "draft", scope: "" }).tabs.draft, 2);
  assert.equal(handVia(byKey["up/x#7"]), null);  // 有草稿的不再「帮我回复」
  assert.equal(statusLine(byKey["up/x#7"]), "回复草稿 1 条（给 boss），等你确认发送");
});

test("草稿待确认 sits right after 待你回 in the 要你处理 group", () => {
  assert.equal(TABS[2].key, "draft");
  assert.equal(TABS[2].label, "草稿待确认");
  assert.deepEqual(SIDE_GROUPS[0].keys.slice(0, 2), ["reply", "draft"]);
  assert.equal(sideGroups().flatMap(g => g.keys).filter(k => k === "draft").length, 1);
});

test("待你回 buttons say 帮我回复, 待动手 keeps 交给 Agent", () => {
  assert.deepEqual(handLabels("reply", 2, 5), { some: "帮我回复（勾选 2 条）", all: "全部帮我回复（5 条）", busy: "提交中…", title: "帮我回复", ok: "帮我回复 2 条" });
  assert.equal(handLabels("todo", 2, 5).some, "交给 Agent（勾选 2 条）");
  assert.equal(handLabels("todo", 2, 5).all, "全部交给 Agent（5 条）");
  assert.match(confirmText(12, 10, "请 Agent 帮你回复"), /^一次请 Agent 帮你回复 12 条。/);
  const notice = handoffNotice([{ handed: [{ subject: "a/b#1", kind: "pr", handed_at: "" }], skipped: [], routed: "", error: "" }], true);
  assert.match(notice, /已请 Agent 帮你回复 1 条.*草稿待确认.*你确认后才发/);
  assert.match(handoffNotice([{ handed: [], skipped: [], routed: "", error: "" }]), /^已交给 Agent 0 条/);
});

test("draft wording: where each reply goes, staleness, results", () => {
  assert.equal(targetText({ to: "boss", review_comment_id: 12, path: "a.py" }, "pr"), "回复 boss · 行内评审线程（a.py）");
  assert.equal(targetText({ to: "", review_comment_id: null, path: "" }, "issue"), "回复 · issue 下的评论");
  assert.equal(draftLine({ replies: [{ to: "a" }, { to: "b" }, { to: "a" }], error: "没有权限" }), "回复草稿 3 条（给 a、b），等你确认发送 · 上次发送失败");
  assert.match(draftMeta(draft("a/b#1")), /^生成于 .* · 依据 boss /);
  assert.match(staleText({ stale: 2, newer: [{ who: "pm", at: "2026-11-18T03:00:00Z", text: "等等" }] }), /^对方又回了 2 条，草稿可能过时。最近：pm .*等等/);
  assert.equal(sendLine("a/b#1", { ok: true, sent: 2 }), "a/b#1 已发出 2 条回复");
  assert.equal(sendLine("a/b#1", { ok: false, sent: 0, stale: 1 }), "a/b#1 没发：对方又回了 1 条，草稿可能过时，点开看");
  assert.equal(sendLine("a/b#1", { ok: false, sent: 0, error: "第 1 条发送失败：没有权限，草稿还在" }), "a/b#1 第 1 条发送失败：没有权限，草稿还在");
  assert.match(sendConfirmText(3), /用你的账号发出 3 条.*对方在草稿生成后又回了的先不发/);
});

test("withDrafts keeps rows without drafts as they were", () => {
  const rows = [{ key: "a/b#1" }, { key: "a/b#2" }];
  const out = withDrafts(rows, [draft("a/b#2")], d => ({ key: d.subject }));
  assert.equal(out[0], rows[0]);
  assert.equal(out[1].draft.subject, "a/b#2");
});

test("no copy mentions cards", () => {
  for (const text of [handLabels("reply", 1, 1).all, sendConfirmText(1), draftLine({ replies: [], error: "" })]) assert.doesNotMatch(text, /卡片|拆卡/);
});

test("通知就是草稿依据的那条留言发出的，不再单写通知时间", () => {
  const d = draft("a/b#1");  // based_on.at 2026-11-17T00:00:00Z
  assert.equal(sameEvent(d, "2026-11-17T08:01:00+08:00"), true);
  assert.equal(sameEvent(d, "2026-11-17T00:30:00Z"), false);
  assert.equal(sameEvent(null, "2026-11-17T00:00:00Z"), false);
});
