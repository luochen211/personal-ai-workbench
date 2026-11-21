// 「PR 待处理」：能不能勾、全选、进度跟踪、结果写法、导航数（node --test 直接加载 .ts）
import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { PrResultLine, following, handoffNotice, keepChosen, pendingCount, resultText, roleTag, saidLine, sections, selectable, stallText, toggleAll } from "../src/PrParts.ts";

const row = (subject, extra = {}) => ({ subject, stage: "未交给 Agent", stalled: "", handed_at: null, section: "active", pending: true, ...extra });

test("rows on their way cannot be ticked; lost handoffs and finished ones can", () => {
  assert.equal(selectable(row("a")), true);
  assert.equal(selectable(row("a", { stage: "已交给 Agent" })), false);
  assert.equal(selectable(row("a", { stage: "处理中" })), false);
  assert.equal(selectable(row("a", { stage: "已交给 Agent", stalled: "lost" })), true);
  assert.equal(selectable(row("a", { stage: "已处理" })), true);
  assert.match(stallText(row("a", { stage: "已交给 Agent", stalled: "lost" })), /派发可能丢了/);
});

test("select all toggles only tickable rows, and refresh drops rows that became busy", () => {
  const rows = [row("a"), row("b", { stage: "处理中" }), row("c", { stage: "等你决定" })];
  const all = toggleAll(new Set(), rows);
  assert.deepEqual([...all], ["a", "c"]);
  assert.deepEqual([...toggleAll(all, rows)], []);
  assert.deepEqual([...keepChosen(new Set(["a", "c"]), [row("a", { stage: "已交给 Agent" }), row("c")])], ["c"]);
});

test("progress is followed every 10 seconds for an hour after handing off", () => {
  const now = Date.parse("2026-11-13T12:00:00Z");
  assert.equal(following(row("a", { stage: "已交给 Agent", handed_at: "2026-11-13T11:30:00Z" }), now), true);
  assert.equal(following(row("a", { stage: "处理中", handed_at: "2026-11-13T10:30:00Z" }), now), false);
  assert.equal(following(row("a", { stage: "已处理", handed_at: "2026-11-13T11:59:00Z" }), now), false);
});

test("results read as one line", () => {
  assert.deepEqual(resultText({ kind: "pushed", commit: "abc1234" }), ["已推送 abc1234", "ok"]);
  assert.deepEqual(resultText({ kind: "closed" }), ["已关闭", "ok"]);
  assert.deepEqual(resultText({ kind: "replied" }), ["已写回复", "ok"]);
  assert.deepEqual(resultText({ kind: "no_action", reason: "维护者说他们来改" }), ["不用动：维护者说他们来改", "ok"]);
  assert.deepEqual(resultText({ kind: "blocked", reason: "推送被拒" }), ["卡住：推送被拒", "bad"]);
  assert.equal(resultText({ kind: "unknown", reason: "" })[1], "warn");
  assert.equal(resultText(null), null);
  const out = renderToStaticMarkup(createElement(PrResultLine, { result: { kind: "pushed", commit: "abc1234", url: "https://github.com/up/x/pull/7/commits/abc1234" } }));
  assert.match(out, /href="https:\/\/github.com\/up\/x\/pull\/7\/commits\/abc1234"/);
  assert.match(out, /已推送 abc1234/);
});

test("nav count, sections and notices", () => {
  const report = { prs: [row("a"), row("b", { stage: "处理中" }), row("c", { section: "recent", pending: false, stage: "已处理" })] };
  assert.equal(pendingCount(report), 1);
  assert.equal(pendingCount(null), 0);
  const { active, recent } = sections(report.prs);
  assert.deepEqual(active.map(r => r.subject), ["a", "b"]);
  assert.deepEqual(recent.map(r => r.subject), ["c"]);
  assert.equal(saidLine({ who: "m", what: "行内意见", path: "a.py", text: "typo" }), "m 行内意见（a.py）：typo");
  assert.equal(roleTag({ maintainer: true, role: "仓库主人" }), null);
  assert.deepEqual(roleTag({ maintainer: false, role: "路人" }), ["非维护者", "warn"]);
  const notice = handoffNotice({ handed: [{ subject: "up/x#7" }], skipped: [{ subject: "up/x#9", reason: "不是你开的 PR" }], routed: "", error: "" });
  assert.equal(notice, "已交给 Agent 1 条 PR，处理进度每 10 秒更新。跳过 1 条：up/x#9 不是你开的 PR。");
});
