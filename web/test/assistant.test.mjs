// 今日简报和事项两页的判断逻辑：node --test test/
import { test } from "node:test";
import assert from "node:assert/strict";
import { REVIEW_NAME, reviewBars, reviewHandoffNote, reviewHead, reviewMissing, reviewOutput, reviewRange, reviewRunLabel, reviewUrl, runTime } from "../src/assistant.ts";
import { NOVEL_LABEL, briefBody, dayLabel, failureNote, gapNote, handoffNote, learningBrief, learningUrl, needsFill, nextLabel, nextNovelStatus, novelUrl, numberRanges, readmeRest, runLabel, writing } from "../src/assistant.ts";

test("missing or pending next steps are flagged, filled ones are not", () => {
  assert.equal(gapNote({ next_missing: true, next_gaps: [] }), "「下一步」还没写，需补。");
  assert.equal(gapNote({ next_missing: false, next_gaps: ["方向：待补。"] }), "「下一步」有 1 条待补：方向：待补。");
  assert.equal(gapNote({ next_missing: false, next_gaps: ["a", "b"] }), "「下一步」有 2 条待补。");
  assert.equal(gapNote({ next_missing: false, next_gaps: [] }), "");
  assert.equal(needsFill({ next_missing: false, next_gaps: [] }), false);
  assert.equal(needsFill({ next_missing: true, next_gaps: [] }), true);
});

test("generate button says regenerate only when today's brief exists", () => {
  assert.equal(runLabel(null, false), "现在生成");
  assert.equal(runLabel({ dates: [], today: "2026-11-12" }, false), "现在生成");
  assert.equal(runLabel({ dates: ["2026-11-11"], today: "2026-11-12" }, false), "现在生成");
  assert.equal(runLabel({ dates: ["2026-11-12"], today: "2026-11-12" }, false), "重新生成今天的");
  assert.equal(runLabel({ dates: ["2026-11-12"], today: "2026-11-12" }, true), "生成中…");
});

test("dates read as month, day and weekday", () => {
  assert.equal(dayLabel("2026-11-12", "2026-11-12"), "11月12日 周四（今天）");
  assert.equal(dayLabel("2026-11-15"), "11月15日 周日");
});

test("failed sources are named with their reasons", () => {
  assert.equal(failureNote([]), "");
  assert.equal(failureNote([{ source: "今天日程（11-12）", reason: "缺权限" }, { source: "GitHub 未读通知", reason: "没网" }]),
    "这份简报有 2 个来源没读到：今天日程（11-12）（缺权限）；GitHub 未读通知（没网）。");
});

test("article numbers collapse into ranges", () => {
  assert.equal(numberRanges([]), "");
  assert.equal(numberRanges(["06"]), "06");
  assert.equal(numberRanges(["01", "02"]), "01、02");
  assert.equal(numberRanges(["03", "01", "02", "05", "15", "16", "17", "21"]), "01–03、05、15–17、21");
});

test("the brief shows each topic's next step from INDEX.md and the articles still missing feedback", () => {
  const topics = [
    { name: "多巴胺与彻底臣服", next: "从 15 往下读新版，逐篇填反馈", unfilled: ["01", "02", "03", "15", "16", "17"], exists: true },
    { name: "组织学", next: "07 考核与激励（材料从 参考07 取）", unfilled: ["06"], exists: true },
    { name: "全填了", next: "", unfilled: [], exists: true },
    { name: "没目录", next: "先建", unfilled: [], exists: false },
  ];
  assert.deepEqual(learningBrief(topics), [
    { name: "多巴胺与彻底臣服", todo: "从 15 往下读新版，逐篇填反馈；未填反馈 01–03、15–17" },
    { name: "组织学", todo: "07 考核与激励（材料从 参考07 取）；未填反馈 06" },
    { name: "全填了", todo: "" },
  ]);
});

