// 「交给 Agent」按钮的状态：node --test test/
import { test } from "node:test";
import assert from "node:assert/strict";
import { handoffButton, stallNote, waiting } from "../src/handoff.ts";

test("label without any run can be handed again", () => {
  const issue = { stage: "已交给 Agent", stalled: "label_only" };
  assert.deepEqual(handoffButton(issue), { text: "再交给 Agent", disabled: false, primary: true });
  assert.match(stallNote(issue, "agent"), /没有运行记录/);
  assert.equal(waiting(issue), false);
});

test("handoff with no new run after 15 minutes says it may be lost", () => {
  const issue = { stage: "已交给 Agent", stalled: "lost" };
  assert.equal(handoffButton(issue).disabled, false);
  assert.equal(stallNote(issue, "agent"), "交给后超过 15 分钟还没有新的一轮：派发可能丢了，可重试。");
});

test("fresh handoff and running issue stay disabled; others can be handed", () => {
  assert.deepEqual(handoffButton({ stage: "已交给 Agent", stalled: "" }), { text: "已交给", disabled: true, primary: false });
  assert.equal(handoffButton({ stage: "处理中", stalled: "" }).disabled, true);
  assert.equal(handoffButton({ stage: "审查中", stalled: "" }).disabled, true);  // 审查和返工还在 Agent 手里
  assert.equal(handoffButton({ stage: "返工中", stalled: "" }).disabled, true);
  assert.deepEqual(handoffButton({ stage: "未交给 Agent", stalled: "" }), { text: "交给 Agent", disabled: false, primary: true });
  assert.deepEqual(handoffButton({ stage: "已处理", stalled: "" }), { text: "再交给 Agent", disabled: false, primary: false });
  assert.equal(stallNote({ stage: "处理中", stalled: "" }, "agent"), "");
});
