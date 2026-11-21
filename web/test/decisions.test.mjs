// 决定包：一屏答完、问你在前、默认选中推荐项，只有点按钮才送出（node --test 直接加载 .ts）
import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { DecisionPackage, allRecommended, answersOf, noteKeyDown, packageOf } from "../src/Decisions.ts";

const q = (id, tier, recommend = 0, created_at = "2026-11-12T10:00:00Z") =>
  ({ id, kind: "ask", tier, title: `问题 ${id}`, text: "", options: ["甲", "乙"], recommend, created_at });

test("the package holds only questions, asks first", () => {
  const waiting = [q(1, "default"), { ...q(2, ""), kind: "notice" }, q(3, "ask"), q(4, "default", 1, "2026-11-12T09:00:00Z")];
  assert.deepEqual(packageOf(waiting).map(x => x.id), [3, 4, 1]);
});

test("untouched questions answer with the recommendation; a note replaces the option", () => {
  const asks = [q(1, "ask"), q(2, "default", 1), q(3, "ask", null)];
  assert.deepEqual(answersOf(asks, {}), { answers: [{ id: 1, choice: 1 }, { id: 2, choice: 2 }], missing: [asks[2]] });
  const picks = { 1: { choice: 1, text: "" }, 3: { choice: null, text: " 都不要 " } };
  assert.deepEqual(answersOf(asks, picks).answers, [{ id: 1, choice: 2 }, { id: 2, choice: 2 }, { id: 3, text: "都不要" }]);
  assert.equal(allRecommended(asks.slice(0, 2), {}), true);
  assert.equal(allRecommended(asks.slice(0, 2), picks), false);
  assert.equal(allRecommended(asks, {}), false);  // 没写推荐的要人自己选
});

test("renders one package with tiers, recommendation preselected and a send button", () => {
  const out = renderToStaticMarkup(createElement(DecisionPackage, { issue: { repo: "o/r", number: 1, waiting: [q(1, "default"), q(2, "ask", 1)] }, done: () => {} }));
  assert.equal((out.match(/<section class="waiting decisions"/g) || []).length, 1);
  assert.match(out, /要你定的事 2 条（问你 1 条，默认过 1 条）/);
  assert.ok(out.indexOf("问题 2") < out.indexOf("问题 1"));
  assert.match(out, /aria-checked="true" class="primary"[^>]*>乙（推荐）/);
  assert.match(out, /<button type="button" class="primary">全部按推荐送出<\/button>/);
  assert.match(out, /可能分成同一个 PR 上的几轮/);
  assert.doesNotMatch(out, /回车|⏎/);
  const none = renderToStaticMarkup(createElement(DecisionPackage, { issue: { repo: "o/r", number: 1, waiting: [{ ...q(5, ""), kind: "notice" }] }, done: () => {} }));
  assert.equal(none, "");
});

test("nothing can send the package by itself: no autofocus, no form, no submit button", () => {
  // 自动聚焦的送出按钮会被空格或后台刷新重挂载时误触；form 里的输入框按回车会整包提交
  const out = renderToStaticMarkup(createElement(DecisionPackage, { issue: { repo: "o/r", number: 1, waiting: [q(1, "ask"), q(2, "default")] }, done: () => {} }));
  assert.doesNotMatch(out, /autofocus/i);
  assert.doesNotMatch(out, /<form/);
  assert.doesNotMatch(out, /type="submit"/);
  assert.equal((out.match(/class="decision-note"/g) || []).length, 2);
});

test("Enter in a note does not send; it moves to the next note", () => {
  const focused = [];
  const notes = [0, 1, 2].map(i => ({ i, focus: () => focused.push(i) }));
  const box = { querySelectorAll: () => notes };
  for (const note of notes) note.closest = selector => selector === ".decisions" ? box : null;
  const press = (key, target, isComposing = false) => {
    let prevented = false;
    noteKeyDown({ key, nativeEvent: { isComposing }, preventDefault: () => { prevented = true; }, currentTarget: target });
    return prevented;
  };
  assert.equal(press("Enter", notes[0]), true);  // 拦下回车，不交给浏览器
  assert.deepEqual(focused, [1]);
  assert.equal(press("Enter", notes[2]), true);  // 最后一问：拦下，焦点不动
  assert.deepEqual(focused, [1]);
  assert.equal(press("Enter", notes[1], true), false);  // 输入法选词的回车照常
  assert.equal(press("a", notes[1]), false);
  assert.deepEqual(focused, [1]);
});
