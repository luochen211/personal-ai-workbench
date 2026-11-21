// 「新需求」抽屉：按钮在不在、一行上的仓库标签、✓ 要不要先展开、仓库下拉、创建前的检查、请求体、状态那一句；草稿不进收件箱的列表
import { test } from "node:test";
import assert from "node:assert/strict";
import { TABS, replyCount, sideCounts } from "../src/InboxParts.ts";
import { NEW_REPO, createBody, createLabel, createdLine, lookFirst, missing, pick, repoChoices, repoNameOk, repoTag, scanBad, scanLine, scanning, showNeeds, targetOf } from "../src/IssueDraftParts.ts";

const draft = (extra = {}) => ({ id: "d0123456789ab", source: { chat: "演示群", kind: "group" }, excerpt: [{ time: "2026-11-20T09:30:00+08:00", from: "甲", text: "编的一句话" }],
  message_at: "2026-11-20T09:30:00+08:00", project_id: "tool", repo: "me/tool", candidates: ["tool"], title: "列表页加导出按钮", body: "正文", labels: [],
  status: "pending", created_at: "2026-11-20T02:00:00Z", error: "", ...extra });
const PROJECTS = [{ id: "a", name: "甲", repo: "me/a" }, { id: "tool", name: "拓客工具", repo: "me/tool" }, { id: "b", name: "乙", repo: "me/b" }];
const scan = (extra = {}) => ({ status: "idle", requested_at: "", finished_at: "", found: 0, saved: 0, error: "", ...extra });
const NEW = { name: "survey-h5", description: "调研问卷" };

test("issue drafts stay out of the inbox list and its badge", () => {
  assert.equal(replyCount([]), 0);
  assert.equal(sideCounts([], { tab: "draft", scope: "" }).tabs.draft, 0);
  assert.equal(TABS.find(t => t.key === "draft").empty, "没有待确认的回复草稿");
});

test("the button shows with WeChat on, or once there are drafts or a scan", () => {
  assert.equal(showNeeds(true, null), true);
  assert.equal(showNeeds(false, null), false);
  assert.equal(showNeeds(false, { drafts: [], scan: scan() }), false);
  assert.equal(showNeeds(false, { drafts: [draft()], scan: scan() }), true);
  assert.equal(showNeeds(false, { drafts: [], scan: scan({ status: "failed" }) }), true);
});

test("row tag: existing repo by short name, new private repo, or not chosen", () => {
  assert.deepEqual(repoTag(targetOf(draft())), { text: "tool", tone: "" });
  assert.deepEqual(repoTag(targetOf(draft({ repo: "", new_repo: NEW }))), { text: "新建 survey-h5 · 私有", tone: "warn" });
  assert.deepEqual(repoTag(targetOf(draft({ repo: "" }))), { text: "未选仓库", tone: "" });
  assert.deepEqual(repoTag({ repo: "", newRepo: { name: "", description: "" } }), { text: "新建 仓库 · 私有", tone: "warn" });
  // 仓库已经建好的草稿（repo 有了）不再算新建
  assert.deepEqual(targetOf(draft({ repo: "me/survey-h5", new_repo: NEW })), { repo: "me/survey-h5", newRepo: null });
});

test("the quick ✓ never creates a repo, an unsorted draft or a failed one without a look", () => {
  assert.equal(lookFirst(draft()), false);
  assert.equal(lookFirst(draft({ repo: "", new_repo: NEW })), true);
  assert.equal(lookFirst(draft({ repo: "" })), true);
  assert.equal(lookFirst(draft({ error: "没有权限" })), true);
});

test("repo choices list candidates first without repeats", () => {
  assert.deepEqual(repoChoices(draft({ project_id: "", candidates: ["b", "gone", "tool"] }), PROJECTS).map(p => p.id), ["b", "tool", "a"]);
  assert.deepEqual(repoChoices(draft({ candidates: [] }), PROJECTS).map(p => p.id), ["tool", "a", "b"]);
});

test("picking in the dropdown switches between an existing repo and a new one", () => {
  const start = targetOf(draft());
  const fresh = pick(NEW_REPO, start);
  assert.deepEqual(fresh, { repo: "", newRepo: { name: "", description: "" } });
  const named = { repo: "", newRepo: NEW };
  assert.deepEqual(pick(NEW_REPO, named), named);  // 已经填的名字留着
  assert.deepEqual(pick("me/a", named), { repo: "me/a", newRepo: null });
  assert.equal(PROJECTS.some(p => p.repo === NEW_REPO), false);
});

test("create needs a target, a title and a body", () => {
  const at = repo => ({ repo, newRepo: null });
  assert.equal(missing("标题", "正文", at("")), "先选仓库");
  assert.equal(missing(" ", "正文", at("me/a")), "标题是空的");
  assert.equal(missing("标题", "\n", at("me/a")), "正文是空的");
  assert.equal(missing("标题", "正文", at("me/a")), "");
  assert.equal(missing("标题", "正文", { repo: "", newRepo: { name: " ", description: "" } }), "先写仓库名");
  assert.equal(missing("标题", "正文", { repo: "", newRepo: { name: "调研 问卷", description: "" } }), "仓库名只能用字母、数字和 . _ -");
  assert.equal(missing("", "正文", { repo: "", newRepo: NEW }), "标题是空的");
  assert.equal(missing("标题", "正文", { repo: "", newRepo: NEW }), "");
});

