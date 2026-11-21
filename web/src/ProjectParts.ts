// 项目详情页的「我的 PR」区和「已完成」的 issue。不写 JSX，node --test 可以直接加载这个文件做渲染测试。
import { createElement as h, useState } from "react";
import type { Activity, ClosedBy, DoneIssue, DoneReport, Issue, LastActivity, PrNews, ProjectDoneIssue, ProjectPr, UnreadMessage } from "./api";
import { checkLabel, reviewLabel } from "./IssueParts.ts";  // 带扩展名：node --test 直接加载

export const when = (value?: string | null) => value
  ? new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";
const tag = (text: string, tone = "") => h("span", { className: tone ? `tag ${tone}` : "tag" }, text);
const link = (href: string, text: string) => h("a", { href, target: "_blank", rel: "noreferrer" }, text);

/** 关闭原因：被哪个 PR 关闭；GitHub 记成提交关闭的写上提交；都没有就是手动关闭。 */
export function closedBy(by: ClosedBy): string {
  if (by.kind === "pr") return `被 PR #${by.number} 关闭${by.commit ? `（合并提交 ${by.commit}）` : ""}`;
  if (by.kind === "commit") return `被提交 ${by.oid} 关闭`;
  return `${by.actor || "有人"}手动关闭`;
}

/** issue 列表的缓存比「已完成」旧时，刚关闭的 issue 还会留在 open 列表里：关闭时间不早于列表里的更新时间就不再列。
 * 重新打开的 issue 更新时间更晚，照常列出。 */
export function hideClosed<T extends Pick<Issue, "repo" | "number" | "updated_at">>(issues: T[], activity: Activity | null): T[] {
  const done = activity?.done_issues ?? [];
  if (!done.length || !activity?.repo) return issues;
  const repo = activity.repo.toLowerCase();
  return issues.filter(i => !(i.repo.toLowerCase() === repo && done.some(d => d.number === i.number && (d.closed_at ?? "") >= i.updated_at)));
}

/** 收件箱：全部项目的「已完成」里有、关闭时间不早于列表里更新时间的，不再列为 open。 */
export function hideDone<T extends Pick<Issue, "subject" | "updated_at">>(issues: T[], done: Pick<ProjectDoneIssue, "subject" | "closed_at">[]): T[] {
  if (!done.length) return issues;
  const closed = new Map(done.map(d => [d.subject.toLowerCase(), d.closed_at ?? ""]));
  return issues.filter(i => {
    const at = closed.get(i.subject.toLowerCase());
    return at === undefined || at < i.updated_at;
  });
}

/** 「已完成」里去掉重新打开的：open 列表里有、更新时间晚于关闭时间。 */
export function reopenedOut<T extends Pick<ProjectDoneIssue, "subject" | "closed_at">>(done: T[], issues: Pick<Issue, "subject" | "updated_at">[]): T[] {
  const open = new Map(issues.map(i => [i.subject.toLowerCase(), i.updated_at]));
  return done.filter(d => !((open.get(d.subject.toLowerCase()) ?? "") > (d.closed_at ?? "")));
}

/** 项目详情顶部的两个计数：我的 PR 待审、近 30 天已合并。没读到时是 null。 */
export function prStats(activity: Activity | null): { pending: number; merged: number } | null {
  return activity?.available && activity.counts ? { pending: activity.counts.pending, merged: activity.counts.merged } : null;
}

/** 新消息标记：读到别人留言的条数就写「N 条新评论」，没读到（只有动态、或条数不明）写「有新消息」。 */
export function newsLabel(news: PrNews): string {
  return news.count ? `${news.count}${news.more ? "+" : ""} 条新评论` : "有新消息";
}

/** 「最近动态：Hmbown 评论 · 11/12 16:34」；说不清谁做了什么时只写时间。 */
export function lastLine(last?: LastActivity | null): string {
  if (!last?.at) return "";
  const what = [last.who, last.what].filter(Boolean).join(" ");
  return `最近动态：${what ? `${what} · ` : ""}${when(last.at)}`;
}

/** 「我的 PR」各组的排序：recent（默认）有新消息的在前、再按最近动态从新到旧；number 按编号从大到小。 */
export type PrSort = "recent" | "number";
const stamp = (pr: ProjectPr) => Date.parse(pr.last?.at || pr.updated_at || pr.merged_at || pr.closed_at || pr.created_at || "") || 0;
export function sortPrs(items: ProjectPr[], sort: PrSort): ProjectPr[] {
  return items.slice().sort((a, b) => sort === "number" ? b.number - a.number
    : Number(!!b.news) - Number(!!a.news) || stamp(b) - stamp(a) || b.number - a.number);
}

