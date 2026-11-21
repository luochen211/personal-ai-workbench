// 「收件箱」页的判断：通知、你开的 PR、登记项目的 open issue 合成一行一条（按 owner/repo#编号 去重），
// 按筛选（收件箱即全部；待你回、待动手、Agent 处理中、等对方、待你看、搁置、记录、我发的 PR）分组，按范围（某个仓库）收窄，
// 左列的条数、哪些行能交给 Agent、排序、相对时间、每行那一句话、网址（含旧的消息、Issue、待你处理页的网址）。
// issue 的筛选取后端合并后的类别（next.bucket，见 personal/next_step.py），同一条有 issue 那一行时以它为准；
// 没有 issue 那一行的 PR 按谁最后说话（pr_feedback.state_of）。
// 不写 JSX，node --test 可以直接加载这个文件。
import { isDone } from "./messages.ts";  // 带扩展名：node --test 直接加载
import { waiting } from "./handoff.ts";
import { parseHome } from "./catalog.ts";
import { draftLine, withDrafts } from "./DraftParts.ts";
import type { SubjectState } from "./messages";
import type { FeedRow, Issue, NextStep, PrReport, PrRow, ReplyDraft, ReplyHint, ReplyMode, RowState, Settled, Snooze, SnoozeWait, WechatRow } from "./api";

export interface Message {
  id: string; repository: string; title: string; type: string;
  category: string; reason: string; unread: boolean; actionable?: boolean; updated_at: string; url: string;
  state?: SubjectState; done?: boolean;
  classification: "workspace" | "paused" | "archived" | "other" | "ambiguous";
  project_id: string | null; project_name: string | null; candidates: { id: string; name: string }[];
  // reason_code：GitHub 原始原因；number：issue / PR 编号；
  // new：上次读过之后别人新留的条数（不明为 null，没读到不给）
  reason_code?: string; number?: number | null; new?: number | null;
}

/** 交给 Agent 走哪个接口：你开的 PR 走 /api/prs/handoff。 */
export type Route = "prs";
/** 一条 PR 的处理情况：你开的 PR 的行（补上 kind）。 */
export type Row = FeedRow & { route: Route };

/** 页面一行：一条 GitHub 通知，或只在处理情况里、没有通知的 PR / issue；两边都有就合成一行。 */
export interface Thread {
  key: string; repository: string; number: number | null; kind: "pr" | "issue" | "other" | "wechat";
  title: string; url: string; unread: boolean; updated_at: string;
  done: boolean; state?: SubjectState; classification: Message["classification"];
  project_id: string | null; project_name: string | null;
  // status：按谁最后说话分的标签，没有处理情况的通知为 null（只在「全部」里）；
  // newCount：你最后一次动作之后对方新增的条数，算不出为 null
  status: RowState | null; newCount: number | null; mine: boolean;
  // pr：你开的 PR 的处理情况（没有 issue 那一行时按它分类）
  message?: Message; pr?: Row;
  // issue：登记项目里这条 open issue（带下一步归类）；category：落在哪个筛选，null 只在收件箱（和「我发的 PR」）里
  issue?: Issue; category?: Tab | null;
  // draft：Agent 写好、等你确认发送的回复草稿；有草稿的行归「草稿待确认」
  draft?: ReplyDraft;
  // wechat：微信的一行（WechatParts.wechatThreads），没有仓库和编号，只在「微信」范围和各筛选里
  wechat?: WechatRow;
}

/** 筛选：all 是收件箱（全部），mine 是我发的 PR（你开着的 PR，只看不交），其余是类别。 */
export type Tab = "all" | "reply" | "draft" | "todo" | "agent" | "wait" | "look" | "hold" | "record" | "mine";
export type Bucket = "reply" | "todo" | "agent" | "wait" | "look" | "hold" | "record";
/** 左列底部还有一处不是行的筛选：近 30 天已完成。 */
export type Choice = Tab | "done";
export type ReadMode = "all" | "unread";
export type Sort = "newest" | "oldest";

