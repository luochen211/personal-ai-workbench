// 待你回的两种交给：帮我回复（只写回复草稿）、处理并回复；每行的建议（只需回复 / 需要处理）；模式写进请求体
import { test } from "node:test";
import assert from "node:assert/strict";
import { MODES, MODE_NAMES, MODE_NOTE, confirmText, handLabels, mergeThreads, replyHint, statusLine, withMode } from "../src/InboxParts.ts";
import { flowNote, nextButtons } from "../src/NextStep.ts";
import { handoffNotice } from "../src/AgentParts.ts";

const ME = "luochen211";
const row = (subject, extra = {}) => ({ subject, kind: "pr", repo: subject.split("#")[0], number: Number(subject.split("#")[1]), title: subject,
  url: `https://github.com/${subject.replace("#", "/pull/")}`, project_id: "", project_name: "", stage: "未交给 Agent", stalled: "",
  handed_at: null, section: "active", open: true, pending: true, new: 1, their_at: "2026-11-18T00:00:00Z", mine_at: "", waiting: [],
  said: [{ who: "boss", what: "评论", at: "2026-11-18T00:00:00Z", text: "这个怎么算？", url: "", path: "", new: true }],
  run: null, history: [], unseen: false, by_me: false, author: ME, state: "reply", ...extra });
const step = (extra = {}) => ({ key: "reply", name: "待你回", line: "boss 留言", next: "", why: "", who: "", at: "", quote: "", url: "", prs: [],
  action: "feedback", drawer_only: false, button: "帮我回复", bucket: "reply", ...extra });

test("待你回 has both buttons for the ticked rows and for all rows", () => {
  assert.deepEqual(MODES, ["reply", "handle"]);
  assert.deepEqual(MODES.map(m => MODE_NAMES[m]), ["帮我回复", "处理并回复"]);
  assert.deepEqual(handLabels("reply", 2, 5, "reply"), { some: "帮我回复（勾选 2 条）", all: "全部帮我回复（5 条）", busy: "提交中…", title: "帮我回复", ok: "帮我回复 2 条" });
  assert.deepEqual(handLabels("reply", 2, 5, "handle"), { some: "处理并回复（勾选 2 条）", all: "全部处理并回复（5 条）", busy: "提交中…", title: "处理并回复", ok: "处理并回复 2 条" });
  assert.equal(handLabels("todo", 2, 5, "handle").some, "交给 Agent（勾选 2 条）");  // 待动手不分模式
  assert.match(confirmText(12, 10, "请 Agent 帮你回复（只写回复草稿）"), /^一次请 Agent 帮你回复（只写回复草稿） 12 条/);
  assert.match(MODE_NOTE, /帮我回复」只写回复草稿，不改代码、不推送、不开或关 PR/);
  for (const text of [MODE_NOTE, ...MODES.map(m => handLabels("reply", 1, 1, m).all)]) assert.doesNotMatch(text, /卡片|拆卡/);
});

test("the mode goes into the request body; 交给 Agent sends none", () => {
  assert.deepEqual(withMode({ items: ["a/b#1"] }, "reply"), { items: ["a/b#1"], mode: "reply" });
  assert.deepEqual(withMode({ prs: ["a/b#2"] }, "handle"), { prs: ["a/b#2"], mode: "handle" });
  assert.deepEqual(withMode({ items: ["a/b#3"] }), { items: ["a/b#3"] });
  assert.deepEqual(withMode({}, "reply"), { mode: "reply" });
});

