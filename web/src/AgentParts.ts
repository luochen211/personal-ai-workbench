// 收件箱顶部的 Agent 计数、要不要每 10 秒刷新、一轮的结果写成一句话、成批交给后的提示。
// 不写 JSX，node --test 可以直接加载这个文件。
import { createElement as h } from "react";
import { resultText } from "./PrParts.ts";  // 带扩展名：node --test 直接加载
import type { AgentRun, AgentsReport, FeedHandoff, FeedRunResult } from "./api";

export const BATCH = 40;  // 一批最多送这么多条

/** 有 Agent 在跑或排队就每 10 秒刷新；否则跟着 60 秒自动刷新（刚交给的 PR 和 issue 由 App 跟）。 */
export const polling = (agents: AgentsReport | null) => !!agents && agents.running + agents.queued > 0;

/** 顶部一行：N 个 Agent 在跑 · M 个排队 · 最多同时 K 个；接单程序容量比规则上限小的写明。 */
export function agentsLine(a: AgentsReport) {
  const parts = [`${a.running} 个 Agent 在跑`, `${a.queued} 个排队`];
  if (a.unknown) parts.push(`${a.unknown} 个没有回音`);
  parts.push(`最多同时 ${a.limit} 个` + (a.capacity !== null && a.capacity < a.parallel ? `（接单程序容量 ${a.capacity}，规则上限 ${a.parallel}）` : ""));
  return parts.join(" · ");
}

export const running = (a: AgentsReport) => a.agents.filter(x => x.state === "running");
const clock = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";
/** 一个在跑的 Agent 写成一行：规则、对象、开始时间。 */
export const agentLine = (x: AgentRun, time: (value: string | null) => string = clock) => `${x.rule} · ${x.subject || "（没有对象）"} · ${time(x.started_at)} 开始`;

export function feedResultText(result?: FeedRunResult | null): [string, string] | null {
  if (result?.kind === "pr") return [`已写回复，开了 PR ${result.prs.map(n => `#${n}`).join("、")}`, "ok"];
  return resultText(result);
}

/** 一轮的结果写成一行；推送了的点开看提交，开了 PR 的每个编号点开看 PR。 */
export function FeedResultLine({ result }: { result?: FeedRunResult | null }) {
  const said = feedResultText(result);
  if (!said || !result) return null;
  const link = (href: string, text: string) => h("a", { key: href, href, target: "_blank", rel: "noreferrer" }, `${text} ↗`);
  const body = result.kind === "pr" && result.urls?.length === result.prs.length
    ? ["已写回复，开了 PR ", ...result.prs.flatMap((n, i) => [i ? "、" : "", link(result.urls![i], `#${n}`)])]
    : result.kind !== "pr" && result.url ? [link(result.url, said[0])] : [said[0]];
  return h("p", { className: `result-line ${said[1]}`, role: "status" }, "结果：", ...body);
}

/** 每批最多 BATCH 条。 */
export function batches(subjects: string[], size = BATCH) {
  const out: string[][] = [];
  for (let i = 0; i < subjects.length; i += size) out.push(subjects.slice(i, i + size));
  return out;
}

/** 交出后的一句话：交了几条（PR、issue 各几条）、跳过了哪些、中台没接上时怎么办。
 *  reply 是待你回里的模式：「帮我回复」只写回复草稿，「处理并回复」改完再写草稿，草稿都等你确认；不给是待动手的「交给 Agent」。 */
export function handoffNotice(results: FeedHandoff[], reply: "" | "reply" | "handle" | boolean = "") {
  const handed = results.flatMap(r => r.handed), skipped = results.flatMap(r => r.skipped);
  const prs = handed.filter(x => x.kind === "pr").length;
  const count = `${handed.length} 条（PR ${prs} 条，issue ${handed.length - prs} 条）`;
  const mode = reply === true ? "reply" : reply || "";
  const parts = [mode === "reply"
    ? `已请 Agent 帮你回复 ${count}，只写回复草稿、不改代码；写好的回复会出现在「草稿待确认」里，你确认后才发；进度每 10 秒更新。`
    : mode === "handle"
    ? `已交给 Agent 处理并回复 ${count}，要改的改好再写回复草稿；写好的回复会出现在「草稿待确认」里，你确认后才发；进度每 10 秒更新。`
    : `已交给 Agent ${count}，进度每 10 秒更新。`];
  if (skipped.length) parts.push(`跳过 ${skipped.length} 条：${skipped.map(s => `${s.subject} ${s.reason}`).join("；")}。`);
  const error = results.map(r => r.error).find(Boolean);
  if (error) parts.push(error);
  return parts.join("");
}
