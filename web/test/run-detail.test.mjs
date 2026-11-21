// 运行记录点开一轮的详情：网址、对象链接、结果行、回执各块、全文折叠、失败时的 stderr（node --test 直接加载 .ts）
import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { RunDetailView, askHref, elapsed, outcomeText, parseRunHash, runHash, subjectLinks } from "../src/RunDetail.ts";

const run = (extra = {}) => ({ id: 2079, rule: "处理 issue 留言", agent: "claude-feedback", subjects: ["o/r#200"], status: "完成", state: "done",
  active: false, attempt: 1, started_at: "2026-11-19T02:45:31Z", finished_at: "2026-11-19T02:47:01Z", error: "", format: "stream-json",
  delivered: true, truncated: false, conclusion: "已给 o/r#200 写好回复草稿", body: "- **缓存在哪：** 本库", receipt: null, outcome: null,
  events: [{ source: "GitHub", type: "issues.feedback", subject: "o/r#200", summary: "投资助理接入", link: "", received_at: "", count: 1 }],
  events_error: "", exit_code: 0, stderr: "", folder_missing: false, ...extra });
const html = (data, now) => renderToStaticMarkup(createElement(RunDetailView, { run: data, now }));
const receipt = (extra = {}) => ({ notice: [], ask: [], later: [], resolve: [], events: [], ...extra });

test("run hash round-trips and rejects anything but a run number", () => {
  assert.equal(runHash(2079), "#/runs/2079");
  assert.equal(parseRunHash("#/runs/2079"), 2079);
  for (const bad of ["#/runs", "#/runs/0", "#/runs/../1", "#/runs/12a", "#/runs/1/2"]) assert.equal(parseRunHash(bad), null, bad);
});

test("subject opens the inbox detail and GitHub; PR feedback goes to the repo in the inbox", () => {
  assert.deepEqual(subjectLinks("o/r#5", "处理 issue"), { inbox: "#/i/o/r/5", github: "https://github.com/o/r/issues/5" });
  assert.deepEqual(subjectLinks("o/r#5", "处理 PR 反馈"), { inbox: "#/inbox?f=all&s=o/r", github: "https://github.com/o/r/pull/5" });
  assert.equal(subjectLinks("不是对象", "处理 issue"), null);
});

test("answer link reuses #/q/<card> for 处理 issue and the issue detail otherwise", () => {
  const ask = { title: "t", text: "", options: [], recommend: null, card: 258, open: true, tier: "ask" };
  assert.equal(askHref(ask, { rule: "处理 issue", subjects: ["o/r#1"] }), "#/q/258");
  assert.equal(askHref(ask, { rule: "处理 issue 留言", subjects: ["o/r#200"] }), "#/i/o/r/200");
});

test("result lines are written in Chinese, unknown ones stay as they are", () => {
  assert.equal(outcomeText({ kind: "RESULT", value: "PR=34", line: "RESULT: PR=34" }), "开了 PR #34");
  assert.equal(outcomeText({ kind: "RESULT", value: "REPLIED", line: "" }), "已写回复草稿");
  assert.equal(outcomeText({ kind: "RESULT", value: "BLOCKED=没权限", line: "" }), "卡住：没权限");
  assert.equal(outcomeText({ kind: "VERDICT", value: "APPROVE", line: "" }), "审查通过");
  assert.equal(outcomeText({ kind: "RESULT", value: "SOMETHING", line: "" }), "SOMETHING");
});

