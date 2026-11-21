// 收件箱顶部的 Agent 计数、10 秒刷新、结果写法、成批交给的提示（node --test 直接加载 .ts）
import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { FeedResultLine, agentLine, agentsLine, batches, feedResultText, handoffNotice, polling, running } from "../src/AgentParts.ts";

const agents = (extra = {}) => ({ running: 0, queued: 0, unknown: 0, agents: [], parallel: 10, capacity: 10, limit: 10, error: "", ...extra });

test("the page polls every 10 seconds while agents run or queue", () => {
  assert.equal(polling(null), false);
  assert.equal(polling(agents()), false);
  assert.equal(polling(agents({ running: 1 })), true);
  assert.equal(polling(agents({ queued: 2 })), true);
  assert.equal(polling(agents({ unknown: 1 })), false);  // 没有回音的不算在跑
});

test("the header says how many agents run, queue, and the cap", () => {
  assert.equal(agentsLine(agents({ running: 3, queued: 2 })), "3 个 Agent 在跑 · 2 个排队 · 最多同时 10 个");
  assert.equal(agentsLine(agents({ running: 2, queued: 5, capacity: 2, limit: 2 })), "2 个 Agent 在跑 · 5 个排队 · 最多同时 2 个（接单程序容量 2，规则上限 10）");
  assert.equal(agentsLine(agents({ unknown: 1, capacity: null })), "0 个 Agent 在跑 · 0 个排队 · 1 个没有回音 · 最多同时 10 个");
  const list = [{ id: 1, rule: "处理 issue 留言", subject: "a/x#1", started_at: "2026-11-17T12:00:00Z", state: "running" },
    { id: 2, rule: "处理 PR 反馈", subject: "a/x#2", started_at: "2026-11-17T12:01:00Z", state: "queued" }];
  assert.deepEqual(running(agents({ agents: list })).map(a => a.id), [1]);
  assert.equal(agentLine(list[0], () => "20:00"), "处理 issue 留言 · a/x#1 · 20:00 开始");
});

test("results read as one line and opened PRs are linked", () => {
  assert.deepEqual(feedResultText({ kind: "pr", prs: [34, 35] }), ["已写回复，开了 PR #34、#35", "ok"]);
  assert.deepEqual(feedResultText({ kind: "replied" }), ["已写回复", "ok"]);
  assert.deepEqual(feedResultText({ kind: "blocked", reason: "等你定" }), ["卡住：等你定", "bad"]);
  assert.equal(feedResultText(null), null);
  const out = renderToStaticMarkup(createElement(FeedResultLine, { result: { kind: "pr", prs: [34, 35], urls: ["https://github.com/a/x/pull/34", "https://github.com/a/x/pull/35"] } }));
  assert.match(out, /href="https:\/\/github.com\/a\/x\/pull\/34"[^>]*>#34 ↗/);
  assert.match(out, /href="https:\/\/github.com\/a\/x\/pull\/35"/);
  const pushed = renderToStaticMarkup(createElement(FeedResultLine, { result: { kind: "pushed", commit: "abc1234", url: "https://github.com/a/x/pull/7/commits/abc1234" } }));
  assert.match(pushed, /已推送 abc1234/);
});

test("a big selection goes out in batches of 40 and the notice adds them up", () => {
  const subjects = Array.from({ length: 85 }, (_, i) => `a/x#${i + 1}`);
  assert.deepEqual(batches(subjects).map(b => b.length), [40, 40, 5]);
  const notice = handoffNotice([
    { handed: [{ subject: "a/x#1", kind: "pr" }, { subject: "a/x#2", kind: "issue" }], skipped: [], routed: "", error: "" },
    { handed: [{ subject: "a/x#3", kind: "issue" }], skipped: [{ subject: "a/x#4", reason: "issue 已经关闭" }], routed: "", error: "" }]);
  assert.equal(notice, "已交给 Agent 3 条（PR 1 条，issue 2 条），进度每 10 秒更新。跳过 1 条：a/x#4 issue 已经关闭。");
});