/** 左列（窄屏是顶部一排标签）的顺序：收件箱是全部，下面是筛选；默认选中待你回。 */
export const TABS: { key: Tab; label: string; empty: string }[] = [
  { key: "all", label: "收件箱", empty: "没有符合条件的条目" },
  { key: "reply", label: "待你回", empty: "没有等你回的" },
  // 草稿待确认：「帮我回复」后 Agent 写好的回复草稿，你确认后才发；计入角标
  { key: "draft", label: "草稿待确认", empty: "没有待确认的回复草稿" },
  { key: "todo", label: "待动手", empty: "没有待动手的 issue" },
  { key: "agent", label: "Agent 处理中", empty: "没有 Agent 在处理的" },
  { key: "wait", label: "等对方", empty: "没有等对方的" },
  { key: "look", label: "待你看", empty: "没有待你看的" },
  // 搁置、记录：不出交给按钮，不计入角标；行上那一句写原因
  { key: "hold", label: "搁置", empty: "没有搁置的 issue" },
  { key: "record", label: "记录", empty: "没有记录类的 issue" },
  { key: "mine", label: "我发的 PR", empty: "没有你开着的 PR" },
];
/** 左列筛选分三组：要你处理的在上，进行中的居中，不用管的在下。TABS 里新加、这里没列的筛选归第一组。 */
export const SIDE_GROUPS: { title: string; keys: Tab[] }[] = [
  { title: "要你处理", keys: ["reply", "draft", "todo", "look"] },
  { title: "进行中", keys: ["agent", "wait"] },
  { title: "不用管", keys: ["mine", "hold", "record"] },
];
export const sideGroups = () => {
  const listed = new Set(SIDE_GROUPS.flatMap(g => g.keys));
  const extra = TABS.filter(t => t.key !== "all" && !listed.has(t.key)).map(t => t.key);
  return SIDE_GROUPS.map((g, i) => ({ ...g, keys: i === 0 ? [...g.keys, ...extra] : g.keys }));
};
export const DONE_LABEL = "近 30 天已完成";
const PICKS: Choice[] = [...TABS.map(t => t.key), "done"];
/** 能勾选、批量交给 Agent 的筛选。 */
export const HANDING: Tab[] = ["reply", "todo"];
export const tabLabel = (key: Tab) => TABS.find(t => t.key === key)!.label;

const NEEDS_YOU = ["等你决定", "等你看"];
/** issue 落在哪个筛选：后端给了 next.bucket 就用；旧后端没给时照 next_step.bucket 推。 */
export function issueBucket(i: Pick<Issue, "next" | "stage" | "stalled">): Bucket {
  if (i.next.bucket) return i.next.bucket;
  if (i.next.key === "flow") return NEEDS_YOU.includes(i.stage) || (i.stage === "已交给 Agent" && !!i.stalled) ? "reply" : "agent";
  if (i.next.key === "seen" && i.next.new) return "reply";  // Agent 看过、说不用动，对方的话你还没回
  const merged: Partial<Record<NextStep["key"], Bucket>> = { pr: "wait", close: "wait", decide: "reply", seen: "look" };
  return merged[i.next.key] ?? i.next.key as Bucket;
}

const kindOf = (type: string): Thread["kind"] => type === "PullRequest" ? "pr" : type === "Issue" ? "issue" : "other";
const numberFrom = (url: string) => { const m = url.match(/\/(?:issues|pull)\/(\d+)$/); return m ? Number(m[1]) : null; };
const latest = (...values: (string | null | undefined)[]) =>
  values.filter((v): v is string => !!v && !Number.isNaN(Date.parse(v))).sort((a, b) => Date.parse(b) - Date.parse(a))[0] ?? "";

/** 一行的标签。后端给了 state 就用；旧后端没给时照后端 pr_feedback.state_of 推：要你做的里在派或在处理的是 Agent 处理中，
 *  其余是待你回；没要你做的开着就是等对方，否则 done。 */
export function rowState(r: Pick<PrRow, "section" | "stage" | "stalled" | "open" | "state">): RowState {
  if (r.state) return r.state;
  if (r.section === "active") return waiting(r) ? "agent" : "reply";
  return r.open ? "wait" : "done";
}

/** 你开的 PR 的行（补上 kind 和交给的接口），同一条只留一行。 */
export function agentRows(prs: Pick<PrReport, "prs"> | null | undefined): Row[] {
  const out = new Map<string, Row>();
  for (const r of prs?.prs ?? []) {
    const key = r.subject.toLowerCase();
    if (!out.has(key)) out.set(key, { ...r, kind: "pr", route: "prs" });
  }
  return [...out.values()];
}