/** 项目详情的网址 #/p/<项目>，排序不是默认时带 ?sort=number。 */
export function projectHash(id: string, sort: PrSort = "recent"): string {
  return `#/p/${encodeURIComponent(id)}${sort === "number" ? "?sort=number" : ""}`;
}
export function parseProjectHash(hash: string): { id: string; sort: PrSort } | null {
  const found = hash.match(/^#\/p\/([^?]+)(?:\?(.*))?$/);
  if (!found) return null;
  return { id: decodeURIComponent(found[1]), sort: new URLSearchParams(found[2] ?? "").get("sort") === "number" ? "number" : "recent" };
}

function prRow(pr: ProjectPr) {
  const tags = [];
  const recent = lastLine(pr.last);
  let time;
  if (pr.news) tags.push(tag(newsLabel(pr.news), "news"));
  if (pr.state === "open") {
    const [ci, ciTone] = checkLabel(pr.checks);
    const [review, reviewTone] = reviewLabel(pr.review);
    tags.push(pr.draft ? tag("草稿") : tag("开着", "warn"), tag(ci, ciTone), tag(review, reviewTone));
    time = `创建于 ${when(pr.created_at)} · ${recent || `更新于 ${when(pr.updated_at)}`}`;
  } else if (pr.state === "merged") {
    tags.push(tag("已合并", "ok"));
    time = pr.last?.what === "已合并" ? recent : `合并于 ${when(pr.merged_at)}${recent ? ` · ${recent}` : ""}`;
  } else {
    tags.push(tag("已关闭未合并", "bad"));
    time = pr.last?.what === "已关闭" ? recent : `关闭于 ${when(pr.closed_at)}${recent ? ` · ${recent}` : ""}`;
  }
  const said = pr.news?.who ? `新消息：${pr.news.who}${pr.news.text ? `「${pr.news.text}」` : ""} · ${when(pr.news.at)}` : "";
  return h("a", { key: pr.number, className: `task-row pr-row${pr.news ? " has-news" : ""}`, href: pr.news?.url || pr.url, target: "_blank", rel: "noreferrer" },
    h("span", { className: "num" }, `#${pr.number}`),
    h("strong", null, pr.title, h("small", { className: "task-github" }, time), said && h("small", { className: "task-github pr-said" }, said)),
    h("span", { className: "pr-tags" }, ...tags), h("span", null, "↗"));
}

const LATER: ["merged" | "closed", string, string][] = [
  ["merged", "近 30 天已合并", "近 30 天没有合并的 PR。"],
  ["closed", "已关闭未合并", "近 30 天没有关闭未合并的 PR。"],
];

function group(key: string, name: string, items: ProjectPr[], empty: string, sort: PrSort) {
  return h("div", { key, className: `pr-group${key === "news" ? " pr-news" : ""}` },
    h("h3", { className: "section-title" }, name, h("small", null, items.length)),
    items.length ? h("div", { className: "task-list" }, sortPrs(items, sort).map(prRow)) : h("p", { className: "meta" }, empty));
}

function sortSwitch(sort: PrSort, setSort: (sort: PrSort) => void) {
  return h("span", { className: "sort-switch", role: "group", "aria-label": "PR 排序" }, "排序：",
    ...([["recent", "最近动态"], ["number", "编号"]] as [PrSort, string][]).map(([value, label]) =>
      h("button", { key: value, className: `text-button${sort === value ? " on" : ""}`, "aria-pressed": sort === value, onClick: () => setSort(value) }, label)));
}

/** 项目详情的「我的 PR」：本人在项目仓库的 PR，点开去 GitHub。默认只列开着的；近 30 天已合并、已关闭未合并
 * 只写一行条数，点「展开」才列明细；其中 GitHub 通知未读的提到最上面的「有新消息」，读了（下次通知同步后）回到收起。
 * 展开不记住，刷新后收起；排序（最近动态 / 编号）由外面给，写在网址里。 */
export function PrSection({ activity, failed, sort = "recent", setSort = () => {} }: { activity: Activity | null; failed: string; sort?: PrSort; setSort?: (sort: PrSort) => void }) {
  const [expanded, setExpanded] = useState(false);
  return h(PrList, { activity, failed, expanded, toggle: () => setExpanded(e => !e), sort, setSort });
}

/** PrSection 的内容，展开与否由外面给（测试直接渲染这一层）。 */
export function PrList({ activity, failed, expanded, toggle, sort = "recent", setSort = () => {} }: {
  activity: Activity | null; failed: string; expanded: boolean; toggle: () => void; sort?: PrSort; setSort?: (sort: PrSort) => void;
}) {
  const prs = activity?.prs;
  const total = prs ? prs.open.length + prs.merged.length + prs.closed.length : 0;
  const later = prs ? prs.merged.length + prs.closed.length : 0;
  const news = prs?.news ?? [];
  let body;
  if (failed && !prs) body = h("div", { className: "error", role: "alert" }, `PR 没读到：${failed}`);
  else if (!activity) body = h("p", { className: "meta" }, "正在读取 PR…");
  else body = [
    activity.limited && h("div", { key: "limited", className: "notice-line", role: "status" }, activity.limited),
    activity.news_note && h("div", { key: "news-note", className: "notice-line", role: "status" }, activity.news_note),
    (activity.error || failed) && h("div", { key: "error", className: "error", role: "alert" }, activity.error || `PR 没读到：${failed}`),
    ...(activity.loaded !== false && prs ? [
      news.length > 0 && group("news", "有新消息", news, "", sort),
      group("open", "开着", prs.open, "没有开着的 PR。", sort),
      h("p", { key: "later", className: "meta pr-later" }, `近 30 天已合并 ${prs.merged.length} 条 · 已关闭未合并 ${prs.closed.length} 条`,
        later > 0 && h("button", { className: "text-button", "aria-expanded": expanded, onClick: toggle }, expanded ? " 收起" : " 展开")),
      ...(expanded ? LATER.map(([key, name, empty]) => group(key, name, prs[key], empty, sort)) : []),
    ] : []),
  ];
  return h("section", { className: "project-prs", id: "project-prs" },
    h("div", { className: "toolbar" },
      h("div", { className: "view-label" }, "我的 PR ", h("small", null, activity ? total : "…")),
      prs && sortSwitch(sort, setSort),
      h("span", { className: "meta read-at" }, `读取于 ${when(activity?.fetched_at)}${activity?.refreshing ? " · 正在更新" : ""}`)),
    h("p", { className: "meta" }, `你在 ${activity?.repo || "这个仓库"} 提的 PR，只读；开着的 2 分钟内更新，已合并、已关闭的长期保存，有新的合并或关闭时重读。新消息跟 GitHub 未读一致，在 GitHub 读过、下次同步消息后标记消失。`),
    body);
}

/** 项目详情顶部「GitHub 消息」旁边：这个项目未读的 issue / PR 消息，最多 3 条，点开去 GitHub 对应位置。 */
export function ProjectUnread({ activity }: { activity: Activity | null }) {
  const items: UnreadMessage[] = activity?.unread ?? [];
  if (!items.length) return null;
  const total = activity?.unread_total ?? items.length;
  return h("section", { className: "project-unread", "aria-label": "这个项目的未读消息" },
    h("h3", { className: "section-title" }, "未读消息", h("small", null, total)),
    h("ul", null, items.map(u => h("li", { key: `${u.kind}${u.number}` },
      link(u.url, `#${u.number} ${u.title}`),
      h("span", { className: "meta" }, u.who ? ` — ${u.who}${u.text ? `：${u.text}` : ` ${u.what}`}` : " — 有新消息", ` · ${when(u.at)}`)))),
    total > items.length && h("p", { className: "meta" }, `另有 ${total - items.length} 条，点上面的「GitHub 消息」查看全部。`));
}

function doneRow(issue: DoneIssue & { subject?: string; project_name?: string }) {
  const by = issue.by;
  const source = by.kind === "manual" ? closedBy(by) : link(by.url, closedBy(by));
  return h("div", { key: issue.subject ?? issue.number, className: "task-row done-row" },
    h("span", { className: "num" }, `#${issue.number}`),
    h("strong", null, link(issue.url, issue.title),
      h("small", { className: "task-github" }, issue.project_name ? `${issue.project_name} · ` : "", `关闭于 ${when(issue.closed_at)} · `, source,
        issue.author ? ` · ${issue.author} 创建` : "")),
    tag(issue.reason, issue.reason === "已完成" ? "ok" : ""));
}

/** 「已完成」筛选：近 30 天关闭、你创建或被你的 PR 关闭的 issue，带关闭原因。 */
export function DoneIssueList({ activity }: { activity: Activity | null }) {
  if (!activity) return h("div", { className: "notice" }, "正在读取已完成的 issue…");
  const items = activity.done_issues ?? [];
  return h("div", { className: "task-list" },
    items.length ? items.map(doneRow) : h("div", { className: "notice" }, activity.limited || "近 30 天没有你创建或被你的 PR 关闭的 issue。"));
}

/** 收件箱的「近 30 天已完成」：全部项目近 30 天关闭、你创建或被你的 PR 关闭的 issue，带项目名和关闭原因。
 * items 是按项目、搜索筛过的那些；report 给读取时间和限流、出错说明。 */
export function AllDoneList({ report, items, failed }: { report: DoneReport | null; items: ProjectDoneIssue[]; failed: string }) {
  if (!report) return h("div", { className: failed ? "error" : "notice", role: failed ? "alert" : undefined },
    failed ? `已完成的 issue 没读到：${failed}` : "正在读取已完成的 issue…");
  const empty = report.limited || (report.loaded ? "近 30 天没有你创建或被你的 PR 关闭的 issue。" : "已完成的 issue 还没读到。");
  return h("div", null,
    h("p", { className: "meta read-at" }, `近 30 天（${report.since} 起）关闭、你创建或被你的 PR 关闭的 issue · 读取于 ${when(report.fetched_at)}${report.refreshing ? " · 正在更新" : ""}`),
    report.limited && items.length > 0 && h("div", { className: "notice-line", role: "status" }, report.limited),
    report.error && h("div", { className: "error", role: "alert" }, report.error),
    h("div", { className: "task-list" }, items.length ? items.map(doneRow) : h("div", { className: "notice" }, empty)));
}
