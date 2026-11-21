// 今日简报和事项两页的判断逻辑；不写 JSX，node --test 直接加载。
import type { Board, BriefFailure, BriefReport, LearningHandoff, LearningTopic, NovelStatus, ReviewKind, ReviewNumbers, ReviewPeriod } from "./api";

const HEADING = /^(#{1,6})\s+(.+?)\s*#*\s*$/;
const fence = (line: string) => /^\s*(```|~~~)/.test(line);
const titled = (title: string, name: string) => title === name || [name + "（", name + "(", name + " "].some(p => title.startsWith(p));

/** 板块 README「展开」时给的部分：卡片上已经有板块名、「状态」「下一步」，这里去掉一级标题和这两段，只留其余各段。 */
export function readmeRest(readme: string): { text: string; titles: string[] } {
  const out: string[] = [];
  const titles: string[] = [];
  let fenced = false, skipping = false;
  for (const line of readme.split("\n")) {
    const heading = fenced ? null : HEADING.exec(line);
    if (fence(line)) fenced = !fenced;
    if (heading && heading[1].length === 1) { skipping = false; continue; }
    if (heading && heading[1].length === 2) {
      skipping = titled(heading[2], "状态") || titled(heading[2], "下一步");
      if (!skipping) titles.push(heading[2]);
    }
    if (!skipping) out.push(line);
  }
  return { text: out.join("\n").replace(/\n{3,}/g, "\n\n").trim(), titles };
}

/** 简报正文去掉和日期下拉框重复的部分：一级标题「每日简报 日期」、「生成时间」那行、「今天日程」后面的当天日期。生成时间另给，当天的只给钟点。 */
export function briefBody(markdown: string, date: string): { text: string; generated: string } {
  let generated = "", titleDropped = false, fenced = false;
  const out: string[] = [];
  for (const line of markdown.split("\n")) {
    const heading = fenced ? null : HEADING.exec(line);
    if (fence(line)) fenced = !fenced;
    if (heading && heading[1].length === 1 && !titleDropped) { titleDropped = true; continue; }
    const stamp = fenced ? null : /^\s*生成时间[：:]\s*(.+?)\s*$/.exec(line);
    if (stamp && !generated) { generated = stamp[1].startsWith(date) ? stamp[1].slice(date.length).trim() || stamp[1] : stamp[1]; continue; }
    out.push(heading && heading[1].length === 2 && titled(heading[2], "今天日程") && heading[2].includes(date) ? `${heading[1]} 今天日程` : line);
  }
  return { text: out.join("\n").replace(/\n{3,}/g, "\n\n").trim(), generated };
}

/** 「下一步」要醒目提示的话：没写、或写着待补；都没有就是空串。 */
export function gapNote(board: Pick<Board, "next_missing" | "next_gaps">): string {
  if (board.next_missing) return "「下一步」还没写，需补。";
  if (board.next_gaps.length === 1) return `「下一步」有 1 条待补：${board.next_gaps[0]}`;
  if (board.next_gaps.length > 1) return `「下一步」有 ${board.next_gaps.length} 条待补。`;
  return "";
}

export const needsFill = (board: Pick<Board, "next_missing" | "next_gaps">) => gapNote(board) !== "";

/** 生成按钮：今天已有简报就是重新生成。 */
export function runLabel(report: Pick<BriefReport, "dates" | "today"> | null, running: boolean): string {
  if (running) return "生成中…";
  return report?.dates.includes(report.today) ? "重新生成今天的" : "现在生成";
}

const WEEK = "日一二三四五六";
/** 2026-11-12 → 11月12日 周四（今天）；按日历日算，不受时区影响。 */
export function dayLabel(value: string, today?: string): string {
  const [y, m, d] = value.split("-").map(Number);
  const week = WEEK[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  return `${m}月${d}日 周${week}${value === today ? "（今天）" : ""}`;
}

/** 简报里读失败的来源，醒目提示用；都读到了就是空串。 */
export function failureNote(failures: BriefFailure[]): string {
  if (!failures.length) return "";
  return `这份简报有 ${failures.length} 个来源没读到：${failures.map(f => `${f.source}（${f.reason}）`).join("；")}。`;
}

/** 篇号连成段：01、02、03、05 → 01–03、05。 */
export function numberRanges(nos: string[]): string {
  const sorted = [...new Set(nos)].sort();
  const out: string[] = [];
  for (let i = 0; i < sorted.length;) {
    let j = i;
    while (j + 1 < sorted.length && Number(sorted[j + 1]) === Number(sorted[j]) + 1) j++;
    out.push(j - i >= 2 ? `${sorted[i]}–${sorted[j]}` : sorted.slice(i, j + 1).join("、"));
    i = j + 1;
  }
  return out.join("、");
}

/** 今日简报顶部的学习提示：每个课题一行，课题名（页面上链到「学习」标签）加 INDEX.md 的「下一步」和还没填反馈的篇号。 */
export function learningBrief(topics: Pick<LearningTopic, "name" | "next" | "unfilled" | "exists">[]): { name: string; todo: string }[] {
  return topics.filter(t => t.exists).map(t => {
    const parts = [t.next, t.unfilled.length ? `未填反馈 ${numberRanges(t.unfilled)}` : ""].filter(Boolean);
    return { name: t.name, todo: parts.join("；") };
  });
}

/** 「写下一篇」还没结束：已交给在派，或在写。页面这时每 10 秒刷新一次。 */
export const writing = (h: Pick<LearningHandoff, "state">) => h.state === "pending" || h.state === "running";

/** 「写下一篇」按钮上的字。 */
export function nextLabel(topic: Pick<LearningTopic, "next_no" | "handoff">, busy: boolean): string {
  if (busy) return "交给中…";
  if (topic.handoff.state === "running") return `正在写第 ${topic.handoff.no || topic.next_no} 篇…`;
  if (topic.handoff.state === "pending") return "已交给 Agent…";
  return topic.next_no ? `写下一篇（第 ${topic.next_no} 篇）` : "写下一篇";
}

/** 「写下一篇」的进度一句话；没交过或写完超过一天就是空串。 */
export function handoffNote(h: LearningHandoff, now = Date.now()): string {
  const no = h.no ? `第 ${h.no} 篇` : "下一篇";
  switch (h.state) {
    case "pending": return `已交给 Agent，正在派出${no}。`;
    case "lost": return `交给超过 15 分钟还没派出，可以再点一次。`;
    case "running": return `Agent 正在写${no}，写完列表自动刷新。`;
    case "done": return h.finished_at && now - Date.parse(h.finished_at) > 86400000 ? "" : `${no}已写好。`;
    case "blocked": return `${no}没写成：${h.reason || "原因没写"}。`;
    case "failed": return `上一轮没跑完：${h.reason || "原因不明"}。`;
    case "unknown": return "上一轮跑完了，回执没写结果，到「Agent」页看运行记录。";
    default: return "";
  }
}

/** 课题目录里的页面在工作台上的只读地址。path 是课题相对根目录上一层的路径（如「学习课题/沟通表达」），
 *  这样页面里 ../../导读/ 这类链接也走得通；没有 path 时按课题目录取。 */
export const learningUrl = (topic: string, file: string, path = "") =>
  `/learning/${encodeURIComponent(topic)}/${path ? path.split("/").map(encodeURIComponent).join("/") + "/" : ""}${file}`;

/** 小说导读里的页面在工作台上的只读地址（整个导读根目录，导读页之间的 ../ 链接照样走通）。 */
export const novelUrl = (path: string) => `/novels/${path.split("/").map(encodeURIComponent).join("/")}`;

export const NOVEL_LABEL: Record<NovelStatus, string> = { unread: "未开始", reading: "在读", done: "读完" };

/** 点一下阅读状态换到下一个：未开始 → 在读 → 读完 → 未开始。 */
export const nextNovelStatus = (status: NovelStatus): NovelStatus => status === "unread" ? "reading" : status === "reading" ? "done" : "unread";

// ---- 回顾标签 ----
export const REVIEW_NAME: Record<ReviewKind, string> = { week: "周回顾", month: "月回顾" };

/** 一期的离线 HTML 在工作台上的只读地址。 */
export const reviewUrl = (kind: ReviewKind, label: string) => `/review/${kind}/${encodeURIComponent(label)}.html`;

const md = (value: string) => { const [, m, d] = value.split("-").map(Number); return `${m}月${d}日`; };
/** 一期的起止：周写「11月16日–11月22日」，月写「11月」。 */
export const reviewRange = (p: Pick<ReviewPeriod, "kind" | "start" | "end">) =>
  p.kind === "month" ? `${Number(p.start.split("-")[1])}月` : `${md(p.start)}–${md(p.end)}`;

/** 收起时那一行右边的话：有总结写总结，没有就写起止日期和「总结还没写」。 */
export const reviewHead = (p: Pick<ReviewPeriod, "kind" | "start" | "end" | "summary">) => p.summary || `${reviewRange(p)} · 总结还没写`;

/** 2026-11-22T21:00+08:00 → 11月22日 周日 21:00（照字面读，不换时区）。 */
export function runTime(iso: string): string {
  const found = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(iso);
  return found ? `${dayLabel(found[1])} ${found[2]}` : iso;
}

/** 分区上的生成按钮：这一期已有就是更新数据。 */
export function reviewRunLabel(label: string, exists: boolean, running: boolean): string {
  if (running) return "生成中…";
  return exists ? `更新 ${label} 的数据` : `现在生成 ${label}`;
}

/** Agent 还没写的块（md 里有这个块、但还是占位符）。 */
export const reviewMissing = (p: Pick<ReviewPeriod, "has" | "written">) =>
  (["summary", "top3", "questions", "projects"] as const).filter(name => p.has[name] && !p.written[name]);

/** 「让 Agent 写」的进度一句话；没交过或写完超过一天就是空串。 */
export function reviewHandoffNote(h: LearningHandoff | undefined, now = Date.now()): string {
  switch (h?.state) {
    case "pending": return "已交给 Agent，正在派出。";
    case "lost": return "交给超过 15 分钟还没派出，可以再点一次。";
    case "running": return "Agent 正在写总结和三问，写完这里自动刷新。";
    case "done": return h.finished_at && now - Date.parse(h.finished_at) > 86400000 ? "" : "Agent 已写好。";
    case "blocked": return `没写成：${h.reason || "原因没写"}。`;
    case "failed": return `上一轮没跑完：${h.reason || "原因不明"}。`;
    case "unknown": return "上一轮跑完了，回执没写结果，到「Agent」页看运行记录。";
    default: return "";
  }
}

/** 产出那一行：只写 md 里读到的数字，一个都没有就是空串。 */
export function reviewOutput(n: ReviewNumbers): string {
  const parts = [n.merged_prs !== undefined ? `合并 PR ${n.merged_prs} 个` : "", n.closed_issues !== undefined ? `关掉 issue ${n.closed_issues} 个` : ""];
  return parts.filter(Boolean).join(" · ");
}

/** 时间去向的横条：每类一条，宽度按最多的那一类算（0–100）。 */
export function reviewBars(n: ReviewNumbers): { name: string; hours: number; share: number | null; width: number }[] {
  const rows = n.categories ?? [];
  const peak = Math.max(0, ...rows.map(r => r.hours));
  return rows.map(r => ({ ...r, width: peak > 0 ? Math.round(r.hours / peak * 100) : 0 }));
}