/** 导航上的数：收件箱里「待你回」和「草稿待确认」的条数（issue 按合并后的类别，PR 按谁最后说话，同一条只算一次）。 */
export const replyCount = (threads: Pick<Thread, "category">[]) => threads.filter(t => t.category === "reply" || t.category === "draft").length;

/** 回复草稿挂到行上：有草稿的行归「草稿待确认」；收件箱里还没有这一行的（列表没读到）单独成一行。 */
export const attachDrafts = (threads: Thread[], drafts: ReplyDraft[]): Thread[] =>
  withDrafts(threads, drafts, (d): Thread => {
    const [repo, number] = d.subject.split("#");
    return { key: d.subject.toLowerCase(), repository: repo, number: Number(number), kind: d.kind, title: d.subject,
      url: `https://github.com/${repo}/${d.kind === "pr" ? "pull" : "issues"}/${number}`, unread: false, updated_at: d.created_at,
      done: false, classification: "workspace", project_id: null, project_name: null, status: null, newCount: null, mine: false, draft: d };
  }).map(t => t.draft && !snoozeOf(t) ? { ...t, category: "draft" as Tab, updated_at: latest(t.updated_at, t.draft.created_at) } : t);

/** 暂时不处理（后端 personal/snooze.py，只记本机）：要你处理的这几类（待你回、草稿待确认、待动手、待你看）能设，
 *  设了落「搁置」，对方有新动静、到点（选了 1 天后、1 周后）或点「恢复」回到原来的类别。 */
export const SNOOZABLE: Tab[] = ["reply", "draft", "todo", "look"];
export const SNOOZE_WAITS: { key: SnoozeWait; label: string }[] = [
  { key: "news", label: "有新动静再说" }, { key: "1d", label: "1 天后" }, { key: "1w", label: "1 周后" },
];
/** 这一行的暂时不处理记录（有 issue 那一行时只看它，和类别一致），没有是 null。 */
export const snoozeOf = (t: Pick<Thread, "issue" | "pr"> & { wechat?: WechatRow }): Snooze | null =>
  (t.wechat ? t.wechat.snooze : t.issue ? t.issue.snooze : t.pr?.snooze) ?? null;
/** 能不能设暂时不处理：在要你处理的这几类里，且有 issue、PR 或微信那一行（后端据它判断何时回来）。 */
export const canSnooze = (t: Pick<Thread, "issue" | "pr" | "category"> & { wechat?: WechatRow }) =>
  !!t.category && SNOOZABLE.includes(t.category) && !!(t.issue || t.pr || t.wechat) && !snoozeOf(t);
/** 设暂时不处理时送给后端的一条：编号和这一行读到的对方最后一条动静的时间，之后更晚的动静让它回来。 */
export const snoozeItem = (t: Pick<Thread, "issue" | "pr" | "key"> & { wechat?: WechatRow }) => t.wechat
  ? { subject: t.wechat.key, their_at: t.wechat.at }  // 微信：这个会话有更新的消息就回来
  : { subject: t.issue?.subject ?? t.pr?.subject ?? t.key, their_at: t.issue ? t.issue.their_at ?? "" : t.pr?.their_last || t.pr?.their_at || "" };
export const snoozeLabel = (ticked: number) => `暂时不处理（勾选 ${ticked} 条）`;

/** 列表工具条上交给按钮的字：待你回里有两种（mode）——「帮我回复」只写回复草稿，「处理并回复」改代码后再写回复草稿，
 *  草稿都等你确认后才发；待动手里是「交给 Agent」（改代码）。 */
export function handLabels(tab: Tab, ticked: number, total: number, mode: ReplyMode = "reply") {
  if (tab !== "reply") return { some: `交给 Agent（勾选 ${ticked} 条）`, all: `全部交给 Agent（${total} 条）`, busy: "交给中…", title: "交给 Agent", ok: `交给 ${ticked} 条` };
  const name = MODE_NAMES[mode];
  return { some: `${name}（勾选 ${ticked} 条）`, all: `全部${name}（${total} 条）`, busy: "提交中…", title: name, ok: `${name} ${ticked} 条` };
}