test("expanding a board shows only the README sections the card does not already show", () => {
  const readme = "# 客户项目（示例公司）\n\n一句开场。\n\n## 状态\n\n- 在做\n\n## 下一步（本周）\n\n- 先做\n\n```\n## 状态\n```\n\n## 相关仓库\n\n- a\n\n### 细节\n\n## 记录\n\n- b\n";
  const rest = readmeRest(readme);
  assert.deepEqual(rest.titles, ["相关仓库", "记录"]);
  assert.equal(rest.text, "一句开场。\n\n## 相关仓库\n\n- a\n\n### 细节\n\n## 记录\n\n- b");
  assert.deepEqual(readmeRest("# 只有标题\n\n## 状态\n\n- x\n"), { text: "", titles: [] });
  // 状态段里的代码块照样算在状态段，不漏出来
  assert.equal(readmeRest("## 状态\n```\n## 记录\n```\n## 其他\nok").text, "## 其他\nok");
});

test("the brief body drops the title, generation line and today's date that the date picker already shows", () => {
  const md = "# 每日简报 2026-11-18 星期三\n\n生成时间：2026-11-18 08:30\n\n## 今天日程（2026-11-18 星期三）\n\n- 无日程\n\n## 明天日程（2026-11-19 星期四）\n\n- 无日程\n";
  assert.deepEqual(briefBody(md, "2026-11-18"), {
    text: "## 今天日程\n\n- 无日程\n\n## 明天日程（2026-11-19 星期四）\n\n- 无日程",
    generated: "08:30",
  });
  // 生成时间不是这一天的就整串给出
  assert.equal(briefBody("生成时间：2026-11-13 00:10\n\n正文", "2026-11-12").generated, "2026-11-13 00:10");
  assert.deepEqual(briefBody("## 只有正文", "2026-11-12"), { text: "## 只有正文", generated: "" });
});

test("write-next button and progress line follow the handoff state", () => {
  const topic = state => ({ next_no: "07", handoff: { state, no: state === "idle" ? "" : "07", handed_at: null } });
  assert.equal(nextLabel(topic("idle"), false), "写下一篇（第 07 篇）");
  assert.equal(nextLabel(topic("idle"), true), "交给中…");
  assert.equal(nextLabel(topic("pending"), false), "已交给 Agent…");
  assert.equal(nextLabel(topic("running"), false), "正在写第 07 篇…");
  assert.equal(writing({ state: "pending" }) && writing({ state: "running" }), true);
  assert.equal(writing({ state: "lost" }) || writing({ state: "done" }) || writing({ state: "idle" }), false);
  const now = Date.parse("2026-11-18T12:00:00+08:00");
  assert.equal(handoffNote({ state: "idle", no: "", handed_at: null }), "");
  assert.equal(handoffNote({ state: "pending", no: "07", handed_at: null }), "已交给 Agent，正在派出第 07 篇。");
  assert.match(handoffNote({ state: "lost", no: "07", handed_at: null }), /可以再点一次/);
  assert.equal(handoffNote({ state: "done", no: "07", handed_at: null, finished_at: "2026-11-18T11:00:00+08:00" }, now), "第 07 篇已写好。");
  assert.equal(handoffNote({ state: "done", no: "07", handed_at: null, finished_at: "2026-11-16T11:00:00+08:00" }, now), "");
  assert.equal(handoffNote({ state: "blocked", no: "07", handed_at: null, reason: "没网" }), "第 07 篇没写成：没网。");
});

test("pages open through the read-only learning route", () => {
  assert.equal(learningUrl("组织学", "01.html"), "/learning/%E7%BB%84%E7%BB%87%E5%AD%A6/01.html");
});

test("novel guides open through the read-only novels route, each path segment encoded", () => {
  assert.equal(novelUrl("白鹿原/index.html"), "/novels/%E7%99%BD%E9%B9%BF%E5%8E%9F/index.html");
  assert.equal(novelUrl("选择对照/人物选择对照.html"), `/novels/${encodeURIComponent("选择对照")}/${encodeURIComponent("人物选择对照.html")}`);
});

