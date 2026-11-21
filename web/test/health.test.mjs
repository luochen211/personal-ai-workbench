// 「健康」标签的换算和图形几何（node --test 直接加载 .ts）；数据是编的
import { test } from "node:test";
import assert from "node:assert/strict";
import { BOX, analysisNote, bedChart, clock, dayTicks, lineChart, readout, recent, sleepChart, slots, span, stepsChart, target, tiles } from "../src/HealthParts.ts";

const TARGETS = { keep: 420, aim: 480 };
const day = (extra = {}) => ({ date: "2026-03-03", sleep_min: 480, deep_min: 100, light_min: 300, rem_min: 80, awake_min: 10, bed: -30, wake: 460,
  hr_avg: 64, hr_resting: 52, hr_estimated: true, steps: 6000, partial: false, ...extra });
const none = day({ date: "2026-03-02", sleep_min: null, deep_min: null, light_min: null, rem_min: null, awake_min: null, bed: null, wake: null, steps: 30, partial: true });

test("clock wraps around midnight and durations read as hours and minutes", () => {
  assert.equal(clock(-30), "23:30");
  assert.equal(clock(0), "00:00");
  assert.equal(clock(506), "08:26");
  assert.equal(span(494), "8 小时 14 分");
  assert.equal(span(480), "8 小时 00 分");
  assert.equal(span(50), "50 分");
});

test("a night is judged against 保 7 争 8", () => {
  assert.deepEqual(target(480, TARGETS), { text: "争 8 达成", tone: "ok" });
  assert.deepEqual(target(479, TARGETS), { text: "保 7 达成", tone: "warn" });
  assert.deepEqual(target(419, TARGETS), { text: "不足 7 小时", tone: "bad" });
});

test("the analysis says when it was written and warns when the data is newer", () => {
  assert.deepEqual(analysisNote({ through: "2026-03-09", written: "2026-03-10", outdated: false }, "2026-03-09"), { text: "AI 解读 · 2026-03-10 写，按截至 2026-03-09 的数据", warn: false });
  assert.deepEqual(analysisNote({ through: "", written: "", outdated: false }, "2026-03-09"), { text: "AI 解读", warn: false });
  assert.deepEqual(analysisNote({ through: "2026-03-05", written: "", outdated: true }, "2026-03-09"),
    { text: "数据已经更新到 2026-03-09，这份分析还是按截至 2026-03-05 的数据写的，需要重写。", warn: true });
});

test("tiles show only the numbers that exist", () => {
  const summary = { nights: 7, sleep_avg: 456, aim: 4, keep: 2, short: 1, bed_avg: -10, wake_avg: 460, bed_spread: 120, late: 3, deep_share: 20, rem_share: 18,
    resting_avg: 52, resting_low: 50, resting_high: 54, resting_estimated: true, steps_avg: 6000, steps_days: 6 };
  assert.deepEqual(tiles(summary), [
    { label: "平均睡眠", value: "7 小时 36 分", note: "7 晚 · 4 晚达到 8 小时" },
    { label: "平均入睡", value: "23:50", note: "平均 07:40 醒来" },
    { label: "静息心率（估算）", value: "52 次/分", note: "50–54" },
    { label: "日均步数", value: "6,000", note: "6 个完整天" },
  ]);
  assert.deepEqual(tiles({ ...summary, sleep_avg: null, bed_avg: null, resting_avg: null, steps_avg: null }), []);
  assert.deepEqual(tiles(null), []);
});

test("the hover line names the day's sleep, heart rate and steps", () => {
  assert.equal(readout(day(), TARGETS), "03-03 · 睡 8 小时 00 分（23:30–07:40），争 8 达成 · 深睡 1 小时 40 分、浅睡 5 小时 00 分、REM 1 小时 20 分 · 日均心率 64，静息 52（估算） · 6,000 步");
  assert.equal(readout(none, TARGETS), "03-02 · 没有睡眠记录 · 日均心率 64，静息 52（估算） · 30 步（不是整天）");
});

