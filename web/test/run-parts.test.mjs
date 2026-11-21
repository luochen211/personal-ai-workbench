// 「巡检状态」：没事可做的轮次不逐条列，失败的轮次照列（node --test 直接加载 .ts）
import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { WatchView, duration, gateLine, lastRunLine, reasonLine } from "../src/RunParts.ts";

const run = (id, extra = {}) => ({ id, rule: "看带 agent 标签的 issue", agent: "", state: "done", summary: "", error: "", attempts: 1,
  created_at: "2026-11-18T06:20:00Z", claimed_at: null, finished_at: null, subjects: [], seconds: 3, link: "", ...extra });
const status = (extra = {}) => ({ last_run: { id: 9, created_at: "2026-11-18T06:20:00Z", state: "nothing", failed: false },
  dispatched: null, failed: [], failures: 0, notified: false, last_error: "", last_failed: null,
  schedule: "*/10 * * * *", next: "11/18 14:30", notify_after: 3, error: "", ...extra });
const html = data => renderToStaticMarkup(createElement(WatchView, { data, reload: () => {} }));

test("healthy watch shows one line, the next run and the last dispatch, no idle rounds", () => {
  const out = html(status({ dispatched: run(4, { summary: "派出 1 条 issue：o/r#1" }) }));
  assert.match(out, /最近一次运行 \d\d:\d\d，正常|最近一次运行 [^<]+，正常/);
  assert.match(out, /11\/18 14:30/);
  assert.match(out, /下次运行 11\/18 14:30（定时 \*\/10 \* \* \* \*）/);
  assert.match(out, /派出 1 条 issue：o\/r#1/);
  assert.equal((out.match(/class="run-row"/g) ?? []).length, 1);
  assert.doesNotMatch(out, /没事可做|连续失败|失败的轮次/);
  assert.doesNotMatch(out, /卡|仓库主人|维护者|协作者/);
});

test("failed rounds are listed with their reason, and the failure streak shows only when there is one", () => {
  const out = html(status({ failures: 2, last_error: "找不到 claude", last_failed: "2026-11-18T06:10:00Z",
    last_run: { id: 9, created_at: "2026-11-18T06:20:00Z", state: "cancelled", failed: true },
    failed: [{ ...run(9, { state: "cancelled", error: "退出码 1" }), count: 3, first_at: "2026-11-18T05:50:00Z" },
      { ...run(5, { state: "queued", error: "找不到 claude" }), count: 1, first_at: "2026-11-18T05:00:00Z" }] }));
  assert.match(out, /连续失败 2 次（3 次通知一次）；最近一次失败 [^<]+：找不到 claude/);
  assert.match(out, /失败的轮次 <small>2<\/small>/);
  assert.match(out, /原因：退出码 1/);
  assert.match(out, /原因：找不到 claude/);
  assert.match(out, /，没跑成/);
  assert.match(out, /3 轮 · [^<]+ – [^<]+/);  // 同一原因连着失败的并成一行
  assert.equal((out.match(/class="run-row"/g) ?? []).length, 2);
  assert.match(out, /最近没有派出过/);
});

test("last run line names the time only for today and says when nothing ran yet", () => {
  const today = new Date("2026-11-18T08:00:00Z");
  assert.match(lastRunLine({ id: 1, created_at: "2026-11-18T06:20:00Z", state: "nothing", failed: false }, today), /^最近一次运行 \d\d:\d\d，正常$/);
  assert.match(lastRunLine({ id: 1, created_at: "2026-11-10T06:20:00Z", state: "claimed", failed: false }, today), /^最近一次运行 11\/10 \d\d:\d\d，进行中$/);
  assert.equal(lastRunLine(null), "还没有运行过");
});

test("a multi-line error keeps its lead-in and the last line", () => {
  assert.equal(reasonLine("退出码 1：Traceback\n  File x\nFileNotFoundError: projects.toml"), "退出码 1：FileNotFoundError: projects.toml");
  assert.equal(reasonLine("找不到 claude"), "找不到 claude");
  assert.equal(reasonLine(""), "");
});

test("the watch gate line says how often the workbench looks and when it last triggered", () => {
  const gate = { interval: 300, checked_at: "2026-11-18T06:25:00Z", triggered_at: "2026-11-18T06:05:00Z", wake_at: null, error: "" };
  const out = html(status({ schedule: "5 * * * *", gate }));
  assert.match(out, /下次兜底运行 11\/18 14:30（定时 5 \* \* \* \*）/);
  assert.match(out, /工作台每 5 分钟看一次 GitHub，有变化才触发监看 · 最近一次看 [^·]+ · 最近一次触发 [^<·]+</);
  assert.match(gateLine({ ...gate, wake_at: "2026-11-18T07:00:00Z", error: "中台没开" }), /到点复查；上次没能触发：中台没开$/);
  assert.doesNotMatch(html(status()), /工作台每|兜底/);  // 旧接口没有 gate：照旧
});

test("a running round shows how long it has run so far; a finished one shows the total", () => {
  const now = Date.parse("2026-11-20T10:12:30Z");
  assert.equal(duration(run(1, { state: "claimed", claimed_at: "2026-11-20T10:07:00Z", seconds: null }), now), "已运行 5 分 30 秒");
  assert.equal(duration(run(2, { state: "done", claimed_at: "2026-11-20T10:00:00Z", finished_at: "2026-11-20T11:05:00Z", seconds: 3900 }), now), "用时 1 小时 5 分");
  assert.equal(duration(run(3, { state: "queued", seconds: null }), now), "用时 —");
  // 时钟比开始时间早（本机和中台差几秒）时不写负数
  assert.equal(duration(run(4, { state: "claimed", claimed_at: "2026-11-20T10:12:40Z", seconds: null }), now), "已运行 0 秒");
});