test("one click moves the reading status 未开始 → 在读 → 读完 → 未开始", () => {
  assert.deepEqual(["unread", "reading", "done"].map(s => `${NOVEL_LABEL[s]}→${NOVEL_LABEL[nextNovelStatus(s)]}`),
    ["未开始→在读", "在读→读完", "读完→未开始"]);
});

// ---- 回顾标签 ----
const period = (extra = {}) => ({ kind: "week", label: "2026-W47", start: "2026-11-16", end: "2026-11-22", summary: "",
  has: { summary: true, top3: true, questions: true, projects: false }, written: { summary: false, top3: false, questions: false, projects: false }, ...extra });

test("a review row shows its summary, or its dates and that the summary is not written yet", () => {
  assert.equal(reviewRange(period()), "11月16日–11月22日");
  assert.equal(reviewRange(period({ kind: "month", start: "2026-10-01", end: "2026-10-31" })), "10月");
  assert.equal(reviewHead(period()), "11月16日–11月22日 · 总结还没写");
  assert.equal(reviewHead(period({ summary: "本人约 20 小时。" })), "本人约 20 小时。");
  assert.equal(reviewUrl("week", "2026-W47"), "/review/week/2026-W47.html");
  assert.deepEqual(REVIEW_NAME, { week: "周回顾", month: "月回顾" });
});

test("the generate button names the period; an existing one is a data update", () => {
  assert.equal(reviewRunLabel("2026-W47", false, false), "现在生成 2026-W47");
  assert.equal(reviewRunLabel("2026-W47", true, false), "更新 2026-W47 的数据");
  assert.equal(reviewRunLabel("2026-11", true, true), "生成中…");
  assert.equal(runTime("2026-11-22T21:00+08:00"), "11月22日 周日 21:00");
  assert.equal(runTime("2026-12-01T09:00+08:00"), "12月1日 周二 09:00");
});

test("only blocks that exist and are still placeholders count as missing", () => {
  assert.deepEqual(reviewMissing(period()), ["summary", "top3", "questions"]);
  assert.deepEqual(reviewMissing(period({ written: { summary: true, top3: true, questions: true, projects: false } })), []);
  assert.deepEqual(reviewMissing(period({ has: { summary: true, top3: false, questions: true, projects: true }, written: { summary: true, top3: false, questions: true, projects: false } })), ["projects"]);
});

test("numbers are shown only when the file had them", () => {
  assert.equal(reviewOutput({}), "");
  assert.equal(reviewOutput({ merged_prs: 18 }), "合并 PR 18 个");
  assert.equal(reviewOutput({ merged_prs: 0, closed_issues: 5 }), "合并 PR 0 个 · 关掉 issue 5 个");
  assert.deepEqual(reviewBars({}), []);
  assert.deepEqual(reviewBars({ categories: [{ name: "甲类", hours: 12, share: 60 }, { name: "乙类", hours: 3, share: null }, { name: "丙类", hours: 0, share: 0 }] }).map(b => [b.name, b.width]),
    [["甲类", 100], ["乙类", 25], ["丙类", 0]]);
});

test("the review handoff note follows the hub run", () => {
  const now = Date.parse("2026-11-20T12:00:00+08:00");
  assert.equal(reviewHandoffNote(undefined), "");
  assert.equal(reviewHandoffNote({ state: "idle", no: "", handed_at: null }), "");
  assert.match(reviewHandoffNote({ state: "running", no: "2026-W47", handed_at: "x" }), /正在写总结和三问/);
  assert.equal(reviewHandoffNote({ state: "done", no: "2026-W47", handed_at: "x", finished_at: "2026-11-20T11:00:00+08:00" }, now), "Agent 已写好。");
  assert.equal(reviewHandoffNote({ state: "done", no: "2026-W47", handed_at: "x", finished_at: "2026-11-18T11:00:00+08:00" }, now), "");
  assert.equal(reviewHandoffNote({ state: "blocked", no: "2026-W47", handed_at: "x", reason: "外接盘没挂载" }), "没写成：外接盘没挂载。");
});