/** 待你回的两种交给（personal/reply_mode.py）：按钮上的字，和详情里按钮下面那一句。 */
export const MODES: ReplyMode[] = ["reply", "handle"];
export const MODE_NAMES: Record<ReplyMode, string> = { reply: "帮我回复", handle: "处理并回复" };
export const MODE_NOTE = "「帮我回复」只写回复草稿，不改代码、不推送、不开或关 PR；「处理并回复」按留言改代码、推送或开 PR，再写回复草稿。草稿都放进「草稿待确认」，你看过、改过、点「确认发送」才用你的账号发出。";

/** 交给接口的请求体带上模式：待你回里点的那一种；待动手的「交给 Agent」不带（后端按处理走）。 */
export const withMode = <T extends object>(body: T, mode?: ReplyMode): T & { mode?: ReplyMode } => mode ? { ...body, mode } : body;

const HINT_LABELS: Record<ReplyMode, string> = { reply: "只需回复", handle: "需要处理" };
/** 待你回那一行的建议：只需回复还是需要处理。后端按对方最后几条判（personal/reply_mode.py）；没给时：「处理 issue」流程里
 *  等你决定、等你看的是需要处理（只回复不适用），其余判不准算只需回复。不在待你回的是 null。 */
export function replyHint(t: Pick<Thread, "category" | "issue" | "pr">): ReplyHint | null {
  if (t.category !== "reply" || t.issue?.next.settled || (!t.issue && t.pr?.settled)) return null;  // 待你定：不出交给按钮，也不给建议
  const given = t.issue?.hint ?? t.pr?.hint;
  if (given) return { ...given, label: HINT_LABELS[given.mode] };
  if (t.issue && t.issue.next.key === "flow" && t.issue.next.action !== "feedback") return { mode: "handle", label: HINT_LABELS.handle, why: "「处理 issue」流程里的，再交一轮按处理走" };
  return { mode: "reply", label: HINT_LABELS.reply, why: "判不准，默认只需回复" };
}

/** 没有 issue 那一行时，PR 的标签落在哪个筛选：待你回、Agent 处理中、搁置同名；
 *  在等对方时只在「我发的 PR」里（只看不交）；已合并或关闭的只在收件箱里。 */
const categoryOf = (status: RowState | null): Bucket | null =>
  status === "reply" || status === "agent" || status === "hold" ? status : null;

/** 合成页面的行：PR 的处理情况按 owner/repo#编号挂到同一主题的消息上，没有消息的单独成一行。 */
export function mergeThreads(messages: Message[], { prs = null }: { prs?: PrReport | null } = {}): Thread[] {
  const rows = new Map(agentRows(prs).map(r => [r.subject.toLowerCase(), r]));
  const fromRow = (r: Row) => ({
    status: rowState(r), category: categoryOf(rowState(r)), newCount: r.new > 0 && ["reply", "agent"].includes(rowState(r)) ? r.new : null,
    mine: true,  // /api/prs/feedback 只列你开的 PR
  });
  const out: Thread[] = messages.map(m => {
    const number = m.number ?? (kindOf(m.type) === "other" ? null : numberFrom(m.url));
    const key = number !== null ? `${m.repository.toLowerCase()}#${number}` : `n:${m.id}`;
    const row = rows.get(key);
    rows.delete(key);
    return {
      key, repository: m.repository, number, kind: kindOf(m.type), title: m.title, url: m.url, unread: m.unread,
      updated_at: row ? latest(m.updated_at, row.their_at, row.mine_at, row.handed_at, row.run?.finished_at) : m.updated_at,
      done: isDone(m), state: m.state, classification: m.classification, project_id: m.project_id, project_name: m.project_name,
      ...(row ? fromRow(row) : { status: null, category: null, newCount: null, mine: false }),
      message: m, pr: row,
    };
  });
  for (const [key, row] of rows) out.push({
    key, repository: row.repo, number: row.number, kind: row.kind, title: row.title, url: row.url, unread: false,
    updated_at: latest(row.their_at, row.mine_at, row.handed_at, row.run?.finished_at), done: rowState(row) === "done",
    state: row.open ? "open" : undefined, classification: "workspace", project_id: row.project_id || null, project_name: row.project_name || null,
    ...fromRow(row), pr: row,
  });
  return out;
}

