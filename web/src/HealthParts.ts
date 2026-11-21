// 「健康」标签的换算和图形几何：不写 JSX，node --test 可以直接加载这个文件。
import type { HealthDay, HealthSummary } from "./api";

/** 相对 0 点的分钟数写成 HH:MM（负数是前一天晚上，-30 是 23:30）。 */
export function clock(minutes: number): string {
  const total = ((Math.round(minutes) % 1440) + 1440) % 1440;
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}
/** 分钟数写成「8 小时 14 分」；不足一小时写「50 分」。 */
export function span(minutes: number): string {
  const total = Math.round(minutes);
  return total >= 60 ? `${Math.floor(total / 60)} 小时 ${String(total % 60).padStart(2, "0")} 分` : `${total} 分`;
}
export const shortDay = (iso: string) => iso.slice(5);
export const CHART_DAYS = 30;  // 图里最多画最近这么多天
export const recent = (days: HealthDay[]) => days.slice(-CHART_DAYS);

/** 对照「保 7 争 8」的一句话。 */
export function target(minutes: number, targets: { keep: number; aim: number }): { text: string; tone: "ok" | "warn" | "bad" } {
  if (minutes >= targets.aim) return { text: "争 8 达成", tone: "ok" };
  if (minutes >= targets.keep) return { text: "保 7 达成", tone: "warn" };
  return { text: "不足 7 小时", tone: "bad" };
}

/** 分析那一块标题旁的说明：什么时候写的、按哪天为止的数据；数据比分析新时说明白。 */
export function analysisNote(a: { through: string; written: string; outdated: boolean }, lastDate: string): { text: string; warn: boolean } {
  const basis = [a.written && `${a.written} 写`, a.through && `按截至 ${a.through} 的数据`].filter(Boolean).join("，");
  if (a.outdated) return { text: `数据已经更新到 ${lastDate}，这份分析还是${a.through ? `按截至 ${a.through} 的数据` : "按旧数据"}写的，需要重写。`, warn: true };
  return { text: basis ? `AI 解读 · ${basis}` : "AI 解读", warn: false };
}

export interface Tile { label: string; value: string; note: string }
/** 页面顶部四个数：平均睡眠、平均入睡、静息心率、日均步数；没有的数不出。 */
export function tiles(s: HealthSummary | null): Tile[] {
  if (!s) return [];
  const out: Tile[] = [];
  if (s.sleep_avg !== null) out.push({ label: "平均睡眠", value: span(s.sleep_avg), note: `${s.nights} 晚 · ${s.aim} 晚达到 8 小时` });
  if (s.bed_avg !== null && s.wake_avg !== null) out.push({ label: "平均入睡", value: clock(s.bed_avg), note: `平均 ${clock(s.wake_avg)} 醒来` });
  if (s.resting_avg !== null) out.push({ label: s.resting_estimated ? "静息心率（估算）" : "静息心率", value: `${s.resting_avg} 次/分`, note: `${s.resting_low}–${s.resting_high}` });
  if (s.steps_avg !== null) out.push({ label: "日均步数", value: s.steps_avg.toLocaleString("en-US"), note: `${s.steps_days} 个完整天` });
  return out;
}

/** 鼠标停在某一天时图上方那一行字。 */
export function readout(d: HealthDay, targets: { keep: number; aim: number }): string {
  const parts = [shortDay(d.date)];
  if (d.sleep_min !== null) {
    parts.push(`睡 ${span(d.sleep_min)}${d.bed !== null && d.wake !== null ? `（${clock(d.bed)}–${clock(d.wake)}）` : ""}，${target(d.sleep_min, targets).text}`);
    if (d.deep_min !== null && d.light_min !== null && d.rem_min !== null) parts.push(`深睡 ${span(d.deep_min)}、浅睡 ${span(d.light_min)}、REM ${span(d.rem_min)}`);
  } else parts.push("没有睡眠记录");
  if (d.hr_avg !== null) parts.push(`日均心率 ${d.hr_avg}${d.hr_resting !== null ? `，静息 ${d.hr_resting}${d.hr_estimated ? "（估算）" : ""}` : ""}`);
  if (d.steps !== null) parts.push(`${d.steps.toLocaleString("en-US")} 步${d.partial ? "（不是整天）" : ""}`);
  return parts.join(" · ");
}

// ---- 图形几何：所有图同一个画布、同一组横向格子，日期上下对齐 ----
export const BOX = { w: 720, h: 200, left: 44, right: 48, top: 10, bottom: 22 };
const plotW = BOX.w - BOX.left - BOX.right;
const plotBottom = BOX.h - BOX.bottom;
const GAP = 2;  // 相邻色块之间留的底色缝

export interface Slot { x: number; w: number; cx: number; left: number; band: number }
/** 每天一格：band 是整格（悬停用），x / w 是格里居中的柱子（最宽 26）。 */
export function slots(count: number): Slot[] {
  const band = plotW / Math.max(count, 1);
  const w = Math.max(2, Math.min(26, band * 0.62));
  return Array.from({ length: count }, (_, i) => {
    const left = BOX.left + i * band;
    return { left, band, cx: left + band / 2, x: left + (band - w) / 2, w };
  });
}
/** 横轴上写日期的那些格子：天数多时隔几天写一个，最后一天总写。 */
export function dayTicks(count: number): number[] {
  const step = Math.max(1, Math.ceil(count / 12));
  const out: number[] = [];
  for (let i = count - 1; i >= 0; i -= step) out.unshift(i);
  return out;
}
const scale = (lo: number, hi: number) => (v: number) => plotBottom - (v - lo) / (hi - lo || 1) * (plotBottom - BOX.top);
const round1 = (v: number) => Math.round(v * 10) / 10;
export interface Tick { y: number; label: string }