test("finished run: title line, conclusion, receipt blocks, folded full text, events in order", () => {
  const out = html(run({ outcome: { kind: "RESULT", value: "REPLIED", line: "RESULT: REPLIED" }, receipt: receipt({
    notice: [{ title: "草稿已写好", text: "等你确认后发出" }],
    ask: [{ title: "要新做查询功能吗", text: "现在没有邮箱", options: ["做", "不做"], recommend: 0, card: 258, open: true, tier: "ask" }],
    later: [{ at: "2026-11-20T01:00:00Z", about: "看 CI 有没有过", key: "ci", cancel: false }],
    resolve: [{ card: 243, reason: "已在 #53 做完" }] }) }));
  assert.match(out, /第 2079 轮/);
  assert.match(out, /<h2>处理 issue 留言<\/h2>/);
  assert.match(out, /href="#\/i\/o\/r\/200">o\/r#200<\/a>/);
  assert.match(out, /https:\/\/github.com\/o\/r\/issues\/200[^>]*>在 GitHub 打开 ↗/);
  assert.match(out, /tag ok">完成/);
  assert.match(out, /用时 1 分 30 秒/);
  assert.match(out, /结果：<strong>已写回复草稿<\/strong><code>RESULT: REPLIED<\/code>/);
  assert.match(out, /草稿已写好/);
  assert.match(out, /推荐/);
  assert.match(out, /等你回答.*href="#\/i\/o\/r\/200">去收件箱回答/);
  assert.match(out, /复查：看 CI 有没有过/);
  assert.match(out, /提醒 #243：已在 #53 做完/);
  assert.match(out, /展开全文/);
  assert.doesNotMatch(out, /缓存在哪/);  // 全文默认折叠
  assert.match(out, /issues\.feedback/);
  assert.doesNotMatch(out, /失败|stderr/);
  const order = ["结论", "回执", "全文", "事件"].map(t => out.indexOf(`<h3>${t}`));
  assert.deepEqual([...order].sort((a, b) => a - b), order);
  assert.ok(order.every(i => i > 0));
  assert.doesNotMatch(out, /卡片|拆卡|仓库主人|维护者/);
});

test("answered asks say so, and a broken receipt shows why", () => {
  const out = html(run({ receipt: receipt({ ask: [{ title: "q", text: "", options: [], recommend: null, card: 9, open: false, tier: "default" }] }) }));
  assert.match(out, /已回答或已关闭/);
  assert.match(out, /已按推荐做/);
  assert.doesNotMatch(out, /去收件箱回答/);
  assert.match(html(run({ receipt: { error: "回执读不懂（不是合法 JSON）" } })), /回执读不懂/);
});

test("running run shows 进行中 and how long it has been running", () => {
  const out = html(run({ status: "进行中", state: "claimed", active: true, finished_at: null, conclusion: "", body: "" }),
    Date.parse("2026-11-19T02:48:31Z"));
  assert.match(out, /tag warn">进行中/);
  assert.match(out, /已运行 3 分 0 秒/);
  assert.match(out, /还在跑/);
  assert.equal(elapsed({ started_at: "2026-11-19T02:45:31Z", finished_at: null, active: true }, Date.parse("2026-11-19T02:45:41Z")), 10);
});

test("merged events show how many times they came", () => {
  const out = html(run({ events: [{ source: "定时", type: "schedule.tick", subject: "", summary: "", link: "", received_at: "", count: 38 }] }));
  assert.match(out, /schedule\.tick × 38/);
  assert.match(out, /<h3>事件<small>38<\/small>/);
});

test("failed run shows the exit code and a folded stderr tail", () => {
  const out = html(run({ status: "没跑成", state: "cancelled", exit_code: 1, stderr: "Traceback\nFileNotFoundError", conclusion: "连续 10 次没跑成" }));
  assert.match(out, /tag bad">没跑成/);
  assert.match(out, /<h3>失败<\/h3>/);
  assert.match(out, /退出码 1/);
  assert.match(out, /展开 stderr 最后 30 行/);
  assert.doesNotMatch(out, /FileNotFoundError/);  // 默认折叠
});

test("spent writes hours and days for long runs", async () => {
  const { spent } = await import("../src/RunParts.ts");
  assert.equal(spent(null), "—");
  assert.equal(spent(45), "45 秒");
  assert.equal(spent(90), "1 分 30 秒");
  assert.equal(spent(375 * 60 + 11), "6 小时 15 分");
  assert.equal(spent(7200), "2 小时");
  assert.equal(spent(26 * 3600 + 120), "1 天 2 小时");
});