/** issue 那一行的「+N」：别人在你最后一次动作之后说了几条（后端 next.new），只在待你回和 Agent 处理中标。 */
const issueNew = (issue: Issue, category: Bucket) => issue.next.new && ["reply", "agent"].includes(category) ? issue.next.new : null;

/** 登记项目的 open issue 并进来：同一条（owner/repo#编号）已有通知或处理情况的行，就挂到那一行上，类别改用 issue 的方案二类别；
 *  没有的单独成一行。login 是你的账号。 */
export function withIssues(threads: Thread[], issues: Issue[], { login = "" }: { login?: string } = {}): Thread[] {
  const me = login.toLowerCase();
  const byKey = new Map(issues.map(i => [i.subject.toLowerCase(), i]));
  const out = threads.map(t => {
    const issue = byKey.get(t.key);
    if (!issue) return t;
    byKey.delete(t.key);
    const category = issueBucket(issue);
    return { ...t, kind: "issue" as const, issue, category, newCount: issueNew(issue, category) ?? t.newCount, updated_at: latest(t.updated_at, issue.updated_at),
      project_id: t.project_id ?? issue.project_id, project_name: t.project_name ?? issue.project_name, state: t.state ?? "open" };
  });
  for (const [key, issue] of byKey) {
    const category = issueBucket(issue);
    out.push({
      key, repository: issue.repo, number: issue.number, kind: "issue", title: issue.title, url: issue.url, unread: false,
      updated_at: issue.updated_at, done: false, state: "open", classification: "workspace", project_id: issue.project_id, project_name: issue.project_name,
      status: null, newCount: issueNew(issue, category), mine: !!me && issue.author.toLowerCase() === me,
      issue, category,
    });
  }
  return out;
}

/** 这个筛选里有没有它。CI、发布这类不是 issue / PR 的通知只在收件箱里；看未读时已归档项目的不列。 */
export function inTab(t: Thread, tab: Tab, read: ReadMode = "all") {
  if (tab === "mine") return mineOpenPr(t);
  if (tab !== "all") return t.category === tab;
  return read === "all" || (t.unread && t.classification !== "archived");
}

/** 「我发的 PR」：你开着的 PR（自己的和上游的），只看不交，不计入角标。 */
export const mineOpenPr = (t: Pick<Thread, "kind" | "mine" | "pr" | "status">) => t.kind === "pr" && t.mine && !!t.pr && t.pr.open && t.status !== "done";

/** 这一行能不能在列表上勾选、批量交给 Agent，走哪条接口：只有待动手、待你回的行。
 *  issue 走 /api/issues/handoff（后端逐条按同一归类分流：待动手打 agent 标签，待你回走 issue 留言（不论哪个仓库），Agent 等你决定的再交一轮）；
 *  没有 issue 那一行的待你回 PR 走 /api/prs/handoff。已交给、还在派或在处理的不能交，是 null。 */
export type Via = "issue" | Route;
export function handVia(t: Pick<Thread, "issue" | "pr" | "status" | "category">): Via | null {
  if (!t.category || !HANDING.includes(t.category)) return null;
  if (t.issue) {
    const n = t.issue.next;
    if (n.key === "flow") return waiting(t.issue) ? null : "issue";
    return !!n.button && !n.drawer_only ? "issue" : null;
  }
  if (t.pr && t.status === "reply" && !waiting(t.pr) && !t.pr.settled) return t.pr.route;  // Agent 上一轮已有结论的只在详情里「仍交给 Agent」
  return null;
}

/** 一次交给超过这么多条先在页面里确认一次：中台最多同时跑 10 个，其余排队。 */
export const CONFIRM_OVER = 10;
export const needsConfirm = (count: number) => count > CONFIRM_OVER;
export const confirmText = (count: number, limit = CONFIRM_OVER, verb = "交给 Agent") =>
  `一次${verb} ${count} 条。中台最多同时跑 ${limit} 个，其余 ${Math.max(0, count - limit)} 条排队，按顺序派出。`;

/** 范围「微信」（只在配置了微信时出现）：网址里写 s=wechat。 */
export const WECHAT_SCOPE = "wechat";
/** 范围：空是全部，wechat 是微信，带斜杠的是某个仓库。 */
export function inScope(t: Thread, scope: string) {
  if (!scope) return true;
  if (scope === WECHAT_SCOPE) return t.kind === "wechat";
  return !!t.repository && t.repository.toLowerCase() === scope.toLowerCase();
}

