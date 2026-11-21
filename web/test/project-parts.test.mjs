// 项目详情的「我的 PR」区和「已完成」筛选（node --test 直接加载 .ts）
import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { DoneIssueList, PrList, PrSection, closedBy, hideClosed, prStats } from "../src/ProjectParts.ts";

const html = (component, props) => renderToStaticMarkup(createElement(component, props));
const pr = (number, state, extra = {}) => ({ number, title: `PR ${number}`, url: `https://github.com/up/x/pull/${number}`, state, draft: false, review: "",
  checks: "", created_at: "2026-11-11T00:00:00Z", updated_at: "2026-11-12T00:00:00Z", merged_at: null, closed_at: null, ...extra });
const activity = (extra = {}) => ({
  repo: "up/x", available: true, loaded: true, fetched_at: "2026-11-12T15:40:00+08:00", refreshing: false, limited: "", error: "",
  prs: {
    open: [pr(7321, "open", { checks: "PENDING" }), pr(7315, "open", { checks: "FAILURE", review: "CHANGES_REQUESTED" })],
    merged: [pr(7314, "merged", { merged_at: "2026-11-12T07:29:25Z" }), pr(7287, "merged", { merged_at: "2026-11-12T07:29:05Z" })],
    closed: [pr(7155, "closed", { closed_at: "2026-10-31T01:00:00Z" })],
  },
  counts: { pending: 1, open: 2, merged: 2 },
  done_issues: [
    { number: 7307, title: "问题 7307", url: "https://github.com/up/x/issues/7307", author: "luochen211", created_at: null, closed_at: "2026-11-12T07:28:53Z",
      reason: "已完成", by: { kind: "pr", number: 7311, url: "https://github.com/up/x/pull/7311", title: "PR 7311" } },
    { number: 7308, title: "问题 7308", url: "https://github.com/up/x/issues/7308", author: "luochen211", created_at: null, closed_at: "2026-11-12T07:28:57Z",
      reason: "已完成", by: { kind: "pr", number: 7312, url: "https://github.com/up/x/pull/7312", title: "PR 7312", commit: "1af27bb" } },
    { number: 7154, title: "问题 7154", url: "https://github.com/up/x/issues/7154", author: "luochen211", created_at: null, closed_at: "2026-10-31T01:24:06Z",
      reason: "不做", by: { kind: "manual", actor: "luochen211" } },
  ],
  ...extra,
});

test("the PR section lists only open PRs by default and sums up merged and closed ones in one line", () => {
  const out = html(PrSection, { activity: activity(), failed: "" });
  assert.match(out, /id="project-prs"/);
  assert.match(out, /我的 PR <small>5<\/small>/);
  assert.match(out, /开着<small>2<\/small>/);
  assert.match(out, /CI 运行中/);
  assert.match(out, /CI 失败/);
  assert.match(out, /要改/);
  assert.match(out, /读取于/);
  assert.match(out, /近 30 天已合并 2 条 · 已关闭未合并 1 条/);
  assert.match(out, /aria-expanded="false"[^>]*> 展开</);
  assert.doesNotMatch(out, /#7314|#7287|#7155|合并于/);  // 明细收起
  assert.doesNotMatch(out, /卡/);
});

// 在 PrList 返回的元素树里找「展开」按钮
const find = (node, test) => {
  if (!node || typeof node !== "object") return null;
  if (Array.isArray(node)) { for (const n of node) { const f = find(n, test); if (f) return f; } return null; }
  if (test(node)) return node;
  return find(node.props?.children, test);
};

test("clicking expand lists merged and closed PRs linking to GitHub", () => {
  let expanded = false;
  const props = () => ({ activity: activity(), failed: "", expanded, toggle: () => { expanded = !expanded; } });
  const button = find(PrList(props()), n => n.type === "button" && n.props["aria-expanded"] === false);
  assert.ok(button, "展开按钮");
  button.props.onClick();
  assert.equal(expanded, true);
  const out = html(PrList, props());
  assert.match(out, /近 30 天已合并<small>2<\/small>/);
  assert.match(out, /已关闭未合并<small>1<\/small>/);
  assert.match(out, /href="https:\/\/github.com\/up\/x\/pull\/7314" target="_blank"/);
  assert.match(out, /#7287/);
  assert.match(out, /#7155/);
  assert.match(out, /合并于/);
  assert.match(out, /> 收起</);
  // CI 和审查只给开着的 PR
  const merged = out.slice(out.indexOf("近 30 天已合并<small>"));
  assert.doesNotMatch(merged.slice(0, merged.indexOf("已关闭未合并<small>")), /CI |暂无审查结论/);
});

test("nothing merged or closed: the summary line has no expand link", () => {
  const out = html(PrSection, { activity: activity({ prs: { open: [], merged: [], closed: [] } }), failed: "" });
  assert.match(out, /近 30 天已合并 0 条 · 已关闭未合并 0 条/);
  assert.doesNotMatch(out, /展开/);
});

test("the PR section: loading, empty groups, rate limited and failed", () => {
  assert.match(html(PrSection, { activity: null, failed: "" }), /正在读取 PR/);
  const empty = html(PrSection, { activity: activity({ prs: { open: [], merged: [], closed: [] } }), failed: "" });
  assert.match(empty, /没有开着的 PR/);
  const limited = html(PrSection, { activity: activity({ limited: "GitHub 限流中，PR 还没读到，约 3 分钟后自动重试", loaded: false }), failed: "" });
  assert.match(limited, /GitHub 限流中，PR 还没读到/);
  assert.doesNotMatch(limited, /没有开着的 PR/);
  assert.match(html(PrSection, { activity: null, failed: "网络错误" }), /PR 没读到：网络错误/);
  assert.match(html(PrSection, { activity: activity({ refreshing: true }), failed: "" }), /正在更新/);
});

test("done issues show who closed them and link to the issue and the PR", () => {
  const out = html(DoneIssueList, { activity: activity() });
  assert.match(out, /href="https:\/\/github.com\/up\/x\/issues\/7307"/);
  assert.match(out, /href="https:\/\/github.com\/up\/x\/pull\/7311"[^>]*>被 PR #7311 关闭/);
  assert.match(out, /被 PR #7312 关闭（合并提交 1af27bb）/);
  assert.match(out, /luochen211手动关闭/);
  assert.match(out, /不做/);
  assert.match(html(DoneIssueList, { activity: activity({ done_issues: [] }) }), /近 30 天没有/);
  assert.match(html(DoneIssueList, { activity: null }), /正在读取/);
  assert.equal(closedBy({ kind: "commit", oid: "abc1234", url: "" }), "被提交 abc1234 关闭");
});

test("issues closed after the cached list was read leave the open list, reopened ones stay", () => {
  const rows = [
    { repo: "up/x", number: 7307, updated_at: "2026-11-12T06:00:00Z" },  // 列表比关闭早：刚关闭
    { repo: "up/x", number: 7154, updated_at: "2026-11-05T00:00:00Z" },  // 关闭后又重新打开
    { repo: "up/x", number: 7400, updated_at: "2026-11-12T06:00:00Z" },
    { repo: "other/y", number: 7308, updated_at: "2026-11-01T00:00:00Z" },
  ];
  assert.deepEqual(hideClosed(rows, activity()).map(r => r.number), [7154, 7400, 7308]);
  assert.equal(hideClosed(rows, null).length, 4);
});

test("project counts: my PRs waiting for review and merged in the last 30 days", () => {
  assert.deepEqual(prStats(activity()), { pending: 1, merged: 2 });
  assert.equal(prStats(null), null);
  assert.equal(prStats({ repo: "", available: false }), null);
});
