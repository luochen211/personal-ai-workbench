// 收件箱的「近 30 天已完成」列表、去掉重新打开的、open 列表里去掉已关闭的（node --test 直接加载 .ts）
import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AllDoneList, hideDone, reopenedOut } from "../src/ProjectParts.ts";

const html = (component, props) => renderToStaticMarkup(createElement(component, props));
const CW = "codewhale-hq/Codewhale", CO = "career-ops-hq/career-ops";
const item = (repo, number, closed_at, project_id, project_name, pr) => ({
  repo, subject: `${repo}#${number}`, number, title: `问题 ${number}`, url: `https://github.com/${repo}/issues/${number}`,
  author: "luochen211", created_at: null, closed_at, reason: "已完成", project_id, project_name,
  by: { kind: "pr", number: pr, url: `https://github.com/${repo}/pull/${pr}`, title: `PR ${pr}` },
});
const report = (extra = {}) => ({
  since: "2026-10-13", loaded: true, fetched_at: "2026-11-12T16:00:00+08:00", refreshing: false, limited: "", error: "",
  issues: [
    ...[7309, 7308, 7307, 7300, 7299].map((n, i) => item(CW, n, `2026-11-12T07:2${9 - i}:00Z`, "codewhale", "Codewhale", 7314 - i)),
    ...[6931, 6927, 6924, 6923].map((n, i) => item(CO, n, `2026-11-12T05:${32 - i}:00Z`, "career-ops", "career-ops", 6940 - i)),
  ],
  ...extra,
});

test("done issues: reopened ones left out, ones closed after the list was read stay done", () => {
  const open = [{ subject: `${CW}#7299`, updated_at: "2026-11-12T08:00:00Z" },  // 关闭后又重新打开
    { subject: `${CO}#6923`, updated_at: "2026-11-11T00:00:00Z" }];  // 列表是关闭前读的，仍算已完成
  assert.deepEqual(reopenedOut(report().issues, open).map(d => d.number), [7309, 7308, 7307, 7300, 6931, 6927, 6924, 6923]);
  assert.equal(reopenedOut(report().issues, []).length, 9);
});

test("issues known to be closed leave the open list even before the issue cache refreshes", () => {
  const rows = [
    { subject: `${CW}#7307`, updated_at: "2026-11-12T06:00:00Z" },  // 列表比关闭早：刚关闭
    { subject: `${CW}#7299`, updated_at: "2026-11-12T08:00:00Z" },  // 重新打开
    { subject: `${CW}#7400`, updated_at: "2026-11-12T06:00:00Z" },
  ];
  assert.deepEqual(hideDone(rows, report().issues).map(r => r.subject), [`${CW}#7299`, `${CW}#7400`]);
  assert.equal(hideDone(rows, []).length, 3);
});

test("the done list shows project, number, title, closed time and the closing PR", () => {
  const r = report();
  const out = html(AllDoneList, { report: r, items: r.issues, failed: "" });
  for (const n of [7299, 7300, 7307, 7308, 7309, 6923, 6924, 6927, 6931]) assert.match(out, new RegExp(`#${n}<`));
  assert.match(out, /Codewhale · 关闭于/);
  assert.match(out, /career-ops · 关闭于/);
  assert.match(out, /href="https:\/\/github.com\/codewhale-hq\/Codewhale\/pull\/7314"[^>]*>被 PR #7314 关闭/);
  assert.match(out, /读取于/);
  assert.doesNotMatch(out, /卡/);
});

test("the done list: loading, empty, rate limited and failed", () => {
  assert.match(html(AllDoneList, { report: null, items: [], failed: "" }), /正在读取已完成的 issue/);
  assert.match(html(AllDoneList, { report: null, items: [], failed: "网络错误" }), /已完成的 issue 没读到：网络错误/);
  assert.match(html(AllDoneList, { report: report({ issues: [] }), items: [], failed: "" }), /近 30 天没有/);
  const limited = html(AllDoneList, { report: report({ issues: [], loaded: false, limited: "GitHub 限流中，已完成的 issue 还没读到，约 3 分钟后自动重试" }), items: [], failed: "" });
  assert.match(limited, /GitHub 限流中，已完成的 issue 还没读到/);
  const stale = report({ limited: "GitHub 限流中，显示的是 15:40 的数据，约 2 分钟后自动重试" });
  assert.match(html(AllDoneList, { report: stale, items: stale.issues, failed: "" }), /显示的是 15:40 的数据/);
  assert.match(html(AllDoneList, { report: report({ refreshing: true }), items: [], failed: "" }), /正在更新/);
});
