// 自媒体页的显示逻辑；不写 JSX，node --test 直接加载。
import type { MaitianFollower, MaitianReport } from "./api";

const pad = (n: number) => String(n).padStart(2, "0");

/** 一期的计划/发布时间：11-19 18:00（本机时区）；没排期是空串。 */
export function scheduleLabel(iso: string): string {
  const t = Date.parse(iso);
  if (!iso || Number.isNaN(t)) return "";
  const d = new Date(t);
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 粉丝数：千分位。 */
export const count = (n: number) => n.toLocaleString("en-US");

/** 近 7 天涨粉：+201、-3、0；没有可比的那天是「—」。 */
export function weekLabel(f: Pick<MaitianFollower, "week">): string {
  if (f.week === null || f.week === undefined) return "—";
  return f.week > 0 ? `+${count(f.week)}` : count(f.week);
}

/** 各平台合计和近 7 天合计（没有可比那天的平台不计入涨粉）。 */
export function totals(rows: MaitianFollower[]): { total: number; week: number } {
  return { total: rows.reduce((n, r) => n + r.total, 0), week: rows.reduce((n, r) => n + (r.week ?? 0), 0) };
}

/** 页面顶部那一句：连不上、口令不对、演示数据；读到真实数据时是空串。 */
export function stateLine(report: Pick<MaitianReport, "state" | "demo" | "error">): string {
  if (report.state === "down") return "麦田没在运行";
  if (report.state === "denied" || report.state === "error") return report.error || "麦田没读到";
  return report.demo ? "现在显示的是麦田演示数据" : "";
}
