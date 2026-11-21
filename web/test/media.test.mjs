// 自媒体页的显示逻辑（node --test 直接加载 .ts）
import { test } from "node:test";
import assert from "node:assert/strict";
import { count, scheduleLabel, stateLine, totals, weekLabel } from "../src/MediaParts.ts";

test("schedule shows month-day and time in local time, blank when missing", () => {
  const iso = new Date(2026, 10, 19, 18, 5).toISOString();
  assert.equal(scheduleLabel(iso), "11-19 18:05");
  assert.equal(scheduleLabel(""), "");
  assert.equal(scheduleLabel("not a date"), "");
});

test("follower numbers: thousands separators and a signed weekly gain", () => {
  assert.equal(count(16136), "16,136");
  assert.equal(weekLabel({ week: 201 }), "+201");
  assert.equal(weekLabel({ week: 0 }), "0");
  assert.equal(weekLabel({ week: -3 }), "-3");
  assert.equal(weekLabel({ week: null }), "—");
  assert.deepEqual(totals([{ total: 10, week: 2 }, { total: 5, week: null }]), { total: 15, week: 2 });
});

test("one line on top says why the data may not be real, never with a stack", () => {
  assert.equal(stateLine({ state: "down", demo: true, error: "" }), "麦田没在运行");
  assert.equal(stateLine({ state: "ok", demo: true, error: "" }), "现在显示的是麦田演示数据");
  assert.equal(stateLine({ state: "ok", demo: false, error: "" }), "");
  assert.equal(stateLine({ state: "denied", demo: false, error: "麦田口令不对" }), "麦田口令不对");
  assert.equal(stateLine({ state: "error", demo: false, error: "" }), "麦田没读到");
});