export const searchText = (t: Thread) => t.wechat ? `${t.wechat.chat} ${t.wechat.label} 微信 ${t.wechat.text.join(" ")}`.toLowerCase() : `${t.title} ${t.repository} ${t.number !== null ? `#${t.number}` : ""} ${t.project_name ?? ""} ${(t.issue?.labels ?? []).join(" ")}`.toLowerCase();

/** 各仓库的条数，最近有消息的仓库在前。 */
export function repoCounts(rows: Pick<Thread, "repository" | "updated_at">[]) {
  const found = new Map<string, { repo: string; count: number; latest: string }>();
  for (const t of rows) {
    if (!t.repository.includes("/")) continue;  // 微信的行没有仓库，单独计数
    const key = t.repository.toLowerCase();
    const entry = found.get(key) ?? { repo: t.repository, count: 0, latest: "" };
    entry.count += 1;
    if (t.updated_at > entry.latest) entry.latest = t.updated_at;
    found.set(key, entry);
  }
  return [...found.values()].sort((a, b) => b.latest.localeCompare(a.latest) || a.repo.localeCompare(b.repo));
}

/** 左列的条数。收件箱和各筛选：当前范围里各有几条；各仓库：当前筛选里各有几条（选中的仓库没有消息也留着，写 0）。
 *  传进来的行已经按搜索筛过。 */
export function sideCounts(rows: Thread[], { tab, scope, read = "all" }: { tab: Tab; scope: string; read?: ReadMode }) {
  const tabs = Object.fromEntries(TABS.map(t => [t.key, rows.filter(r => inScope(r, scope) && inTab(r, t.key, read)).length])) as Record<Tab, number>;
  const here = rows.filter(r => inTab(r, tab, read));
  const repos = repoCounts(here);
  if (scope.includes("/") && !repos.some(r => r.repo.toLowerCase() === scope.toLowerCase())) repos.push({ repo: scope, count: 0, latest: "" });
  return { tabs, repos, wechat: here.filter(r => r.kind === "wechat").length };
}

/** 「近 30 天已完成」在不在当前范围：带斜杠的是那个仓库。 */
export function doneInScope(d: { repo: string }, scope: string) {
  if (!scope) return true;
  return d.repo.toLowerCase() === scope.toLowerCase();
}

/** 仓库对应的登记项目：issue 仓库或代码仓库是它的；都不是时看行上带的项目 id。没有是 null。 */
export function projectOfRepo<P extends { id: string; issue_repository: string; repository: string }>(repo: string, projects: P[], rows: Pick<Thread, "repository" | "project_id">[] = []): P | null {
  const low = repo.toLowerCase();
  const direct = projects.find(p => [p.issue_repository, p.repository].some(r => !!r && r.toLowerCase() === low));
  if (direct) return direct;
  const id = rows.find(t => t.repository.toLowerCase() === low && t.project_id)?.project_id;
  return projects.find(p => p.id === id) ?? null;
}

export function sortThreads(rows: Thread[], sort: Sort) {
  const sign = sort === "oldest" ? -1 : 1;
  return rows.slice().sort((a, b) => sign * ((Date.parse(b.updated_at) || 0) - (Date.parse(a.updated_at) || 0)));
}

const DAY = 86400000;
const startOfDay = (t: number) => { const d = new Date(t); d.setHours(0, 0, 0, 0); return d.getTime(); };

/** 右侧的相对时间：刚刚、N 分钟前、N 小时前、昨天、N 天前，再早写月日。 */
export function ago(value: string, now = Date.now()) {
  const at = Date.parse(value);
  if (!value || Number.isNaN(at)) return "—";
  const minutes = Math.max(0, Math.floor((now - at) / 60000));
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  if (minutes < 24 * 60 && at >= startOfDay(now)) return `${Math.floor(minutes / 60)} 小时前`;
  const days = Math.round((startOfDay(now) - startOfDay(at)) / DAY);
  if (days <= 1) return "昨天";
  if (days < 7) return `${days} 天前`;
  const d = new Date(at);
  return `${d.getMonth() + 1}月${d.getDate()}日`;
}

