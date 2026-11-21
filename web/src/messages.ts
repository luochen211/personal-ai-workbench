// GitHub 消息的提醒计数、完成情况和状态标签；不写 JSX，node --test 直接加载。
import { createElement as h } from "react";

/** 主题状态：merged 已合并的 PR、completed 以「已完成」关闭的 issue、closed 其他关闭、open 开着、unknown 没读到；
 *  CI、发布等没有主题状态，是空字符串。 */
export type SubjectState = "" | "open" | "merged" | "closed" | "completed" | "unknown";
interface Progress { done?: boolean }

/** 已合并的 PR 和已完成关闭的 issue 算已完成。 */
export const isDone = (n: Progress) => n.done === true;

/** 侧栏和顶栏只数要你动手的未读通知（请你评审、分配给你）；CI、自己 PR 的更新、提到、订阅都不算，已完成的、归档项目的也不算。 */
export function actionCount(notifications: { unread: boolean; actionable?: boolean; done?: boolean; classification?: string }[]): number {
  return notifications.filter(n => n.unread && n.actionable === true && !isDone(n) && n.classification !== "archived").length;
}

/** 「未完成」「已完成」两组：默认看未完成的，已完成的收起来。 */
export function byProgress<T extends Progress>(rows: T[], progress: string): T[] {
  return rows.filter(n => progress === "done" ? isDone(n) : !isDone(n));
}

const labels: Record<string, [string, string]> = {
  merged: ["已合并", "ok"], completed: ["已完成", "ok"], closed: ["已关闭", ""], unknown: ["状态未读到", "warn"],
};
/** 状态标签的文字和色调；开着的和没有主题状态的不打标签。 */
export const stateLabel = (state?: string): [string, string] | null => labels[state ?? ""] ?? null;

export function StateTag({ state }: { state?: string }) {
  const label = stateLabel(state);
  return label ? h("span", { className: label[1] ? `tag ${label[1]}` : "tag" }, label[0]) : null;
}

/** GitHub 未读的消息条数，已归档项目的不算；和收件箱选「未读」时的条数一致。 */
export function unreadCount(notifications: { unread: boolean; classification?: string }[]): number {
  return notifications.filter(n => n.unread && n.classification !== "archived").length;
}

/** 选「未读消息」：未读的全列出（已合并 PR 下的新评论也要看到），不再按完成情况收起；
 *  看「全部消息」时不列已归档项目的，条数和导航徽标一致。其他筛选照旧按完成情况分组。 */
export function unreadView<T extends Progress & { unread: boolean; classification?: string }>(rows: T[], scope: string): T[] {
  return rows.filter(n => n.unread && (scope !== "all" || n.classification !== "archived"));
}

// 已同步通知的摘要（/api/github/notifications 的 digest，见 personal/pr_news.py）
export interface NewsItem {
  id: string; project_id: string | null; project_name: string | null; repository: string; number: number | null;
  kind: "pr" | "issue" | "other"; title: string; category: string; count: number | null; more: boolean;
  who: string; what: string; text: string; at: string; url: string;
}
export interface ProjectDigest { unread: number; latest_at: string; latest: NewsItem | null }
export interface Digest { projects: Record<string, ProjectDigest>; latest: NewsItem[]; unread: number; note: string }

const ref = (item: NewsItem) => item.number ? `${item.kind === "pr" ? "PR" : item.kind === "issue" ? "issue" : ""} #${item.number}`.trim() : item.category;

/** 一条消息说了什么：「octocat 在 PR #72：已合并，辛苦了。」；没读到是谁时写标题。 */
export function saidLine(item: NewsItem): string {
  if (!item.who) return `${ref(item)} ${item.title}`;
  return `${item.who} 在 ${ref(item)}${item.text ? `：${item.text}` : ` ${item.what}`}`;
}
