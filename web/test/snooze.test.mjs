// 暂时不处理：设了的行落「搁置」、不计角标、不能交给；有草稿的也不留在草稿待确认；行上那一句；只在要你处理的四类里能设
import { test } from "node:test";
import assert from "node:assert/strict";
import { SNOOZABLE, SNOOZE_WAITS, attachDrafts, canSnooze, handVia, inTab, mergeThreads, replyCount, rowLine, snoozeItem, snoozeLabel, snoozeOf,
  withIssues } from "../src/InboxParts.ts";

const ME = "luochen211";
const HELD = { at: "2026-11-18T10:00:00+08:00", until: "", line: "你 11-18 设为暂时不处理", from: "reply" };
const issue = (subject, bucket, extra = {}) => ({ subject, repo: subject.split("#")[0], number: Number(subject.split("#")[1]), project_id: "app",
  project_name: "App", title: subject, url: `https://github.com/${subject.replace("#", "/issues/")}`, labels: [], author: "boss", comments: 1,
  created_at: "2026-11-01T00:00:00Z", updated_at: "2026-11-16T00:00:00Z", scope: "", stage: "未交给 Agent", stalled: "", handed_at: null,
  history: [], waiting: [], run: null, their_at: "2026-11-17T00:00:00Z",
  next: { key: bucket === "hold" ? "reply" : bucket, name: "待你回", line: "boss 11-17 留言", next: "", why: "", who: "", at: "", quote: "", url: "",
    prs: [], action: "feedback", drawer_only: false, button: "帮我回复", bucket }, ...extra });
const pr = (subject, state, extra = {}) => ({ subject, repo: subject.split("#")[0], number: Number(subject.split("#")[1]), title: subject,
  url: `https://github.com/${subject.replace("#", "/pull/")}`, project_id: "", project_name: "", stage: "未交给 Agent", stalled: "", handed_at: null,
  section: state === "reply" ? "active" : "recent", open: true, pending: state === "reply", new: 1, their_at: "2026-11-17T00:00:00Z",
  their_last: "2026-11-17T05:00:00Z", mine_at: "", said: [], waiting: [], run: null, history: [], unseen: false, by_me: false, author: ME, state, ...extra });
const draft = subject => ({ subject, kind: "pr", created_at: "2026-11-18T02:00:00Z", work: "", based_on: null,
  replies: [{ id: "r1", to: "boss", body: "改好了", review_comment_id: null, path: "" }] });

const rows = () => attachDrafts(withIssues(mergeThreads([], { prs: { login: ME, prs: [
  pr("up/x#7", "reply"), pr("up/x#8", "hold", { snooze: { ...HELD, from: "reply" } }), pr("up/x#9", "hold", { snooze: HELD }),
] } }), [issue("o/r#1", "reply"), issue("o/r#2", "hold", { snooze: HELD }), issue("o/r#3", "todo"), issue("o/r#4", "wait")], { login: ME }),
[draft("up/x#9")]);

test("a snoozed row sits in 搁置, off the badge and the handoff, with the line saying when you set it", () => {
  const byKey = Object.fromEntries(rows().map(t => [t.key, t]));
  assert.equal(byKey["o/r#2"].category, "hold");
  assert.equal(byKey["up/x#8"].category, "hold");
  assert.equal(byKey["up/x#9"].category, "hold");  // 有草稿也不留在草稿待确认
  assert.deepEqual(rows().filter(t => inTab(t, "hold")).map(t => t.key).sort(), ["o/r#2", "up/x#8", "up/x#9"]);
  assert.equal(replyCount(rows()), 2);  // 只剩 o/r#1、up/x#7
  for (const key of ["o/r#2", "up/x#8"]) {
    assert.equal(handVia(byKey[key]), null);
    assert.equal(rowLine(byKey[key], "hold"), "你 11-18 设为暂时不处理");
    assert.equal(canSnooze(byKey[key]), false);
  }
  assert.equal(snoozeOf(byKey["o/r#1"]), null);
});

test("只在要你处理的四类里能设，送给后端的是编号和对方最后一条动静", () => {
  assert.deepEqual(SNOOZABLE, ["reply", "draft", "todo", "look"]);
  const byKey = Object.fromEntries(rows().map(t => [t.key, t]));
  assert.equal(canSnooze(byKey["o/r#1"]), true);
  assert.equal(canSnooze(byKey["o/r#3"]), true);
  assert.equal(canSnooze(byKey["up/x#7"]), true);
  assert.equal(canSnooze(byKey["o/r#4"]), false);  // 等对方：不用设
  assert.deepEqual(snoozeItem(byKey["o/r#1"]), { subject: "o/r#1", their_at: "2026-11-17T00:00:00Z" });
  assert.deepEqual(snoozeItem(byKey["up/x#7"]), { subject: "up/x#7", their_at: "2026-11-17T05:00:00Z" });  // PR 算上别人的提交和评审
  const withDraft = attachDrafts(withIssues(mergeThreads([], { prs: { login: ME, prs: [pr("up/x#5", "wait")] } }), [], { login: ME }), [draft("up/x#5")]);
  assert.equal(withDraft[0].category, "draft");
  assert.equal(canSnooze(withDraft[0]), true);  // 草稿待确认的也能设
});

test("按钮和选项的字", () => {
  assert.equal(snoozeLabel(3), "暂时不处理（勾选 3 条）");
  assert.deepEqual(SNOOZE_WAITS.map(w => w.label), ["有新动静再说", "1 天后", "1 周后"]);
  assert.equal(SNOOZE_WAITS[0].key, "news");
  for (const text of [snoozeLabel(1), ...SNOOZE_WAITS.map(w => w.label), HELD.line]) assert.doesNotMatch(text, /卡片|拆卡/);
});