/** 那一句话里的时刻：今天写 10:41，别的天写 11-17 15:58。 */
export function clock(value: string | null | undefined, now = Date.now()) {
  const at = Date.parse(value ?? "");
  if (Number.isNaN(at)) return "";
  const d = new Date(at), pad = (n: number) => String(n).padStart(2, "0");
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return at >= startOfDay(now) && at < startOfDay(now) + DAY ? time : `${d.getMonth() + 1}-${d.getDate()} ${time}`;
}

/** Agent 上一轮已有结论、之后没有新动静（personal/settled.py）的那一句。 */
export const settledLine = (s: Settled) => s.kind === "decide" ? `Agent 要你定：${s.line}` : `Agent 看过，不用动：${s.line}`;

/** Agent 这一轮做完的结果，接在等对方那一句后面。 */
function agentDone(r: Row) {
  const result = r.run?.result;
  if (!result || r.stage !== "已处理") return "";
  if (result.kind === "pr") return `Agent 开了 PR ${result.prs.map(n => `#${n}`).join(" ")}`;
  return ({ pushed: "Agent 已推送", closed: "Agent 已关闭", replied: "Agent 已写回复", no_action: "Agent：不用动" } as Record<string, string>)[result.kind] ?? "";
}

/** 行上状态那一句话：issue 是归类给的那一句（等对方的写清是等 PR 评审、等验收关闭还是等对方回）；Agent 等你决定、
 *  等你看的写 Agent 问了什么；PR 的行写谁什么时候说了话、Agent 到哪一步了。没有处理情况的通知是空的。 */
export function statusLine(t: Thread, now = Date.now()) {
  const held = snoozeOf(t);
  if (held) return held.line;
  if (t.wechat) return t.wechat.text.join(" ");
  if (t.draft) return draftLine(t.draft);
  if (t.issue) {
    const i = t.issue;
    if (i.next.key !== "flow" || t.category !== "reply") return i.next.line;
    if (i.stage === "已交给 Agent") return i.stalled === "lost" ? "交给后 15 分钟没动静，派发可能丢了" : "只有 agent 标签，还没派出";
    const asks = i.waiting.filter(w => w.kind === "ask" || w.tier === "ask");
    if (i.stage === "等你决定") return asks.length ? `Agent 问你：${asks.map(w => w.title.replace(/^\s*【[^】]*】\s*/, "")).join("；")}` : `Agent 卡住了：${i.run?.result?.reason || "等你决定"}`;
    return "Agent 交回结果，等你看";
  }
  const r = t.pr;
  if (!r || !t.status) return "";
  const at = (v: string | null | undefined) => clock(v, now);
  const only = r.mode === "reply" ? "（只回复）" : "";
  if (t.status === "agent") return r.stage === "处理中" ? `Agent 正在处理${only}` : `${at(r.handed_at)} 交给 Agent${only}，等派出`;
  if (t.status === "reply") {
    const heard = r.said.find(x => x.new);
    // Agent 看过、说不用动，对方的话你还没回：写对方那句和 Agent 的结论
    if (r.settled) return r.settled.kind === "nothing" && heard ? `${heard.who} ${at(heard.at)} ${heard.what} · ${settledLine(r.settled)}` : settledLine(r.settled);
    if (r.stage === "已交给 Agent") return "交给后 15 分钟没动静，派发可能丢了";
    if (r.stage === "等你决定") return r.waiting.some(w => w.kind === "ask") ? "Agent 有问题等你决定" : `Agent 卡住了：${r.run?.result?.reason || "等你决定"}`;
    if (r.stage === "等你看") return r.unseen ? r.run?.result?.kind === "pushed" ? "Agent 说已推送，GitHub 上还没看到，去看一眼" : "Agent 说写了回复，没找到草稿，GitHub 上也没看到，去看一眼" : "Agent 回执不全，去看一眼";
    const s = r.said.find(x => x.new) ?? r.said[0];
    return s ? `${s.who} ${at(s.at)} ${s.what} · 你还没回` : "对方回了 · 你还没回";
  }
  const head = t.mine ? "你开的 PR" : "";
  const last = r.said[0];
  const them = !!r.their_at && Date.parse(r.their_at) > (Date.parse(r.mine_at) || 0);
  const said = them && last ? `${last.who} ${at(r.their_at)} ${last.what}` : r.mine_at ? `你 ${at(r.mine_at)} 回过` : "还没人回";
  const parts = [head, t.status === "done" ? "已合并或关闭" : "", r.kind === "pr" && t.mine && !r.their_at ? "还没人回" : said, agentDone(r)];
  return [...new Set(parts.filter(Boolean))].join(" · ");
}

