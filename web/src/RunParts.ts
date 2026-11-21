// 「运行记录」和「巡检状态」共用的判断和小部件。不写 JSX，node --test 可以直接加载这个文件。
import { createElement as h } from "react";

export interface RunRow {
  id: number; rule: string; agent: string; state: string; summary: string; error: string; attempts: number;
  created_at: string; claimed_at: string | null; finished_at: string | null; subjects: string[]; seconds: number | null; link: string;
}
// 失败的轮次：原因相同、中间没跑成过的连着几轮并成一行，count 是轮数，first_at 是最早一轮
export interface FailedRun extends RunRow { count: number; first_at: string }
// 巡检状态：没事可做的轮次和重报的轮次不逐条列，最近一次运行只给时间和好坏
export interface WatchStatus {
  last_run: { id: number; created_at: string; state: string; failed: boolean } | null;
  dispatched: RunRow | null; failed: FailedRun[];
  failures: number; notified: boolean; last_error: string; last_failed: string | null;
  schedule: string; next: string; notify_after: number; error: string;
  // 工作台的监看闸门（personal/watch_gate.py）：每 interval 秒看一眼 GitHub，有变化才触发监看；旧接口没有这一项
  gate?: { interval: number; checked_at: string | null; triggered_at: string | null; wake_at: string | null; error: string };
}

export const stateName: Record<string, string> = { done: "完成", nothing: "没事可做", claimed: "处理中", queued: "排队中", cancelled: "已停止", unknown: "状态未知" };
export const failed = (r: RunRow) => r.state === "cancelled" || (r.state !== "done" && !!r.error);
export const finished = (r: RunRow) => !failed(r) && (r.state === "done" || r.state === "nothing");
export const when = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";
// 用时：不到 1 分钟写秒，不到 1 小时写分秒，1 小时以上写小时和分（不写秒），1 天以上写天和小时
export const spent = (seconds: number | null) => {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
  const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60);
  if (hours < 24) return minutes ? `${hours} 小时 ${minutes} 分` : `${hours} 小时`;
  const days = Math.floor(hours / 24), rest = hours % 24;
  return rest ? `${days} 天 ${rest} 小时` : `${days} 天`;
};

/** 列表里的用时：还在跑（处理中）写「已运行」，从这一轮开始算到现在；其余写「用时」，是开始到结束，没结束写「—」。 */
export function duration(r: RunRow, now = Date.now()) {
  if (r.state === "claimed" && r.claimed_at) return `已运行 ${spent(Math.max(0, Math.round((now - Date.parse(r.claimed_at)) / 1000)))}`;
  return `用时 ${spent(r.seconds)}`;
}

/** 最近一次运行写成一句：今天的只写时分。 */
export function lastRunLine(last: WatchStatus["last_run"], today = new Date()) {
  if (!last) return "还没有运行过";
  const at = new Date(last.created_at);
  const time = at.toDateString() === today.toDateString()
    ? at.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }) : when(last.created_at);
  const how = last.failed ? "没跑成" : last.state === "claimed" || last.state === "queued" ? "进行中" : "正常";
  return `最近一次运行 ${time}，${how}`;
}

/** 监看闸门写成一句：多久看一次、最近一次看和触发的时间，没能触发时写原因。 */
export function gateLine(gate: WatchStatus["gate"]) {
  if (!gate) return "";
  const every = Math.round(gate.interval / 60);
  const parts = [`工作台每 ${every} 分钟看一次 GitHub，有变化才触发监看`, `最近一次看 ${when(gate.checked_at)}`,
    `最近一次触发 ${when(gate.triggered_at)}`];
  if (gate.wake_at) parts.push(`${when(gate.wake_at)} 到点复查`);
  return parts.join(" · ") + (gate.error ? `；上次没能触发：${gate.error}` : "");
}

/** 失败原因写成一行：报错是多行的（比如一段调用栈），留第一行「：」前的说法和最后一行。 */
export function reasonLine(text: string) {
  const lines = text.split("\n").map(l => l.trim()).filter(Boolean);
  if (lines.length < 2) return lines[0] ?? "";
  const head = lines[0].includes("：") ? lines[0].split("：")[0] + "：" : "";
  return head + lines[lines.length - 1];
}

function runRow(r: RunRow | FailedRun) {
  const bad = failed(r);
  const times = "count" in r && r.count > 1 ? `${r.count} 轮 · ${when(r.first_at)} – ${when(r.created_at)}` : `${when(r.created_at)} · ${duration(r)}`;
  return h("div", { key: r.id, className: "run-row" },
    h("span", { className: "num" }, `#${r.id}`),
    h("div", { className: "run-main" }, h("strong", null, r.rule),
      h("p", { className: bad ? "run-error" : "", title: bad ? r.error : undefined }, bad ? `原因：${reasonLine(r.error || r.summary) || "被停止"}` : r.summary || "进行中")),
    h("div", { className: "run-side" },
      h("span", { className: `tag ${bad ? "bad" : finished(r) ? "ok" : "warn"}` }, stateName[r.state] ?? r.state),
      h("small", null, times)));
}

/** 巡检状态的正文：下次运行和最近一次运行各一句、连续失败（有才显示）、最近一次派出、失败的轮次。 */
export function WatchView({ data, reload }: { data: WatchStatus; reload: () => void }) {
  return h("div", null,
    data.error && h("div", { className: "error", role: "alert" }, data.error),
    data.failures > 0 && h("div", { className: "error", role: "alert" },
      `连续失败 ${data.failures} 次（${data.notify_after} 次通知一次）；最近一次失败 ${when(data.last_failed)}：${reasonLine(data.last_error)}${data.notified ? "（已发桌面通知）" : ""}`),
    h("div", { className: "toolbar" },
      h("p", { className: "meta" }, `下次${data.gate ? "兜底" : ""}运行 ${data.next || "—"}${data.schedule ? `（定时 ${data.schedule}）` : ""} · ${lastRunLine(data.last_run)}`),
      h("button", { onClick: reload }, "↻ 刷新")),
    data.gate && h("p", { className: "meta" }, gateLine(data.gate)),
    h("div", { className: "view-label" }, "最近一次派出"),
    h("div", { className: "task-list" }, data.dispatched ? runRow(data.dispatched) : h("div", { className: "notice" }, "最近没有派出过。")),
    data.failed.length > 0 && h("div", { className: "view-label" }, "失败的轮次 ", h("small", null, data.failed.length)),
    data.failed.length > 0 && h("div", { className: "task-list" }, data.failed.map(runRow)));
}
