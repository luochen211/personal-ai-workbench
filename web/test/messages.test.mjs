// GitHub 消息提醒计数、完成情况和状态标签：node --test test/
import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { StateTag, actionCount, byProgress, isDone, stateLabel } from "../src/messages.ts";

const tag = state => renderToStaticMarkup(createElement(StateTag, { state }));

test("only unread notifications that need the user's action are counted", () => {
  assert.equal(actionCount([]), 0);
  assert.equal(actionCount([{ unread: true, actionable: false }, { unread: true }, { unread: false, actionable: true }]), 0);
  assert.equal(actionCount([{ unread: true, actionable: true }, { unread: true, actionable: false }, { unread: true, actionable: true }]), 2);
});

test("a review request on a merged pull request no longer needs action", () => {
  assert.equal(actionCount([{ unread: true, actionable: true, done: true }, { unread: true, actionable: true, done: false }]), 1);
});

test("review requests on archived projects are not counted", () => {
  assert.equal(actionCount([{ unread: true, actionable: true, classification: "archived" }, { unread: true, actionable: true, classification: "other" }]), 1);
});

test("merged pull requests and completed issues go to the done group, everything else stays open", () => {
  const rows = [
    { id: "pr-merged", state: "merged", done: true },
    { id: "issue-completed", state: "completed", done: true },
    { id: "pr-closed", state: "closed", done: false },
    { id: "pr-open", state: "open", done: false },
    { id: "ci", state: "", done: false },
    { id: "missing", state: "unknown", done: false },
    { id: "old-server" },
  ];
  assert.deepEqual(byProgress(rows, "done").map(r => r.id), ["pr-merged", "issue-completed"]);
  assert.deepEqual(byProgress(rows, "open").map(r => r.id), ["pr-closed", "pr-open", "ci", "missing", "old-server"]);
  assert.equal(rows.filter(isDone).length, 2);
});

test("state tags: merged, completed and closed are labelled; open and non-thread messages are not", () => {
  assert.equal(tag("merged"), '<span class="tag ok">已合并</span>');
  assert.equal(tag("completed"), '<span class="tag ok">已完成</span>');
  assert.equal(tag("closed"), '<span class="tag">已关闭</span>');
  assert.equal(tag("unknown"), '<span class="tag warn">状态未读到</span>');
  assert.equal(tag("open"), "");
  assert.equal(tag(""), "");
  assert.equal(tag(undefined), "");
  assert.equal(stateLabel("open"), null);
});

test("labels never mention cards", () => {
  for (const state of ["merged", "completed", "closed", "unknown"]) assert.doesNotMatch(stateLabel(state)[0], /卡/);
});