export interface SleepBar { date: string; parts: { key: "deep" | "light" | "rem" | "all"; y: number; h: number }[] }
/** 每晚一根堆叠柱（深睡在下、浅睡、REM 在上；没有分期的只画总时长）；纵轴从 0 起，到整小时。 */
export function sleepChart(days: HealthDay[], targets: { keep: number; aim: number }): { bars: SleepBar[]; ticks: Tick[]; lines: (Tick & { key: string })[] } {
  const top = Math.max(targets.aim + 60, ...days.map(d => d.sleep_min ?? 0));
  const hi = Math.ceil(top / 60) * 60;
  const y = scale(0, hi);
  const bars = days.map(d => {
    const staged = d.deep_min !== null && d.light_min !== null && d.rem_min !== null;
    const stack: [SleepBar["parts"][number]["key"], number][] = !d.sleep_min ? []
      : staged ? [["deep", d.deep_min!], ["light", d.light_min!], ["rem", d.rem_min!]] : [["all", d.sleep_min]];
    let base = 0;
    const parts = stack.filter(([, v]) => v > 0).map(([key, v]) => {
      const from = y(base), to = y(base + v);
      base += v;
      return { key, y: round1(to), h: round1(Math.max(1, from - to - (base === v ? 0 : GAP))) };  // 最下面一段贴着基线，往上每段留一条缝
    });
    return { date: d.date, parts };
  });
  const ticks: Tick[] = [];
  for (let v = 0; v <= hi; v += 120) ticks.push({ y: round1(y(v)), label: `${v / 60}h` });
  return { bars, ticks, lines: [{ key: "keep", y: round1(y(targets.keep)), label: "7 小时" }, { key: "aim", y: round1(y(targets.aim)), label: "8 小时" }] };
}

/** 每晚一条从入睡到醒来的竖条；纵轴是时刻，晚上在上、早上在下，两端取到整小时。 */
export function bedChart(days: HealthDay[]): { bars: ({ date: string; y: number; h: number } | null)[]; ticks: Tick[] } {
  const timed = days.filter(d => d.bed !== null && d.wake !== null);
  if (!timed.length) return { bars: days.map(() => null), ticks: [] };
  const lo = Math.floor(Math.min(...timed.map(d => d.bed!)) / 60) * 60;
  const hi = Math.ceil(Math.max(...timed.map(d => d.wake!)) / 60) * 60;
  const y = (v: number) => BOX.top + (v - lo) / (hi - lo || 1) * (plotBottom - BOX.top);  // 时刻早的在上
  const ticks: Tick[] = [];
  for (let v = lo; v <= hi; v += 120) ticks.push({ y: round1(y(v)), label: clock(v) });
  return { ticks, bars: days.map(d => d.bed === null || d.wake === null ? null : { date: d.date, y: round1(y(d.bed)), h: round1(Math.max(2, y(d.wake) - y(d.bed))) }) };
}

export interface Line { key: string; path: string; points: ({ x: number; y: number; value: number } | null)[]; end: { x: number; y: number } | null }
/** 几条共用纵轴的折线（心率）：纵轴不从 0 起，上下各留一点；缺数的那天断开。 */
export function lineChart(series: { key: string; values: (number | null)[] }[]): { lines: Line[]; ticks: Tick[] } {
  const all = series.flatMap(s => s.values).filter((v): v is number => v !== null);
  if (!all.length) return { lines: [], ticks: [] };
  const lo = Math.floor((Math.min(...all) - 2) / 5) * 5, hi = Math.ceil((Math.max(...all) + 2) / 5) * 5;
  const y = scale(lo, hi);
  const cols = slots(series[0].values.length);
  const step = Math.max(5, Math.ceil((hi - lo) / 4 / 5) * 5);
  const ticks: Tick[] = [];
  for (let v = lo; v <= hi; v += step) ticks.push({ y: round1(y(v)), label: String(v) });
  const lines = series.map(s => {
    const points = s.values.map((v, i) => v === null ? null : { x: round1(cols[i].cx), y: round1(y(v)), value: v });
    let path = "", pen = false;
    for (const p of points) {
      if (!p) { pen = false; continue; }
      path += `${pen ? "L" : "M"}${p.x} ${p.y}`;
      pen = true;
    }
    const drawn = points.filter(p => p !== null);
    return { key: s.key, path, points, end: drawn.length ? { x: drawn[drawn.length - 1]!.x, y: drawn[drawn.length - 1]!.y } : null };
  });
  return { lines, ticks };
}

/** 每天一根步数柱；纵轴从 0 起，到整千。 */
export function stepsChart(days: HealthDay[]): { bars: ({ date: string; y: number; h: number; partial: boolean } | null)[]; ticks: Tick[] } {
  const top = Math.max(1000, ...days.map(d => d.steps ?? 0));
  const unit = top > 12000 ? 5000 : top > 4000 ? 2000 : 1000;
  const hi = Math.ceil(top / unit) * unit;
  const y = scale(0, hi);
  const ticks: Tick[] = [];
  for (let v = 0; v <= hi; v += unit) ticks.push({ y: round1(y(v)), label: v >= 1000 ? `${v / 1000}k` : "0" });
  return { ticks, bars: days.map(d => d.steps === null ? null : { date: d.date, y: round1(y(d.steps)), h: round1(Math.max(d.steps ? 1 : 0, plotBottom - y(d.steps))), partial: d.partial }) };
}