/** 列表行上那一句里和类别同义的字：行的类别已经写在行前的标记或选中的筛选上，句子里不再说一遍。 */
const ECHO: Partial<Record<Tab, [RegExp, string][]>> = {
  reply: [[/\s*[，·]\s*你还没回$/, ""]],
  draft: [[/，等你确认发送/, ""]],
  agent: [[/^处理中(?:\s*·\s*|$)/, ""], [/^Agent 正在处理（只回复）$/, "只回复"], [/^Agent 正在处理$/, ""]],
  wait: [[/^等对方[：:]\s*/, ""]],
  record: [[/，是记录$/, ""]],
};
/** 列表行上那一句：statusLine 去掉和类别同义的字；「我发的 PR」里不再写「你开的 PR」。详情里用完整的 statusLine。 */
export function rowLine(t: Thread, tab: Tab, now = Date.now()) {
  let text = statusLine(t, now);
  for (const [pattern, to] of (t.category ? ECHO[t.category] : undefined) ?? []) text = text.replace(pattern, to);
  if (tab === "mine") text = text.replace(/^你开的 PR(?:\s*·\s*|$)/, "");
  return text.trim();
}

/** 收件箱的网址：#/inbox，筛选不是待你回时带 f，范围带 s（owner/repo），旧的按项目网址带 p（项目 id，页面读到项目后换成仓库）。
 *  不是收件箱的网址返回 null。 */
export interface InboxRoute { tab: Choice; scope: string; project: string }
export function parseInboxHash(hash: string): InboxRoute | null {
  const m = hash.match(/^#\/inbox(?:\?(.*))?$/);
  if (!m) return null;
  const query = new URLSearchParams(m[1] ?? "");
  const f = query.get("f") ?? "", s = query.get("s") ?? "";
  return { tab: PICKS.includes(f as Choice) ? f as Choice : "reply", scope: s.includes("/") || s === WECHAT_SCOPE ? s : "", project: query.get("p") ?? "" };
}

export function inboxHash({ tab = "reply", scope = "", project = "" }: Partial<InboxRoute> = {}) {
  const query = new URLSearchParams();
  if (tab !== "reply") query.set("f", tab);
  if (scope) query.set("s", scope);
  if (project) query.set("p", project);
  const text = query.toString().replace(/%2F/g, "/");
  return text ? `#/inbox?${text}` : "#/inbox";
}

/** 并进收件箱的旧网址换成新的；不是旧网址返回 null。
 *  主页 #/、「待你处理」#/todo、「PR 待处理」#/prs 进待你回；
 *  消息页 #/messages?t=&s=（还有更早的 ?r=owner/repo）进同名筛选和范围；
 *  原 Issue 列表 #/issues、#/?v=issue（带 p 时只看那个项目）和「按项目」#/?v=project、#/?g= 进收件箱（全部）。 */
export function legacyInboxHash(hash: string): string | null {
  if (hash === "#/prs" || hash === "#/todo") return inboxHash();
  const m = hash.match(/^#\/messages(?:\?(.*))?$/);
  if (m) {
    const query = new URLSearchParams(m[1] ?? "");
    const t = query.get("t") ?? "";
    const s = query.get("s") ?? query.get("r") ?? "";
    return inboxHash({ tab: TABS.some(x => x.key === t) ? t as Tab : "reply", scope: s.includes("/") ? s : "" });
  }
  const home = parseHome(hash);
  if (!home) return null;
  if (/^#?\/?$/.test(hash)) return inboxHash();
  return inboxHash({ tab: "all", project: home.mode === "issue" ? home.project : "" });
}

/** 详情顶上仓库名后面的项目链接：项目名就是仓库名（career-ops-hq/career-ops 对 career-ops）时只写「项目详情 →」，不同才带上项目名。 */
export const projectLinkText = (name: string | null | undefined, repository: string) =>
  !name || name.toLowerCase() === repository.split("/").pop()?.toLowerCase() ? "项目详情 →" : `${name} · 项目详情 →`;