test("repo names follow the backend rule", () => {
  for (const ok of ["survey-h5", "a", "A_b.c-1", "x".repeat(100)]) assert.equal(repoNameOk(ok), true, ok);
  for (const bad of ["", ".hidden", "a b", "中文", "a/b", "tool.git", "x".repeat(101)]) assert.equal(repoNameOk(bad), false, bad);
});

test("the request carries repo or new_repo, never both", () => {
  assert.deepEqual(createBody("d1", "标题", "正文", { repo: "me/a", newRepo: null }), { id: "d1", title: "标题", body: "正文", repo: "me/a" });
  assert.deepEqual(createBody("d1", "标题", "正文", { repo: "", newRepo: { name: " survey-h5 ", description: " 调研问卷 " } }),
    { id: "d1", title: "标题", body: "正文", new_repo: { name: "survey-h5", description: "调研问卷" } });
});

test("button and result wording say when a repo is created too", () => {
  assert.equal(createLabel({ repo: "me/a", newRepo: null }), "创建 issue");
  assert.equal(createLabel({ repo: "", newRepo: NEW }), "建仓库并创建 issue");
  assert.equal(createLabel({ repo: "", newRepo: NEW }, true), "创建中…");
  assert.equal(createdLine({}), "已创建 · 在收件箱");
  assert.equal(createdLine({ repo_created: true }), "已建仓库并创建 · 在收件箱");
});

test("scan wording: running, done with counts, failed with the reason, stale libraries", () => {
  assert.equal(scanLine(null), "");
  assert.equal(scanLine(scan({})), "");
  assert.equal(scanLine(scan({ status: "running" })), "正在读…");
  assert.equal(scanning(scan({ status: "running" })), true);
  assert.equal(scanning(scan({ status: "done" })), false);
  assert.match(scanLine(scan({ status: "done", found: 7, saved: 2, finished_at: "2026-11-20T02:18:00Z" })), /^读到 7 条，新拟 2 条草稿（/);
  assert.equal(scanLine(scan({ status: "done", found: 9, saved: 0, skipped: 2 })), "读到 9 条，新拟 0 条草稿，2 个话题没拟");
  assert.equal(scanLine(scan({ status: "done", found: 0, saved: 0 })), "读到 0 条，新拟 0 条草稿");
  assert.equal(scanLine(scan({ status: "failed", error: "15 分钟没交回结果" })), "没读成：15 分钟没交回结果");
  const stale = scan({ status: "done", found: 3, saved: 1, refresh_notes: ["wechat2 的库没刷新成，读的是上一次刷新时的记录", ""] });
  assert.equal(scanLine(stale), "读到 3 条，新拟 1 条草稿 · wechat2 的库没刷新成，读的是上一次刷新时的记录");
  assert.equal(scanBad(stale), true);
  assert.equal(scanBad(scan({ status: "done", refresh_notes: [] })), false);
  assert.equal(scanBad(scan({ status: "failed" })), true);
  assert.equal(scanBad(null), false);
});

test("mention after done: one box per speaker, prefilled from remembered accounts", async () => {
  const { mentionOf, mentionLogins, loginOk, sendersOf } = await import("../src/IssueDraftParts.ts");
  const d = draft({ excerpt: [{ time: "", from: "甲", text: "一" }, { time: "", from: "乙", text: "二" }, { time: "", from: "甲", text: "三" }, { time: "", from: "", text: "四" }] });
  assert.deepEqual(sendersOf(d), ["甲", "乙"]);
  assert.deepEqual(mentionOf(d, { 甲: "alice" }), { 甲: "alice", 乙: "" });
  assert.deepEqual(mentionLogins({ 甲: " @alice ", 乙: "", 丙: "alice" }), ["alice"]);
  assert.equal(loginOk("@alice-b"), true);
  assert.equal(loginOk(""), true);
  assert.equal(loginOk("张三"), false);
  assert.equal(loginOk("-bad"), false);
  assert.equal(missing("标题", "正文", targetOf(d), { 甲: "不是账号" }), "做完后 @ 的要填 GitHub 账号名");
  assert.equal(missing("标题", "正文", targetOf(d), { 甲: "alice", 乙: "" }), "");
  assert.deepEqual(createBody("d1", "标题", "正文", targetOf(d), { 甲: "@alice ", 乙: "" }), { id: "d1", title: "标题", body: "正文", repo: "me/tool", mention: { 甲: "alice", 乙: "" } });
  assert.deepEqual(createBody("d1", "标题", "正文", targetOf(d)), { id: "d1", title: "标题", body: "正文", repo: "me/tool" });
});

test("source line: WeChat chats get the prefix, recordings already carry theirs", async () => {
  const { sourceLine } = await import("../src/IssueDraftParts.ts");
  assert.equal(sourceLine({ chat: "演示群", kind: "group" }), "微信 · 演示群");
  assert.equal(sourceLine({ chat: "演示群", kind: "wechat" }), "微信 · 演示群");
  assert.equal(sourceLine({ chat: "得到 · 周会", kind: "getnote" }), "得到 · 周会");
  assert.equal(sourceLine({ chat: "", kind: "audio" }), "录音");
});

test("after an upload: transcribing, or wait for the running round", async () => {
  const { uploadedLine } = await import("../src/IssueDraftParts.ts");
  assert.match(uploadedLine({ queued: false }), /正在本机转写/);
  assert.match(uploadedLine({ queued: true }), /再点一次/);
});
