// 「每 60 秒刷新」：默认开启、手动关掉的记忆、弹窗开着或正在输入时跳过、后台暂停（node --test test/）
import { test } from "node:test";
import assert from "node:assert/strict";
import { AUTO_KEY, isTyping, loadAuto, saveAuto, shouldAutoRefresh, startAutoRefresh } from "../src/autorefresh.ts";

const memory = (initial = {}) => {
  const data = { ...initial };
  return { data, getItem: k => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); } };
};
const broken = { getItem() { throw Error("SecurityError"); }, setItem() { throw Error("QuotaExceeded"); } };

test("auto refresh is on unless the user turned it off", () => {
  assert.equal(loadAuto(memory()), true);  // 从没设置过
  assert.equal(loadAuto(null), true);  // 没有 localStorage
  assert.equal(loadAuto(broken), true);  // 读不到（隐私模式等）
  assert.equal(loadAuto(memory({ [AUTO_KEY]: "on" })), true);
  assert.equal(loadAuto(memory({ [AUTO_KEY]: "off" })), false);
});

test("turning it off is remembered for the next visit and storage errors are swallowed", () => {
  const store = memory();
  saveAuto(false, store);
  assert.equal(store.data[AUTO_KEY], "off");
  assert.equal(loadAuto(store), false);
  saveAuto(true, store);
  assert.equal(loadAuto(store), true);
  assert.doesNotThrow(() => saveAuto(false, broken));
});

test("typing means focus is in a text field, not on a checkbox or button", () => {
  assert.equal(isTyping({ tagName: "INPUT", type: "search" }), true);
  assert.equal(isTyping({ tagName: "textarea" }), true);
  assert.equal(isTyping({ tagName: "SELECT" }), true);
  assert.equal(isTyping({ tagName: "INPUT", type: "checkbox" }), false);
  assert.equal(isTyping({ tagName: "BUTTON" }), false);
  assert.equal(isTyping(null), false);
  assert.equal(shouldAutoRefresh({ hidden: false, modalOpen: false, typing: false }), true);
  assert.equal(shouldAutoRefresh({ hidden: false, modalOpen: true, typing: false }), false);
  assert.equal(shouldAutoRefresh({ hidden: false, modalOpen: false, typing: true }), false);
  assert.equal(shouldAutoRefresh({ hidden: true, modalOpen: false, typing: false }), false);
});

function harness() {
  const listeners = new Set();
  const doc = { hidden: false, activeElement: null, addEventListener: (_, fn) => listeners.add(fn), removeEventListener: (_, fn) => listeners.delete(fn) };
  const timers = { fn: null, every: 0, cleared: false, setInterval(fn, ms) { this.fn = fn; this.every = ms; return 1; }, clearInterval() { this.cleared = true; } };
  return { doc, timers, listeners, fire: () => listeners.forEach(fn => fn()) };
}

test("a round is skipped while a modal is open or the user is typing, and runs again after", () => {
  const { doc, timers } = harness();
  let modal = false, runs = 0;
  const stop = startAutoRefresh({ run: () => runs++, modalOpen: () => modal, doc, timers });
  assert.equal(timers.every, 60000);
  timers.fn(); assert.equal(runs, 1);
  modal = true; timers.fn(); assert.equal(runs, 1);  // issue 详情或弹窗开着：跳过
  modal = false; doc.activeElement = { tagName: "INPUT", type: "search" }; timers.fn(); assert.equal(runs, 1);  // 正在搜索：跳过
  doc.activeElement = null; timers.fn(); assert.equal(runs, 2);
  stop(); assert.equal(timers.cleared, true);
});

test("hidden pages pause and coming back refreshes at once", () => {
  const { doc, timers, listeners, fire } = harness();
  let runs = 0;
  const stop = startAutoRefresh({ run: () => runs++, modalOpen: () => false, doc, timers });
  doc.hidden = true; timers.fn(); fire(); assert.equal(runs, 0);  // 在后台：不刷新
  doc.hidden = false; fire(); assert.equal(runs, 1);  // 切回来立即刷新一次
  stop(); assert.equal(listeners.size, 0);
});
