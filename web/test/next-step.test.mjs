// Issue 页按下一步归类：按钮、走哪条流程的说明、详情里的「下一步」（node --test 直接加载 .ts）
import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { CLASSES, classTone, flowNote, nextButton, nextButtons, switchButton } from "../src/NextStep.ts";

const step = (key, fields = {}) => ({ key, name: Object.fromEntries(CLASSES)[key], line: "", next: "", why: "", who: "", at: "", quote: "",
  url: "", prs: [], action: "", drawer_only: false, button: "", ...fields });
const row = (key, fields = {}, extra = {}) => ({ stage: "未交给 Agent", stalled: "", scope: "", repo: "acme/channel", next: step(key, fields), ...extra });

test("classes come in the agreed order with their names", () => {
  assert.deepEqual(CLASSES.map(([, name]) => name), ["待你回", "待你定", "待动手", "等对方回", "PR 评审中", "待关闭", "待你看", "Agent 看过", "搁置", "记录", "Agent 流程中"]);
  assert.equal(classTone("reply"), "bad");
  assert.equal(classTone("wait"), "wait");
});

test("only todo, reply and look get a button; look only in the drawer", () => {
  assert.deepEqual(nextButton(row("todo", { button: "交给 Agent", action: "issue" })), { text: "交给 Agent", disabled: false, primary: true, drawerOnly: false });
  assert.equal(nextButton(row("reply", { button: "帮我回复", action: "feedback" })).text, "帮我回复");
  assert.deepEqual(nextButton(row("look", { button: "仍交给 Agent", action: "issue", drawer_only: true })), { text: "仍交给 Agent", disabled: false, primary: false, drawerOnly: true });
  for (const key of ["wait", "pr", "close", "reply", "hold", "record"]) assert.equal(nextButton(row(key)), null);
});

test("agent flow keeps the old handed / hand again button", () => {
  assert.deepEqual(nextButton(row("flow", {}, { stage: "处理中" })), { text: "已交给", disabled: true, primary: false, drawerOnly: false });
  assert.equal(nextButton(row("flow", {}, { stage: "等你看" })).text, "再交给 Agent");
  // issue 留言流程（帮我回复）的按钮也叫帮我回复
  assert.equal(nextButton(row("flow", { action: "feedback" }, { stage: "等你看" })).text, "再帮我回复");
  assert.equal(nextButton(row("flow", { action: "feedback" }, { stage: "处理中" })).text, "已交给");
});

test("the note says where the button goes or why there is none", () => {
  assert.match(flowNote(row("todo", { button: "交给 Agent", action: "issue" })), /打 agent 标签/);
  assert.match(flowNote(row("look", { button: "仍交给 Agent", action: "issue" }, { scope: "mine" })), /fork 到你的账号/);
  const pushed = flowNote(row("todo", { button: "交给 Agent", action: "issue" }, { scope: "mine", push: true }));
  assert.match(pushed, /原仓库新开分支/);
  assert.doesNotMatch(pushed, /fork 到你的账号|打 agent 标签/);
  assert.match(flowNote(row("reply", { button: "帮我回复", action: "feedback" })), /处理并回复」先改代码开 PR.*再写回复草稿。$/);
  assert.match(flowNote(row("wait")), /下一步在对方/);
  assert.equal(flowNote(row("flow")), "");
  assert.match(flowNote(row("hold")), /搁置/);
  assert.match(flowNote(row("record")), /记录/);
  for (const key of ["wait", "pr", "close", "reply", "todo", "hold", "record"]) assert.doesNotMatch(flowNote(row(key)), /卡|仓库主人|维护者|协作者/);
});

const DECIDED = { kind: "decide", run: 1969, rule: "处理 issue", flow: "issue", line: "这一版收不收费", since: "2026-11-18T09:30:28Z", finished_at: "2026-11-18T09:31:03Z" };

test("an item the agent already settled only keeps a forced hand-off in the drawer", () => {
  // 待你定（Agent 要你定、之后没有新动静）：列表上没有按钮，详情里一个「仍交给 Agent」，带 force
  const decided = row("decide", { button: "仍交给 Agent", action: "issue", drawer_only: true, settled: DECIDED });
  assert.deepEqual(nextButton(decided), { text: "仍交给 Agent", disabled: false, primary: false, drawerOnly: true, force: true });
  // issue 留言流程的也只有这一个（不拆成帮我回复、处理并回复两个）
  const fed = row("decide", { button: "仍交给 Agent", action: "feedback", drawer_only: true, settled: { ...DECIDED, flow: "feedback" } });
  assert.deepEqual(nextButtons(fed).map(b => [b.text, b.force]), [["仍交给 Agent", true]]);
  assert.match(flowNote(decided), /第 1969 轮的结论是要你定.*仍交给 Agent/);
  const seen = row("seen", { button: "仍交给 Agent", action: "issue", drawer_only: true, settled: { ...DECIDED, kind: "nothing" } });
  assert.match(flowNote(seen), /结论是不用动.*有人回复、评审或提交后会放开/);
  assert.equal(classTone("decide"), "bad");
  assert.equal(classTone("seen"), "look");
});

test("a reply-only round that asked for code changes offers to switch to handle", () => {
  const sw = { run: 2071, at: "2026-11-19T01:59:27Z", line: "这条要改代码，要动手请在工作台用「处理并回复」再交一次。" };
  assert.deepEqual(switchButton({ switch: sw }), { text: "改用处理并回复", disabled: false, primary: true, drawerOnly: true, mode: "handle" });
  assert.equal(switchButton({ switch: sw }, true), null);  // 有草稿时在草稿里出
  assert.equal(switchButton({ switch: null }), null);
});

