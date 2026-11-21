// 详情区小部件：交给 Agent 的说明、关联 PR、找不到的项目（node --test 直接加载 .tsx）
import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { LinkedPrList, MissingProject, NO_NOTE, ReviewLine, RunResultLine, canHandoff, noteFor } from "../src/IssueParts.ts";

const html = (component, props) => renderToStaticMarkup(createElement(component, props));

test("handoff is only offered online", () => {
  assert.equal(canHandoff(true, { scope: "mine" }), true);
  assert.equal(canHandoff(false, { scope: "" }), false);
});

test("linked pull requests show number, title, author, CI and review and link to GitHub", () => {
  const prs = [
    { repo: "up/x", number: 7287, title: "fix hunks", url: "https://github.com/up/x/pull/7287", author: "luochen211", draft: false, review: "", checks: "SUCCESS" },
    { repo: "other/y", number: 3, title: "port", url: "https://github.com/other/y/pull/3", author: "bob", draft: true, review: "CHANGES_REQUESTED", checks: "FAILURE" },
  ];
  const out = html(LinkedPrList, { repo: "up/x", result: { prs, error: "", limited: "" }, failed: "" });
  assert.match(out, /关联 PR<small>2<\/small>/);
  assert.match(out, /href="https:\/\/github.com\/up\/x\/pull\/7287" target="_blank"/);
  assert.match(out, /#7287 fix hunks/);
  assert.match(out, /other\/y#3 port/);
  assert.match(out, /luochen211/);
  assert.match(out, /CI 通过/);
  assert.match(out, /CI 失败/);
  assert.match(out, /要改/);
  assert.match(out, /草稿/);
});

test("linked pull requests: loading, empty, rate limited and failed", () => {
  assert.match(html(LinkedPrList, { repo: "o/r", result: null, failed: "" }), /正在读取/);
  assert.match(html(LinkedPrList, { repo: "o/r", result: { prs: [], error: "", limited: "" }, failed: "" }), /没有引用这条 issue 的 open PR/);
  const limited = html(LinkedPrList, { repo: "o/r", result: { prs: [], error: "", limited: "GitHub 限流中" }, failed: "" });
  assert.match(limited, /GitHub 限流中/);
  assert.doesNotMatch(limited, /没有引用/);
  assert.match(html(LinkedPrList, { repo: "o/r", result: null, failed: "网络错误" }), /关联 PR 没读到：网络错误/);
});

test("a missing project says so and links back to the inbox", () => {
  const out = html(MissingProject, { id: "nonexistent", back: () => {} });
  assert.match(out, /找不到项目「nonexistent」/);
  assert.match(out, /href="#\/inbox"[^>]*>← 回收件箱/);
  assert.match(out, /收件箱只列近月有活动的项目/);
  assert.doesNotMatch(out, /卡/);
});

test("run result line links the PR, explains blocks and never calls a missing result done", () => {
  assert.equal(html(RunResultLine, { result: null }), "");
  const pr = html(RunResultLine, { result: { kind: "pr", pr: 34, url: "https://github.com/o/r/pull/34" } });
  assert.match(pr, /href="https:\/\/github.com\/o\/r\/pull\/34"/);
  assert.match(pr, /#34/);
  assert.match(html(RunResultLine, { result: { kind: "blocked", reason: "推送被拒" } }), /卡住了，推送被拒/);
  assert.match(html(RunResultLine, { result: { kind: "no_change" } }), /没东西可改/);
  const unknown = html(RunResultLine, { result: { kind: "unknown", reason: "回执最后一行没有 RESULT 结果行" } });
  assert.match(unknown, /结果不明/);
  assert.doesNotMatch(unknown, /卡片/);
});

test("review progress reads as one line: reviewing, verdict, rework round, or handed back to the owner", () => {
  const base = { id: 6, state: "done", active: false, verdict: null, current: true, moved: false, sha: "", head: "", hold: "", reworks: 0, max: 3, url: "https://github.com/o/r/pull/34", at: null };
  assert.equal(html(ReviewLine, { review: null }), "");
  assert.match(html(ReviewLine, { review: { ...base, state: "claimed", active: true } }), /审查中（已返工 0 \/ 3 轮）/);
  const approved = html(ReviewLine, { review: { ...base, verdict: "APPROVE" } });
  assert.match(approved, /审查通过/);
  assert.match(approved, /Agent 不合并/);
  assert.match(approved, /href="https:\/\/github.com\/o\/r\/pull\/34"/);
  assert.match(html(ReviewLine, { review: { ...base, verdict: "CHANGES_REQUIRED", reworks: 1 } }), /转返工第 2 轮/);
  assert.match(html(ReviewLine, { review: { ...base, verdict: "CHANGES_REQUIRED", reworks: 3 } }), /不再自动返工，等你处理/);
  assert.match(html(ReviewLine, { review: { ...base, verdict: "WAIT" } }), /约一小时后再审/);
  assert.match(html(ReviewLine, { review: { ...base, verdict: "CHANGES_REQUIRED", current: false, reworks: 1 } }), /有新提交会重新审查/);
  assert.match(html(RunResultLine, { result: { kind: "review", verdict: "CHANGES_REQUIRED" } }), /审查结论：要改/);
});

test("a verdict is tied to the commit it reviewed, and reasons for not reviewing show up", () => {
  const base = { id: 6, state: "done", active: false, verdict: "APPROVE", current: true, moved: false, sha: "aaaaaaaaaaaa", head: "aaaaaaaaaaaa", hold: "", reworks: 0, max: 3, url: "https://github.com/o/r/pull/34", at: null };
  assert.match(html(ReviewLine, { review: base }), /审查通过（审的是 aaaaaaa，/);
  // 审完 PR 又有新提交，或审查发现提交已更新（STALE）：不用旧结论，也不说「约一小时后再审」
  const moved = html(ReviewLine, { review: { ...base, verdict: "CHANGES_REQUIRED", current: false, moved: true, head: "bbbbbbbbbbbb" } });
  assert.match(moved, /提交已更新，等重新审查（上次审的是 aaaaaaa/);
  assert.doesNotMatch(moved, /转返工/);
  const stale = html(ReviewLine, { review: { ...base, verdict: "STALE", current: false, moved: true } });
  assert.match(stale, /提交已更新，等重新审查/);
  assert.doesNotMatch(stale, /约一小时后再审/);
  assert.match(html(ReviewLine, { review: { ...base, id: null, state: "", verdict: null, current: false, sha: "", hold: "CI 没过，未审查" } }), /CI 没过，未审查（已返工 0 \/ 3 轮）/);
  // 审查这一轮被取消：说没跑完，不说「没有结论行」
  const cancelled = html(ReviewLine, { review: { ...base, state: "cancelled", verdict: null, current: false } });
  assert.match(cancelled, /这次审查没跑完/);
  assert.doesNotMatch(cancelled, /没有结论行/);
  assert.match(html(ReviewLine, { review: { ...base, verdict: null } }), /没有结论行/);
  const spent = html(ReviewLine, { review: { ...base, verdict: "CHANGES_REQUIRED", reworks: 1, spent: true } });
  assert.match(spent, /返工第 1 轮结束了却没推新提交，不会再自动返工，等你决定/);
  assert.doesNotMatch(spent, /转返工第 2 轮/);
});

test("a drawer only shows the note about its own issue, never another issue's sent reply", () => {
  const sent = { text: "acme/orders#6 已发出 1 条回复", about: "acme/orders#6" };
  assert.equal(noteFor(sent, "luochen211/shop#200"), "");  // 先前在另一条详情里发的，不串到这条
  assert.equal(noteFor(sent, "ACME/Orders#6"), sent.text);  // 同一条（不分大小写）照常显示
  assert.equal(noteFor({ text: "确认发送 3 条草稿：3 条已发出。", about: "" }, "luochen211/shop#200"), "");  // 列表上的批量提示不进详情
  assert.equal(noteFor(NO_NOTE, "luochen211/shop#200"), "");
});