test("columns share one grid: equal bands inside the plot, bars centred and capped", () => {
  const cols = slots(4);
  assert.equal(cols[0].left, BOX.left);
  assert.equal(cols[3].left + cols[3].band, BOX.w - BOX.right);
  assert.equal(cols[0].w, 26);
  assert.equal(cols[1].cx, cols[1].x + cols[1].w / 2);
  assert.ok(slots(200)[0].w >= 2);
  assert.deepEqual(dayTicks(5), [0, 1, 2, 3, 4]);
  assert.deepEqual(dayTicks(30), [2, 5, 8, 11, 14, 17, 20, 23, 26, 29]);
  assert.equal(recent(Array.from({ length: 40 }, (_, i) => day({ date: String(i) }))).length, 30);
});

test("sleep bars stack deep, light and REM from the baseline with a gap between segments", () => {
  const chart = sleepChart([none, day()], TARGETS);
  const base = BOX.h - BOX.bottom;
  assert.deepEqual(chart.bars[0].parts, []);
  const [deep, light, rem] = chart.bars[1].parts;
  assert.deepEqual([deep.key, light.key, rem.key], ["deep", "light", "rem"]);
  assert.ok(Math.abs(deep.y + deep.h - base) < 0.2);           // 最下面一段贴着基线
  assert.ok(Math.abs(light.y + light.h + 2 - deep.y) < 0.2);   // 段与段之间 2px 缝
  assert.ok(rem.y < light.y && light.y < deep.y);
  assert.deepEqual(chart.ticks.map(t => t.label), ["0h", "2h", "4h", "6h", "8h"]);  // 纵轴到 9 小时，每 2 小时一格
  assert.deepEqual(chart.lines.map(l => l.label), ["7 小时", "8 小时"]);
  assert.ok(chart.lines[1].y < chart.lines[0].y);
  assert.deepEqual(sleepChart([day({ deep_min: null })], TARGETS).bars[0].parts.map(p => p.key), ["all"]);
});

test("bedtime bars run from falling asleep down to waking, evening at the top", () => {
  const chart = bedChart([none, day(), day({ date: "2026-03-04", bed: 60, wake: 490 })]);
  assert.equal(chart.bars[0], null);
  assert.ok(chart.bars[1].y < chart.bars[2].y);  // 23:30 入睡画在 01:00 上面
  assert.deepEqual(chart.ticks.map(t => t.label), ["23:00", "01:00", "03:00", "05:00", "07:00", "09:00"]);
  assert.deepEqual(bedChart([none]), { bars: [null], ticks: [] });
});

test("heart-rate lines share one axis and break where a day has no value", () => {
  const chart = lineChart([{ key: "avg", values: [60, null, 66] }, { key: "resting", values: [50, 52, 51] }]);
  assert.match(chart.lines[0].path, /^M[\d.]+ [\d.]+M[\d.]+ [\d.]+$/);
  assert.match(chart.lines[1].path, /^M[\d.]+ [\d.]+L[\d.]+ [\d.]+L[\d.]+ [\d.]+$/);
  assert.ok(chart.lines[0].points[2].y < chart.lines[1].points[2].y);  // 66 在 51 上面
  assert.deepEqual(chart.ticks.map(t => t.label), ["45", "55", "65"]);  // 45–70，每 10 一格
  assert.deepEqual(lineChart([{ key: "avg", values: [null] }]), { lines: [], ticks: [] });
});

test("step bars start at zero and mark days that are not whole", () => {
  const chart = stepsChart([none, day(), day({ steps: null })]);
  assert.equal(chart.bars[0].partial, true);
  assert.equal(chart.bars[1].partial, false);
  assert.equal(chart.bars[2], null);
  assert.ok(Math.abs(chart.bars[1].y + chart.bars[1].h - (BOX.h - BOX.bottom)) < 0.2);
  assert.deepEqual(chart.ticks.map(t => t.label), ["0", "2k", "4k", "6k"]);
});
