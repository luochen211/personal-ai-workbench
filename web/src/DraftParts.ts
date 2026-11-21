// 回复草稿的判断和文字：草稿挂到收件箱的行上（归「草稿待确认」）、行上那一句、每条回复发到哪、发送结果写成一句话。
// 不写 JSX，node --test 可以直接加载这个文件。
import type { DraftReply, ReplyDraft, SendResult } from "./api";

/** 有草稿的行：挂上草稿。收件箱里同一条 owner/repo#编号 已有一行就挂上去，没有（例如列表还没读到）就单独成一行。 */
export function withDrafts<T extends { key: string; draft?: ReplyDraft }>(threads: T[], drafts: ReplyDraft[], make: (d: ReplyDraft) => T): T[] {
  const byKey = new Map(drafts.map(d => [d.subject.toLowerCase(), d]));
  const out = threads.map(t => {
    const draft = byKey.get(t.key);
    if (!draft) return t;
    byKey.delete(t.key);
    return { ...t, draft };
  });
  for (const d of byKey.values()) out.push(make(d));
  return out;
}

const clock = (value: string) => value ? new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "";

/** 行上那一句：几条回复、给谁、上次发送失败。 */
export function draftLine(d: Pick<ReplyDraft, "replies" | "error">) {
  const who = [...new Set(d.replies.map(r => r.to).filter(Boolean))].join("、");
  const head = `回复草稿 ${d.replies.length} 条${who ? `（给 ${who}）` : ""}，等你确认发送`;
  return d.error ? `${head} · 上次发送失败` : head;
}

/** 一条回复发到哪：行内评审回到那条线程；其余是 issue / PR 下的评论。 */
export function targetText(r: Pick<DraftReply, "to" | "review_comment_id" | "path">, kind: ReplyDraft["kind"]) {
  const to = r.to ? `回复 ${r.to}` : "回复";
  if (r.review_comment_id) return `${to} · 行内评审线程${r.path ? `（${r.path}）` : ""}`;
  return `${to} · ${kind === "pr" ? "PR" : "issue"} 下的评论`;
}

/** 通知就是草稿依据的那条留言发出的（相差不到 5 分钟）：详情里不再单写「通知时间」，草稿标题上已有谁、几点。 */
export const sameEvent = (d: Pick<ReplyDraft, "based_on"> | null | undefined, at: string) =>
  !!d?.based_on && Math.abs(Date.parse(at) - Date.parse(d.based_on.at)) <= 5 * 60_000;

export const draftMeta = (d: Pick<ReplyDraft, "created_at" | "based_on">) =>
  `生成于 ${clock(d.created_at)}${d.based_on ? ` · 依据 ${d.based_on.who} ${clock(d.based_on.at)} 的留言` : ""}`;

/** 过时提示：对方又回了几条，列出最近的几条。 */
export const staleText = (r: Pick<SendResult, "stale" | "newer">) =>
  `对方又回了 ${r.stale ?? 0} 条，草稿可能过时。` + (r.newer?.length ? `最近：${r.newer.map(n => `${n.who} ${clock(n.at)}：${n.text || "（没有文字）"}`).join("；")}` : "");

/** 一条发送结果写成一句话。 */
export function sendLine(subject: string, r: SendResult) {
  if (r.ok) return `${subject} 已发出 ${r.sent} 条回复`;
  if (r.stale) return `${subject} 没发：对方又回了 ${r.stale} 条，草稿可能过时，点开看`;
  return `${subject} ${r.error || "发送失败"}`;
}

/** 列表上「确认发送」的确认文字。 */
export const sendConfirmText = (count: number) =>
  `用你的账号发出 ${count} 条草稿里的回复。每条发之前重读一次讨论，对方在草稿生成后又回了的先不发，留在草稿里等你看。`;