test("each 待你回 row carries a suggestion, from the backend or a safe default", () => {
  const [asked] = mergeThreads([], { prs: { prs: [row("up/x#7", { hint: { mode: "reply", label: "只需回复", why: "boss 在提问" } })], login: ME } });
  assert.deepEqual(replyHint(asked), { mode: "reply", label: "只需回复", why: "boss 在提问" });
  const [changes] = mergeThreads([], { prs: { prs: [row("up/x#8", { hint: { mode: "handle", label: "", why: "boss 评审要求修改" } })], login: ME } });
  assert.equal(replyHint(changes).label, "需要处理");
  const [old] = mergeThreads([], { prs: { prs: [row("up/x#9")], login: ME } });  // 旧后端没给：判不准算只需回复
  assert.equal(replyHint(old).mode, "reply");
  const flow = { category: "reply", issue: { next: step({ key: "flow", action: "issue" }) } };  // 「处理 issue」等你决定：只回复不适用
  assert.equal(replyHint(flow).mode, "handle");
  const [waiting] = mergeThreads([], { prs: { prs: [row("up/x#10", { state: "wait", section: "recent", pending: false })], login: ME } });
  assert.equal(replyHint(waiting), null);  // 只标在待你回
});

test("a row handed in reply-only mode says so while the Agent works on it", () => {
  const [t] = mergeThreads([], { prs: { prs: [row("up/x#7", { state: "agent", stage: "处理中", mode: "reply" })], login: ME } });
  assert.equal(statusLine(t), "Agent 正在处理（只回复）");
  const [u] = mergeThreads([], { prs: { prs: [row("up/x#8", { state: "agent", stage: "处理中", mode: "handle" })], login: ME } });
  assert.equal(statusLine(u), "Agent 正在处理");
});

test("the issue detail shows both buttons side by side on the issue-comment flow", () => {
  const issue = { stage: "未交给 Agent", stalled: "", next: step(), hint: { mode: "handle", label: "需要处理", why: "" } };
  const two = nextButtons(issue);
  assert.deepEqual(two.map(b => [b.text, b.mode, b.primary]), [["帮我回复", "reply", false], ["处理并回复", "handle", true]]);
  const again = nextButtons({ stage: "等你决定", stalled: "", next: step({ key: "flow", button: "" }) });  // 再交一轮
  assert.deepEqual(again.map(b => [b.text, b.mode]), [["再帮我回复", "reply"], ["再处理并回复", "handle"]]);
  const busy = nextButtons({ stage: "处理中", stalled: "", next: step({ key: "flow", button: "" }) });
  assert.deepEqual(busy.map(b => [b.text, b.disabled, b.mode]), [["已交给", true, undefined]]);
  const todo = nextButtons({ stage: "未交给 Agent", stalled: "", next: step({ key: "todo", action: "issue", button: "交给 Agent", bucket: "todo" }) });
  assert.deepEqual(todo.map(b => [b.text, b.mode]), [["交给 Agent", undefined]]);
  assert.match(flowNote({ next: step(), scope: "" }), /帮我回复」只写回复草稿，不改代码/);
});

test("the notice after handing says which mode went out", () => {
  const handed = [{ handed: [{ subject: "a/b#1", kind: "pr", handed_at: "" }, { subject: "a/b#2", kind: "issue", handed_at: "" }], skipped: [], routed: "", error: "" }];
  assert.match(handoffNotice(handed, "reply"), /^已请 Agent 帮你回复 2 条（PR 1 条，issue 1 条），只写回复草稿、不改代码；.*草稿待确认/);
  assert.match(handoffNotice(handed, "handle"), /^已交给 Agent 处理并回复 2 条（PR 1 条，issue 1 条），要改的改好再写回复草稿；/);
  assert.match(handoffNotice(handed), /^已交给 Agent 2 条/);
});

test("待你回 issues get both buttons in every repo: own and upstream (drafts first, you confirm before sending)", () => {
  for (const [repo, scope] of [["luochen211/shop", ""], ["acme/orders", ""], ["up/x", "mine"]]) {
    const issue = { stage: "未交给 Agent", stalled: "", scope, repo, next: step() };
    const buttons = nextButtons(issue);
    assert.deepEqual(buttons.map(b => [b.text, b.mode]), [["帮我回复", "reply"], ["处理并回复", "handle"]], repo);
    assert.doesNotMatch(flowNote(issue), /去 GitHub 回|没开/);
    assert.match(flowNote(issue), /上游仓库先 fork 到你的账号/);
  }
});
