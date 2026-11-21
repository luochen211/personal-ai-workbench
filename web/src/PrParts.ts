// 「PR 待处理」页的判断和小部件：能不能勾、要不要每 10 秒看进度、结果写成一句话。
// 不写 JSX，node --test 可以直接加载这个文件。
import { createElement as h } from "react";
import { waiting } from "./handoff.ts";  // 带扩展名：node --test 直接加载
import type { PrHandoff, PrReport, PrRow, PrRunResult, PrSaid } from "./api";

type Row = Pick<PrRow, "stage" | "stalled">;

// 已交给、还在派或在处理的不能再勾；派发可能丢了（交给后 15 分钟没有新的一轮）的可以再交
export const selectable = (r: Row) => !waiting(r);
// 刚交给的每 10 秒看一次进度，一小时后回到 60 秒刷新
export const following = (r: Pick<PrRow, "stage" | "stalled" | "handed_at">, now = Date.now()) =>
  !!r.handed_at && waiting(r) && now - Date.parse(r.handed_at) < 3600000;

export function sections<T extends Pick<PrRow, "section">>(rows: T[]) {
  return { active: rows.filter(r => r.section === "active"), recent: rows.filter(r => r.section !== "active") };
}

/** 导航上的数：对方有新回复、还没交给 Agent（或交出后又有新回复）的 PR。 */
export const pendingCount = (report: PrReport | null) => (report?.prs ?? []).filter(r => r.section === "active" && r.pending && selectable(r)).length;

/** 全选：勾上全部能勾的；已经全勾上了就全清掉。 */
export function toggleAll(chosen: Set<string>, rows: (Row & Pick<PrRow, "subject">)[]): Set<string> {
  const open = rows.filter(selectable).map(r => r.subject);
  return open.length && open.every(s => chosen.has(s)) ? new Set() : new Set(open);
}

/** 列表刷新后，勾选只留还能勾的。 */
export const keepChosen = (chosen: Set<string>, rows: (Row & Pick<PrRow, "subject">)[]) =>
  new Set(rows.filter(r => selectable(r) && chosen.has(r.subject)).map(r => r.subject));

export function resultText(result?: PrRunResult | null): [string, string] | null {
  if (!result) return null;
  if (result.kind === "pushed") return [`已推送 ${result.commit}`, "ok"];
  if (result.kind === "closed") return ["已关闭", "ok"];
  if (result.kind === "replied") return ["已写回复", "ok"];
  if (result.kind === "no_action") return [`不用动：${result.reason || "没写原因"}`, "ok"];
  if (result.kind === "blocked") return [`卡住：${result.reason || "没写原因"}`, "bad"];
  if (result.kind === "failed") return [`这一轮没跑完：${result.reason || "没说原因"}`, "bad"];
  return [`结果不明：${result.reason || "回执没有结果行"}`, "warn"];
}

/** 一轮的结果写成一行；推送了的点开看提交。 */
export function PrResultLine({ result }: { result?: PrRunResult | null }) {
  const said = resultText(result);
  if (!said) return null;
  const [text, tone] = said;
  return h("p", { className: `result-line ${tone}`, role: "status" }, "结果：",
    result?.url ? h("a", { href: result.url, target: "_blank", rel: "noreferrer" }, `${text} ↗`) : text);
}

export const saidLine = (s: Pick<PrSaid, "who" | "what" | "path" | "text">) => `${s.who} ${s.what}${s.path ? `（${s.path}）` : ""}：${s.text || "（没有文字）"}`;
// 非维护者的话 Agent 只回答问题，不照着改代码、关闭或换方向
// 维护者不加身份标签；只有非维护者标一个「非维护者」提醒
export const roleTag = (s: Pick<PrSaid, "maintainer" | "role">): [string, string] | null => s.maintainer ? null : ["非维护者", "warn"];

export function stallText(r: Row) {
  return r.stage === "已交给 Agent" && r.stalled === "lost" ? "交给后超过 15 分钟还没有新的一轮：派发可能丢了，可重试。" : "";
}

/** 交出后的一句话：交了几条、跳过了哪些、中台没接上时怎么办。 */
export function handoffNotice(result: PrHandoff) {
  const parts = [`已交给 Agent ${result.handed.length} 条 PR，处理进度每 10 秒更新。`];
  if (result.skipped.length) parts.push(`跳过 ${result.skipped.length} 条：${result.skipped.map(s => `${s.subject} ${s.reason}`).join("；")}。`);
  if (result.error) parts.push(result.error);
  return parts.join("");
}
