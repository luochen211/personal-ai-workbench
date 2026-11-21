import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import Confirm from "./Confirm";
import Modal from "./Modal";
import { request } from "./api";
import type { IssueDraftsReport } from "./api";
import type { AgentsReport, BatchHandoff, DoneReport, FeedHandoff, Handoff, Issue, Payload, PrHandoff, PrReport, ReplyMode, SendResult, SnoozeWait, WechatReport, WechatRow } from "./api";
import { DraftPanel } from "./Drafts";
import { NeedsButton, NeedsDrawer } from "./IssueDrafts";
import { showNeeds } from "./IssueDraftParts";
import { sameEvent, sendConfirmText, sendLine } from "./DraftParts";
import Markdown from "./markdown";
import { StateTag } from "./messages";
import { batches, handoffNotice, polling } from "./AgentParts";
import { AgentStatus, FeedDetail } from "./Agents";
import { classTone } from "./NextStep";
import { stuck } from "./handoff";
import { NO_NOTE, noteFor } from "./IssueParts";
import type { Note } from "./IssueParts";
import { AllDoneList, reopenedOut } from "./ProjectParts";
import { CONFIRM_OVER, DONE_LABEL, HANDING, MODES, MODE_NAMES, MODE_NOTE, SNOOZABLE, TABS, WECHAT_SCOPE, withMode, ago, canSnooze, confirmText, doneInScope, handLabels, handVia, inScope, inTab, inboxHash, mergeThreads, needsConfirm, parseInboxHash,
  projectLinkText, projectOfRepo, replyHint, repoCounts, rowLine, searchText, settledLine, sideCounts, sideGroups, snoozeItem, snoozeLabel, snoozeOf, sortThreads, tabLabel } from "./InboxParts";
import { SnoozeButton, SnoozeSection, snooze } from "./Snooze";
import type { Choice, Message, ReadMode, Sort, Tab, Thread } from "./InboxParts";
import { WechatDetail, WechatIcon } from "./Wechat";
import { doneItem, freshness, signalText } from "./WechatParts";

interface Report { account: string | null; synced_at: string | null; notifications: Message[]; state_note?: string; digest?: import("./messages").Digest }
interface Comment { author: string; body: string; created_at: string }
interface Content { notification: Message; body: string; author: string | null; url: string; created_at?: string; updated_at?: string; content_kind: string; body_error: string | null; comments?: Comment[]; comment_error?: string | null }
export interface AgentsFeed { report: AgentsReport | null; failed: string; reload: () => Promise<void> }
export interface PrFeed { report: PrReport | null; failed: string; reload: (force?: boolean) => Promise<void> }
const errorText = (e: unknown) => e instanceof Error ? e.message : "GitHub 消息读取失败";
const when = (value: string | null | undefined) => value ? new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";
const SIZE = 50;
const PR_BATCH = 20;  // /api/prs/handoff 一次最多收这么多条
const ISSUE_BATCH = 20;  // /api/issues/handoff 每次送这么多条（后端一次最多 50 条）

export function useGithubMessages(online: boolean) {
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!online) return;
    let current = true;
    setLoading(true);
    void (async () => {
      try {
        const cached = await request<Report>("/api/github/notifications");
        if (current) setReport(cached);
        if (cached.synced_at && Date.now() - new Date(cached.synced_at).getTime() < 300000) return;
        const fresh = await request<Report>("/api/github/notifications/sync", {});
        if (current) { setReport(fresh); setError(""); }
      } catch (e) { if (current) setError(errorText(e)); }
      finally { if (current) setLoading(false); }
    })();
    return () => { current = false; };
  }, [online]);
  // 页面定时刷新时调用：读本机缓存；上次同步超过 5 分钟就再同步一次，GitHub 那边的新消息和已读状态跟上来
  const reload = async () => {
    try {
      const cached = await request<Report>("/api/github/notifications");
      setReport(cached);
      if (cached.synced_at && Date.now() - new Date(cached.synced_at).getTime() < 300000) return;
      setReport(await request<Report>("/api/github/notifications/sync", {})); setError("");
    } catch (e) { setError(errorText(e)); }
  };
  const sync = async () => {
    setLoading(true); setError("");
    try { setReport(await request<Report>("/api/github/notifications/sync", {})); }
    catch (e) { setError(errorText(e)); }
    finally { setLoading(false); }
  };
  return { report, loading, error, sync, reload };
}

// issue / PR / 其他（CI、发布……）的小图标；颜色按主题状态：开着的绿、已合并和已完成的蓝、关闭的灰
function KindIcon({ thread }: { thread: Thread }) {
  const state = thread.state || (thread.pr?.open ? "open" : "");
  const tone = state === "open" ? "open" : state === "merged" || state === "completed" ? "done" : "plain";
  const label = thread.kind === "pr" ? "PR" : thread.kind === "issue" ? "issue" : "其他通知";
  return <svg className={`kind-icon ${tone}`} viewBox="0 0 16 16" width="16" height="16" role="img" aria-label={label}>
    {thread.kind === "pr" ? <><circle cx="4" cy="3.5" r="1.8" /><circle cx="4" cy="12.5" r="1.8" /><circle cx="12" cy="12.5" r="1.8" />
      <path d="M4 5.3v5.4M12 10.7V6.5a2 2 0 0 0-2-2H7.5" /><path d="M9 3l-1.6 1.5L9 6" /></>
      : thread.kind === "issue" ? <><circle cx="8" cy="8" r="6" /><circle cx="8" cy="8" r="1.3" className="fill" /></>
      : <><rect x="2.5" y="2.5" width="11" height="11" rx="2.5" /><path d="M5.5 8h5" /></>}
  </svg>;
}

function InboxIcon() {
  return <svg className="kind-icon" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
    <path d="M2 9.5 3.6 3.2a1 1 0 0 1 1-.7h6.8a1 1 0 0 1 1 .7L14 9.5V13a.5.5 0 0 1-.5.5h-11A.5.5 0 0 1 2 13Z" /><path d="M2 9.5h3.5l1 1.5h3l1-1.5H14" />
  </svg>;
}

export default function Inbox({ online, feed, initialProject = "", onProject, agents, prs, threads = [], openIssues = [], done = null, doneError = "",
  data = null, wechat = null, issueDrafts = null, onIssue, onRefresh, onHanded, footer }: {
  online: boolean; feed: ReturnType<typeof useGithubMessages>; initialProject?: string;
  onProject: (id: string) => void; agents?: AgentsFeed; prs?: PrFeed;
  // 下面是收件箱用的（项目详情里的消息弹窗不传）：合好的行、open issue（「已完成」去掉重新打开的）、近 30 天已完成、项目登记
  threads?: Thread[]; openIssues?: Issue[]; done?: DoneReport | null; doneError?: string; data?: Payload | null;
  // wechat：可选的微信范围（没配置时 enabled 为 false，页面上不出现微信）；微信的行已经并进 threads
  wechat?: WechatReport | null;
  // issueDrafts：从微信拟的 issue 草稿、能选的项目和「读新需求」的状态；在工具条右端的「新需求」按钮和它打开的抽屉里，不进列表
  issueDrafts?: IssueDraftsReport | null;
  onIssue?: (issue: Issue) => void; onRefresh?: (force: boolean) => Promise<void>; onHanded?: (handed: [Issue, Handoff][]) => void; footer?: ReactNode;
}) {
  const { report, loading, error, sync } = feed;
  const compact = !!initialProject;  // 项目详情里的弹窗：只列这个项目的消息，不分筛选、不能交给
  const start = compact ? null : parseInboxHash(location.hash);
  const [tab, setTabState] = useState<Choice>(compact ? "all" : start?.tab ?? "reply");
  const [scope, setScopeState] = useState(start?.scope ?? "");
  const [pending, setPending] = useState(start?.project ?? "");  // 旧网址 #/?v=issue&p=<项目>：读到项目登记后换成它的仓库
  const [read, setRead] = useState<ReadMode>("all");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<Sort>("newest");
  const [page, setPage] = useState(1);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [asking, setAsking] = useState<{ keys: string[]; mode?: ReplyMode } | null>(null);  // 超过 10 条时等页面里的确认
  const [handing, setHanding] = useState<ReplyMode | "">("");  // 待你回里正在交的是哪种（按钮上写「提交中…」）
  const [sendAsking, setSendAsking] = useState<string[] | null>(null);  // 草稿待确认：批量确认发送前在页面里确认一次
  const [sent, setSent] = useState<{ subject: string; result: SendResult }[]>([]);  // 批量发送逐条的结果
  const [busy, setBusy] = useState("");
  const [actionError, setActionError] = useState("");
  // 提示带上说的是哪一条：列表上照常显示，消息详情里只显示这一条自己的（noteFor）
  const [note, setNote] = useState<Note>(NO_NOTE);
  const notice = note.text;
  const setNotice = (text: string, about = "") => setNote({ text, about });
  const [thread, setThread] = useState<Thread | null>(null);
  const [content, setContent] = useState<Content | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const selected = useRef<string | null>(null);
  const agentsReport = compact ? null : agents?.report ?? null;
  const prReport = compact ? null : prs?.report ?? null;
  const projects = data ? [...data.projects, ...data.archived ?? []] : [];
  const wechatOn = !compact && !!wechat?.enabled;
  // 「新需求」：启用了微信，或已经有草稿、读过一轮时才有这个按钮
  const needsOn = !compact && online && showNeeds(!!wechat?.enabled, issueDrafts);
  const [needsOpen, setNeedsOpen] = useState(false);
  // 换筛选或范围：勾选清掉，只对当前筛选下的行生效
  const setTab = (next: Choice) => { setTabState(next); setChosen(new Set()); };
  const setScope = (next: string) => { setScopeState(next); setChosen(new Set()); };

  useEffect(() => setPage(1), [tab, scope, read, search, sort]);
  useEffect(() => {
    if (!pending || !data) return;
    const found = projects.find(p => p.id === pending);
    const repo = found?.issue_repository || found?.repository || "";
    if (repo) setScopeState(repo);
    setPending("");
  }, [pending, data]);
  useEffect(() => {
    // 筛选和范围写进网址：刷新、后退回到同一处
    if (compact || !parseInboxHash(location.hash)) return;
    const target = inboxHash({ tab, scope, project: pending });
    if (location.hash !== target) history.replaceState(null, "", target);
  }, [compact, tab, scope, pending]);
  const agentsTracking = polling(agentsReport);
  useEffect(() => {
    // 有 Agent 在跑或排队：每 10 秒看一次计数（你开的 PR 和 issue 由 App 跟）
    if (compact || !agents || !online || !agentsTracking) return;
    const id = setInterval(() => void agents.reload(), 10000);
    return () => clearInterval(id);
  }, [compact, agents, online, agentsTracking]);
  // 弹窗里的行跟着最新数据换（交给后的阶段、Agent 的提问）
  const all = compact ? mergeThreads(report?.notifications ?? []) : threads;
  const current = thread && (all.find(t => t.key === thread.key) ?? thread);
  // 能勾的：要你处理的四类（待你回、草稿待确认、待动手、待你看）里能交给 Agent、能确认发送或能暂时不处理的行；列表刷新后勾选只留还能勾的
  // 交给只对能交的行（待动手、待你回），确认发送只对有草稿的行，暂时不处理对勾选的全部
  const drafting = !compact && tab === "draft";
  const ticking = !compact && SNOOZABLE.includes(tab as Tab);
  const handBar = ticking && (HANDING.includes(tab as Tab) || drafting);  // 这个筛选有交给或确认发送的按钮
  const canHand = (t: Thread) => handBar && (drafting ? !!t.draft : !!handVia(t));
  const canTick = (t: Thread) => ticking && (canHand(t) || canSnooze(t));
  const tickableAll = new Set(all.filter(canTick).map(t => t.key));
  const tickKey = [...tickableAll].sort().join(",");
  useEffect(() => setChosen(c => { const next = new Set([...c].filter(s => tickableAll.has(s))); return next.size === c.size ? c : next; }), [tickKey]);

  const open = async (t: Thread) => {
    if (t.issue && onIssue) { onIssue(t.issue); return; }  // issue 打开 issue 详情
    selected.current = t.key; setThread(t); setContent(null);
    const message = t.message;
    if (!message) return;
    setContent({ notification: message, body: "", author: null, url: message.url, content_kind: "summary", body_error: null });
    setDetailLoading(true);
    try {
      const result = await request<Content>(`/api/github/notifications/${message.id}`);
      if (selected.current === t.key) setContent(result);
    } catch (e) {
      if (selected.current === t.key) setContent(c => c && { ...c, body_error: errorText(e) });
    } finally { if (selected.current === t.key) setDetailLoading(false); }
  };
  const close = () => { selected.current = null; setThread(null); setContent(null); };

  const operate = async (name: string, action: () => Promise<unknown>) => {
    setBusy(name); setActionError(""); setNotice("");
    try { await action(); } catch (e) { setActionError(errorText(e)); } finally { setBusy(""); }
  };
  const reloadAll = (force = false) => Promise.all([agents?.reload(), prs?.reload(force), onRefresh?.(force)]);
  const handoff = (keys: string[], mode?: ReplyMode) => operate("handoff", async () => {
    // 交给 Agent：issue 一批送 /api/issues/handoff，后端逐条按同一归类分流（待动手打 agent 标签，待你回走 issue 留言（不论哪个仓库），
    // Agent 等你决定的再交一轮）并逐条给结果；没有 issue 那一行的、你开的 PR 走 /api/prs/handoff。
    // mode 是待你回里点的那一种：reply 帮我回复（只写回复草稿），handle 处理并回复；待动手的「交给 Agent」不带
    const byKey = new Map(all.map(t => [t.key, t]));
    setHanding(mode ?? "");
    const picked = keys.map(k => byKey.get(k)).filter((t): t is Thread => !!t && !!handVia(t));
    const results: FeedHandoff[] = [];
    const handed: [Issue, Handoff][] = [];
    try {
      const issues = new Map(picked.filter(t => handVia(t) === "issue").map(t => [t.issue!.subject, t.issue!]));
      for (const items of batches([...issues.keys()], ISSUE_BATCH)) {
        const result = await request<BatchHandoff>("/api/issues/handoff", withMode({ items }, mode));
        results.push({ handed: result.results.filter(r => r.ok).map(r => ({ subject: r.subject, kind: "issue" as const, handed_at: r.handed_at ?? "" })),
          skipped: result.results.filter(r => !r.ok).map(r => ({ subject: r.subject, reason: r.error })), routed: result.routed, error: result.error });
        for (const r of result.results) {
          const issue = issues.get(r.subject);
          if (r.ok && issue) handed.push([issue, { subject: r.subject, handed_at: r.handed_at ?? "", routed: result.routed, error: "", flow: r.flow || "issue" }]);
        }
      }
      for (const items of batches(picked.filter(t => handVia(t) === "prs").map(t => t.pr!.subject), PR_BATCH)) {
        const result = await request<PrHandoff>("/api/prs/handoff", withMode({ prs: items }, mode));
        results.push({ ...result, handed: result.handed.map(h => ({ ...h, kind: "pr" as const })) });
      }
    } finally {
      if (handed.length) onHanded?.(handed);
      setHanding("");
      if (results.length) { setChosen(new Set()); setNotice(handoffNotice(results, mode ?? ""), keys.length === 1 ? keys[0] : ""); await reloadAll(); }
    }
  });
  // 详情里单条交一个 PR：Agent 上一轮已有结论的「仍交给 Agent」（force），只回复那一轮说要改代码的「改用处理并回复」
  const handPr = (t: Thread, mode?: ReplyMode, force = false) => operate("handoff", async () => {
    setHanding(mode ?? "");
    try {
      const result = await request<PrHandoff>("/api/prs/handoff", withMode({ prs: [t.pr!.subject], ...(force ? { force } : {}) }, mode));
      setNotice(handoffNotice([{ ...result, handed: result.handed.map(h => ({ ...h, kind: "pr" as const })) }], mode ?? ""), t.key);
      await reloadAll();
    } finally { setHanding(""); }
  });
  // 草稿待确认：逐条确认发送（后端每条发之前重读讨论，对方又回了的不发），逐条显示结果
  const sendAll = (keys: string[]) => operate("send", async () => {
    const byKey = new Map(all.map(t => [t.key, t]));
    const subjects = keys.map(k => byKey.get(k)?.draft?.subject).filter((s): s is string => !!s);
    const out: { subject: string; result: SendResult }[] = [];
    setSent([]);
    try {
      for (const subject of subjects) {
        let result: SendResult;
        try { result = await request<SendResult>("/api/replies/send", { subject }); }
        catch (e) { result = { ok: false, sent: 0, error: errorText(e) }; }
        out.push({ subject, result });
        setSent([...out]);
      }
    } finally {
      setChosen(new Set());
      const ok = out.filter(x => x.result.ok).length;
      setNotice(`确认发送 ${out.length} 条草稿：${ok} 条已发出${out.length - ok ? `，${out.length - ok} 条没发（见下面）` : ""}。`);
      await reloadAll();
    }
  });
  // 微信「已处理」：只记本机 .personal/wechat-done.json，这个会话有更新的消息才回来；微信里不会有任何变化
  const markDone = (row: WechatRow) => operate("wechat-done", async () => {
    await request("/api/wechat/done", doneItem(row));
    close(); setNotice(`「${row.chat}」的${row.label}已标为已处理，这个会话有新消息会再出现。`); await reloadAll();
  });
  const draftChanged = (message: string) => { if (message) setNotice(message); void reloadAll().catch(e => setActionError(errorText(e))); };
  // 暂时不处理：勾选的行一起记进本机 .personal/snoozed.json（不写 GitHub），落「搁置」
  const snoozeAll = (keys: string[], wait: SnoozeWait) => operate("snooze", async () => {
    const byKey = new Map(all.map(t => [t.key, t]));
    const items = keys.map(k => byKey.get(k)).filter((t): t is Thread => !!t && canSnooze(t)).map(snoozeItem);
    if (!items.length) return;
    const message = await snooze(items, wait);
    setChosen(new Set()); setNotice(message); await reloadAll();
  });
  // 一次超过 10 条先在页面里确认（中台最多同时跑 10 个，其余排队）
  const ask = (keys: string[], mode?: ReplyMode) => { if (drafting) setSendAsking(keys); else if (needsConfirm(keys.length)) setAsking({ keys, mode }); else void handoff(keys, mode); };
  const answered = (message: string) => { setNotice(message); void reloadAll().catch(e => setActionError(errorText(e))); };

  const q = search.trim().toLowerCase();
  const listing = tab !== "done";
  const listTab: Tab = listing ? tab as Tab : "all";
  const readMode: ReadMode = tab === "all" ? read : "all";
  const searched = (compact ? all.filter(t => t.project_id === initialProject) : all).filter(t => !q || searchText(t).includes(q));
  const visible = compact ? searched : searched.filter(t => inScope(t, scope));
  const side = sideCounts(searched, { tab: listTab, scope, read: readMode });
  // 近 30 天已完成：当前项目里的，去掉重新打开的，按搜索和范围筛
  const activeIds = new Set((data?.projects ?? []).map(p => p.id));
  const doneSearched = done ? reopenedOut(done.issues, openIssues).filter(d => activeIds.has(d.project_id) && (!q || `${d.subject} ${d.title} ${d.project_name}`.toLowerCase().includes(q))) : [];
  const doneShown = doneSearched.filter(d => doneInScope(d, scope));
  const doneRepos = repoCounts(doneSearched.map(d => ({ repository: d.repo, updated_at: d.closed_at ?? "" })));
  if (scope.includes("/") && !doneRepos.some(r => r.repo.toLowerCase() === scope.toLowerCase())) doneRepos.push({ repo: scope, count: 0, latest: "" });
  const repos = tab === "done" ? doneRepos : side.repos;
  const inThisTab = visible.filter(t => inTab(t, listTab, readMode));
  const rows = sortThreads(inThisTab.filter(t => listTab !== "all" || t.kind !== "other"), sort);
  const others = sortThreads(inThisTab.filter(t => listTab === "all" && t.kind === "other"), sort);  // CI、发布这类，收进折叠的组
  const total = tab === "done" ? doneShown.length : rows.length;
  const pages = Math.max(1, Math.ceil(total / SIZE));
  const currentPage = Math.min(page, pages);
  const shown = rows.slice((currentPage - 1) * SIZE, currentPage * SIZE);
  const tickable = rows.filter(canTick).map(t => t.key);
  const ticked = tickable.filter(k => chosen.has(k));
  const allTicked = tickable.length > 0 && ticked.length === tickable.length;
  const someTicked = ticked.length > 0;
  const tickAll = () => setChosen(allTicked ? new Set() : new Set(tickable));
  // 交给、确认发送只算能交、有草稿的行；暂时不处理算勾选的全部
  const handable = rows.filter(canHand).map(t => t.key);
  const handTicked = handable.filter(k => chosen.has(k));
  const snoozeTicked = rows.filter(t => chosen.has(t.key) && canSnooze(t)).map(t => t.key);
  const labels = handLabels(tab as Tab, handTicked.length, handable.length);
  const replying = tab === "reply";  // 待你回：两种交给并列（帮我回复只写草稿，处理并回复改完再写草稿）
  const toggle = (key: string) => setChosen(c => { const next = new Set(c); if (next.has(key)) next.delete(key); else next.add(key); return next; });
  const headBox = useRef<HTMLInputElement>(null);
  useEffect(() => { if (headBox.current) headBox.current.indeterminate = someTicked && !allTicked; }, [someTicked, allTicked]);
  // 左列的仓库（窄屏是范围下拉）：只写仓库名，重名的写全名，全名在提示里；再点一下选中的就回到全部仓库
  const names = repos.map(r => r.repo.split("/")[1].toLowerCase());
  const shortName = (full: string) => { const name = full.split("/")[1]; return names.filter(n => n === name.toLowerCase()).length > 1 ? full : name; };
  const isScope = (s: string) => scope.toLowerCase() === s.toLowerCase();
  const pickScope = (s: string) => setScope(isScope(s) ? "" : s);
  const scopeProject = !compact && scope.includes("/") ? projectOfRepo(scope, projects, all) : null;
  const oneRepo = !compact && scope.includes("/");  // 选中一个仓库：行上不再写仓库名，只写编号
  const loaded = compact ? !!report?.synced_at : !!report?.synced_at || !!prReport || openIssues.length > 0;
  const failures = compact ? [] : [...new Set([actionError, prs?.failed, prReport?.error && `GitHub 读取失败：${prReport.error}`,
    prReport?.autoloop_error].filter(Boolean))];
  const notes = compact ? [] : [...new Set([prReport?.limited, prReport?.note].filter(Boolean))];
  const message = content?.notification;
  const unread = tab === "all";  // 未读点只在收件箱（全部）里有意义
  const tagged = tab === "all" || tab === "mine";  // 各类混在一起的列表：行前标上类别
  const counts: Record<Choice, number | string> = { ...side.tabs, done: done ? doneShown.length : "…" };
  const choices: { key: Choice; label: string }[] = [...TABS, { key: "done", label: DONE_LABEL }];
  const limit = agentsReport?.limit || CONFIRM_OVER;

  // 微信的一行：类型图标、会话名、信号、一两句原话、时间；不能交给 Agent，只能勾选后暂时不处理
  const wechatLine = (t: Thread) => {
    const w = t.wechat!;
    const held = snoozeOf(t);
    const label = tagged && t.category ? tabLabel(t.category) : "";
    return <div key={t.key} className="inbox-row wechat-row">
      {ticking && (canTick(t) ? <input type="checkbox" aria-label={`选中 微信 ${w.chat}`} checked={chosen.has(t.key)} disabled={!!busy} onChange={() => toggle(t.key)} />
        : <span className="tick-gap" />)}
      <button className="inbox-open" onClick={() => void open(t)}>
        <WechatIcon row={w} />
        <span className="inbox-text">
          <small className="inbox-ref">微信 · {signalText(w)}{w.low_signal ? " · 疑似客套/表情" : ""}</small>
          <strong>{w.chat}</strong>
          {(label || held) && <span className={`inbox-line ${t.category ?? ""}`}>{label && <span className={`tag ${classTone(t.category ?? "")}`}>{label}</span>}{held?.line}</span>}
          {!held && w.text.map((x, n) => <span key={n} className="inbox-line wechat-text">{x}</span>)}
        </span>
        <span className="inbox-new" />
        <time dateTime={t.updated_at} title={when(t.updated_at)}>{ago(t.updated_at)}</time>
      </button>
    </div>;
  };
  const line = (t: Thread) => {
    if (t.wechat) return wechatLine(t);
    const text = rowLine(t, listTab);
    const label = tagged && t.category ? tabLabel(t.category) : "";
    const tone = t.category === "agent" ? classTone("flow") : classTone(t.category ?? "");
    const lost = t.issue && t.category !== "reply" ? stuck(t.issue) : false;
    const can = canTick(t);
    const hint = replying ? replyHint(t) : null;  // 只需回复 / 需要处理：默认建议，两个按钮都能点
    return <div key={t.key} className={`inbox-row${unread && t.unread ? " unread" : ""}`}>
      {unread && <span className={t.unread ? "unread-dot" : "read-dot"} role="img" aria-label={t.unread ? "未读" : "已读"} />}
      {ticking && (can ? <input type="checkbox" aria-label={`选中 ${t.repository}${t.number !== null ? ` #${t.number}` : ""}`} checked={chosen.has(t.key)}
        disabled={!!busy} onChange={() => toggle(t.key)} /> : <span className="tick-gap" title={drafting ? "没有草稿" : "已交给 Agent，还在派或在处理"} />)}
      <button className="inbox-open" onClick={() => void open(t)}>
        <KindIcon thread={t} />
        <span className="inbox-text">
          <small className="inbox-ref">{oneRepo && t.number !== null ? `#${t.number}` : <>{t.repository}{t.number !== null && ` #${t.number}`}</>}</small>
          <strong>{t.title}</strong>
          {(text || label || lost || hint) && <span className={`inbox-line ${t.category ?? ""}`}>
            {label && <span className={`tag ${tone}`}>{label}</span>}{lost && <span className="tag warn">{t.issue!.stalled === "lost" ? "派发可能丢了" : "未派出"}</span>}{text}
            {hint && <span className={`tag reply-hint ${hint.mode === "handle" ? "warn" : ""}`} title={`建议：${hint.why}`}>{hint.label}</span>}</span>}
        </span>
        <span className="inbox-new" title={t.newCount ? `你最后一次动作之后对方新增 ${t.newCount} 条` : undefined}>{t.newCount ? `+${t.newCount}` : ""}</span>
        <time dateTime={t.updated_at} title={when(t.updated_at)}>{ago(t.updated_at)}</time>
      </button>
    </div>;
  };
  const linked = !!current?.project_id && current.classification === "workspace";  // 有「项目详情 →」时不再写自动归类
  const detail = current?.wechat ? <WechatDetail row={current.wechat} close={close} done={() => void markDone(current.wechat!)} busy={busy === "wechat-done"} error={actionError}
      snooze={<SnoozeSection key={current.key} item={snoozeItem(current)} held={snoozeOf(current)} can={canSnooze(current)} changed={draftChanged} />} />
    : current && <Modal title="GitHub 消息详情" close={close}>
      <div className="eyebrow">{current.repository}{current.number !== null && ` #${current.number}`}
        {linked && <> · <button className="text-button" onClick={() => { close(); onProject(current.project_id!); }}>{projectLinkText(current.project_name, current.repository)}</button></>}</div><h2>{current.title}</h2>
      {message && <div className="message-meta"><StateTag state={message.state} /><span>{message.unread ? "GitHub 未读" : "GitHub 已读"}</span><span>{message.category}</span></div>}
      {current.draft && <DraftPanel draft={current.draft} switchable={current.issue?.switch ?? current.pr?.switch} changed={draftChanged} />}
      {!compact && <SnoozeSection key={current.key} item={snoozeItem(current)} held={snoozeOf(current)} can={canSnooze(current)} changed={draftChanged} />}
      {!current.draft && !current.issue && current.category === "reply" && handVia(current) && !snoozeOf(current) && <section className="detail-section">
        <div className="inbox-actions">{MODES.map(m => <button key={m} className={(replyHint(current)?.mode ?? "reply") === m ? "primary" : ""} disabled={!!busy}
          onClick={() => void handoff([current.key], m)}>{busy === "handoff" && handing === m ? "提交中…" : MODE_NAMES[m]}</button>)}</div>
        <p className="meta">{MODE_NOTE}</p>
        {actionError && <p className="error" role="alert">{actionError}</p>}{noteFor(note, current.key) && <p className="success-note" role="status">{notice}</p>}
      </section>}
      {!current.issue && current.pr && !snoozeOf(current) && (current.pr.settled || (current.pr.switch && !current.draft)) && <section className="detail-section">
        {current.pr.settled && <p className="notice-line" role="status">{settledLine(current.pr.settled)}。之后没有新的回复、评审或提交，列表上不出交给按钮、批量交也会跳过；确实要再派一轮点「仍交给 Agent」。</p>}
        {current.pr.switch && !current.draft && <p className="meta">Agent 第 {current.pr.switch.run} 轮（只回复）说这条要改代码：{current.pr.switch.line}</p>}
        <div className="inbox-actions">
          {current.pr.switch && !current.draft && <button className="primary" disabled={!!busy} onClick={() => void handPr(current, "handle", !!current.pr?.settled)}>{busy === "handoff" && handing === "handle" ? "提交中…" : "改用处理并回复"}</button>}
          {current.pr.settled && <button className="ghost" disabled={!!busy} onClick={() => void handPr(current, undefined, true)}>{busy === "handoff" && !handing ? "提交中…" : "仍交给 Agent"}</button>}
        </div>
        {actionError && <p className="error" role="alert">{actionError}</p>}{noteFor(note, current.key) && <p className="success-note" role="status">{notice}</p>}
      </section>}
      {current.pr && <FeedDetail row={current.pr} answered={answered} />}
      {message && content && <>
        <dl>{!linked && <><dt>自动归类</dt><dd>{message.project_id ? message.project_name : message.classification === "paused" ? "项目暂缓，不自动纳入当前处理范围" : message.classification === "archived" ? `${message.project_name ?? "项目"}已归档，不纳入当前处理范围` : message.classification === "ambiguous" ? `归属待核对：${message.candidates.map(p => p.name).join("、")}` : "未匹配工作台项目"}</dd></>}{!sameEvent(current.draft, message.updated_at) && <><dt>通知时间</dt><dd>{when(message.updated_at)}</dd></>}</dl>
        <section className="detail-section"><h3>{content.content_kind === "pull" ? "PR 正文" : content.content_kind === "issue" ? "issue 正文" : "正文"}{content.author && <small>{content.author}{content.created_at ? ` · ${when(content.created_at)}` : ""}</small>}</h3>{detailLoading ? <p className="meta" role="status">读取正文…</p> : <>{content.body_error && <p className="error" role="alert">{content.body_error}</p>}{content.body ? <Markdown text={content.body} repo={message.repository} /> : !content.body_error && <p className="meta">该通知没有提供文字正文。</p>}</>}</section>
        {!detailLoading && (content.comments?.length || content.comment_error) ? <section className="detail-section"><h3>评论 <small>{content.comments?.length ?? 0}</small></h3>{content.comment_error && <p className="error" role="alert">{content.comment_error}</p>}<ol className="timeline">{(content.comments ?? []).map((c, n) => <li key={n}><div><strong>{c.author}</strong><time>{when(c.created_at)}</time></div><Markdown text={c.body} repo={message.repository} /></li>)}</ol></section> : null}
      </>}
      <a className="source-link" href={content?.url ?? current.url} target="_blank" rel="noreferrer">查看 GitHub 原文 ↗</a>
    </Modal>;
  // 左列分组可以收起，同一时间只展开一组；默认展开当前筛选所在的组，换筛选时跟着展开
  const groupOf = (key: Choice) => sideGroups().find(g => g.keys.includes(key as Tab))?.title ?? "";
  const [openGroup, setOpenGroup] = useState<string>(() => groupOf(tab) || sideGroups()[0].title);
  useEffect(() => { const g = groupOf(tab); if (g) setOpenGroup(g); }, [tab]);
  const groupHead = (title: string, total: ReactNode) => {
    const open = openGroup === title;
    return <button className="inbox-side-head" aria-expanded={open} onClick={() => setOpenGroup(open ? "" : title)}>
      <span className="inbox-side-caret" aria-hidden="true">{open ? "▾" : "▸"}</span><span>{title}</span>{!open && <small>{total}</small>}</button>;
  };
  const sideButton = (key: Choice, label: string, icon?: ReactNode) => <button key={key} aria-current={tab === key || undefined} className={tab === key ? "on" : ""} onClick={() => setTab(key)}>
    {icon}<span className="inbox-repo">{label}</span><small>{counts[key]}</small></button>;
  // 同步消息：账号、上次同步时刻和按钮放在列表上方工具条的右侧；未读条数写在「未读」开关上（选中时左列已经写了）
  const syncBar = online && <div className="inbox-sync">
    <span className="meta">{report?.account ? `GitHub · ${report.account}` : "正在连接 GitHub…"}{report?.synced_at ? ` · 同步于 ${when(report.synced_at)}` : " · 尚未完成同步"}</span>
    <button disabled={loading} onClick={() => void sync()}>{loading ? "同步中…" : "↻ 同步消息"}</button>
    {needsOn && <NeedsButton count={issueDrafts?.drafts.length ?? 0} open={needsOpen} toggle={() => setNeedsOpen(o => !o)} />}
  </div>;
  const unreadTotal = searched.filter(t => (compact || inScope(t, scope)) && inTab(t, "all", "unread")).length;
  const readToggle = <div className="segmented" role="group" aria-label="已读状态">{([["all", "全部"], ["unread", "未读"]] as const).map(([value, label]) =>
    <button key={value} className={read === value ? "on" : ""} aria-pressed={read === value} onClick={() => setRead(value)}>{label}{value === "unread" && read !== "unread" && report?.synced_at ? ` ${unreadTotal}` : ""}</button>)}</div>;
  const pagination = pages > 1 && <div className="pagination"><span>第 {currentPage} / {pages} 页</span><div><button disabled={currentPage === 1} onClick={() => setPage(p => p - 1)}>上一页</button><button disabled={currentPage === pages} onClick={() => setPage(p => p + 1)}>下一页</button></div></div>;

  if (compact) return <>
    <h2>项目 GitHub 消息</h2>
    {!online ? <div className="notice">GitHub 消息需要从本机工作台服务读取。</div> : <>
      {error && <div className="error" role="alert">{error}{report?.synced_at ? "；当前保留上次同步结果。" : ""}</div>}
      {report?.state_note && <div className="notice-line" role="status">{report.state_note}</div>}
      {actionError && <div className="error" role="alert">{actionError}</div>}
      <div className="inbox-toolbar"><input type="search" aria-label="搜索 GitHub 消息" placeholder="搜索标题、仓库或编号…" value={search} onChange={e => setSearch(e.target.value)} />{syncBar}</div>
      <div className="inbox-table">
        <div className="inbox-head">
          {readToggle}
          <span>{rows.length + others.length} 条</span>
          <select className="inbox-sort" aria-label="排序" value={sort} onChange={e => setSort(e.target.value as Sort)}><option value="newest">新的在前</option><option value="oldest">旧的在前</option></select>
        </div>
        {loading && !loaded && <div className="notice" role="status">正在读取 GitHub 通知并自动归类…</div>}
        {shown.map(line)}
        {!rows.length && loaded && <div className="notice">{TABS[0].empty}</div>}
        {others.length > 0 && <details className="inbox-fold"><summary>CI 等其他通知 <small>{others.length}</small></summary>{others.map(line)}</details>}
      </div>
      {pagination}
      <footer><span>自动归类与 GitHub 已读状态分别记录。读取消息不会自动建任务或执行代码。</span><span>同步结果保存在本机数据目录</span></footer>
    </>}
    {detail}
  </>;

  return <>
    {online && <>
      {error && <div className="error" role="alert">{error}{report?.synced_at ? "；当前保留上次同步结果。" : ""}</div>}
      {report?.state_note && <div className="notice-line" role="status">{report.state_note}</div>}
      {failures.map(f => <div key={f} className="error" role="alert">{f}</div>)}
      {notes.map(n => <div key={n} className="notice-line" role="status">{n}</div>)}
    </>}
    <div className="tabs inbox-narrow" role="tablist" aria-label="筛选">{choices.map(t =>
      <button key={t.key} role="tab" className="tab" aria-selected={tab === t.key} onClick={() => setTab(t.key)}>{t.label}<small>{counts[t.key]}</small></button>)}</div>
    <div className="inbox-layout">
      <aside className="inbox-side" aria-label="收件箱筛选">
        <div className="inbox-side-group">{sideButton("all", TABS[0].label, <InboxIcon />)}</div>
        {sideGroups().map(g => <div key={g.title} className="inbox-side-group">
          {groupHead(g.title, g.keys.reduce((n, k) => n + (Number(counts[k]) || 0), 0))}
          {openGroup === g.title && g.keys.map(k => sideButton(k, tabLabel(k)))}
        </div>)}
        <div className="inbox-side-group">{groupHead("范围", scope === WECHAT_SCOPE ? "微信" : scope ? shortName(scope) : "全部仓库")}
          {openGroup === "范围" && <>
          {wechatOn && <button aria-pressed={isScope(WECHAT_SCOPE)} className={isScope(WECHAT_SCOPE) ? "on" : ""} title="微信；再点一下回到全部" onClick={() => pickScope(WECHAT_SCOPE)}>
            <span className="inbox-repo">微信</span><small>{side.wechat}</small></button>}
          {repos.map(r => <button key={r.repo} aria-pressed={isScope(r.repo)} className={isScope(r.repo) ? "on" : ""} title={`${r.repo}；再点一下回到全部仓库`} onClick={() => pickScope(r.repo)}>
            <span className="inbox-repo">{shortName(r.repo)}</span><small>{r.count}</small></button>)}
          {!repos.length && !wechatOn && <p className="meta">这里没有条目</p>}
          </>}
        </div>
        <div className="inbox-side-group">{sideButton("done", DONE_LABEL)}</div>
      </aside>
      <div className="inbox-main">
        {online && listing && <div className="agent-bar">
          {agentsReport ? <AgentStatus agents={agentsReport} /> : <span className="meta">{agents?.failed ? "Agent 计数没读到" : "正在读取 Agent 计数…"}</span>}
          <button disabled={!!busy || !agents} onClick={() => void operate("read", () => reloadAll(true))}>{busy === "read" ? "读取中…" : "一键读取"}</button>
        </div>}
        {notice && <div className="success-note" role="status">{notice}</div>}
        <div className="inbox-toolbar">
          <input type="search" aria-label="搜索收件箱" placeholder="搜索标题、仓库、编号或标签…" value={search} onChange={e => setSearch(e.target.value)} />
          <select className="inbox-scope" aria-label="范围" value={isScope(WECHAT_SCOPE) ? WECHAT_SCOPE : repos.find(r => isScope(r.repo))?.repo ?? ""} onChange={e => setScope(e.target.value)}>
            <option value="">全部仓库</option>
            {wechatOn && <option value={WECHAT_SCOPE}>微信（{side.wechat}）</option>}
            {repos.map(r => <option key={r.repo} value={r.repo} title={r.repo}>{shortName(r.repo)}（{r.count}）</option>)}
          </select>
          {syncBar}
        </div>
        {wechatOn && isScope(WECHAT_SCOPE) && <div className="wechat-bar">
          <span className="meta">{freshness(wechat)}{wechat?.running ? " · 正在刷新" : ""}{wechat?.hidden ? ` · ${wechat.hidden} 条已处理` : ""}</span>
          {wechat?.hint && <p className="notice-line" role="status">{wechat.hint}</p>}
        </div>}
        {scopeProject && <div className="inbox-scope-bar">
          <button className="text-button" onClick={() => onProject(scopeProject.id)}>{scopeProject.name} · 项目详情 →</button>
        </div>}
        {!online ? <div className="notice-line" role="status">离线快照不含收件箱：请在仓库根目录运行 python3 -m personal 启动工作台，再打开 http://127.0.0.1:8790</div>
        : tab === "done" ? <AllDoneList report={done} items={doneShown.slice((currentPage - 1) * SIZE, currentPage * SIZE)} failed={doneError} />
        : <div className="inbox-table">
          <div className="inbox-head">
            {ticking && <input ref={headBox} type="checkbox" aria-label="全选" checked={allTicked} disabled={!!busy || !tickable.length} onChange={tickAll} />}
            {tab === "all" && readToggle}
            {handBar && (drafting ? <div className="inbox-actions">
              {/* 勾了就只出「勾选」按钮，没勾才出「全部」：两个并排时容易点到全部（2026-11-19 勾 3 条点成了全部 5 条） */}
              {handTicked.length ? <button className="primary" disabled={!!busy} onClick={() => ask(handTicked)}>{busy === "send" ? "发送中…" : `确认发送（勾选 ${handTicked.length} 条）`}</button>
              : <button className="primary" disabled={!!busy || !handable.length} onClick={() => ask(handable)}>{busy === "send" ? "发送中…" : `全部确认发送（${handable.length} 条）`}</button>}
            </div> : replying ? <div className="inbox-actions split">
              {handTicked.length ? MODES.map(m => { const l = handLabels("reply", handTicked.length, handable.length, m);
                return <button key={m} className={m === "reply" ? "primary" : ""} disabled={!!busy} onClick={() => ask(handTicked, m)}>{busy === "handoff" && handing === m ? l.busy : l.some}</button>; })
              : MODES.map(m => <button key={`all-${m}`} className={m === "reply" ? "primary" : ""} disabled={!!busy || !handable.length}
                onClick={() => ask(handable, m)}>{handLabels("reply", handTicked.length, handable.length, m).all}</button>)}
            </div> : <div className="inbox-actions">
              {handTicked.length ? <button className="primary" disabled={!!busy} onClick={() => ask(handTicked)}>{busy === "handoff" ? labels.busy : labels.some}</button>
              : <button className="primary" disabled={!!busy || !handable.length} onClick={() => ask(handable)}>{labels.all}</button>}
            </div>)}
            {ticking && <div className="inbox-actions snooze-actions"><SnoozeButton label={snoozeLabel(snoozeTicked.length)} busy={busy === "snooze"}
              disabled={!!busy || !snoozeTicked.length} run={wait => void snoozeAll(snoozeTicked, wait)} /></div>}
            <select className="inbox-sort" aria-label="排序" value={sort} onChange={e => setSort(e.target.value as Sort)}><option value="newest">新的在前</option><option value="oldest">旧的在前</option></select>
          </div>
          {!loaded && <div className="notice" role="status">正在读取 issue、PR 和 GitHub 通知…</div>}
          {drafting && rows.length > 0 && <p className="meta draft-hint">点开一行看草稿全文、直接改；勾选后确认发送的按草稿原样发出。每条发之前重读讨论，对方又回了的先不发。</p>}
          {drafting && sent.length > 0 && <ul className="send-results" aria-label="发送结果">{sent.map(x =>
            <li key={x.subject} className={x.result.ok ? "ok" : x.result.stale ? "warn" : "bad"}>{sendLine(x.subject, x.result)}</li>)}</ul>}
          {shown.map(line)}
          {!rows.length && loaded && <div className="notice">{TABS.find(t => t.key === listTab)!.empty}{q || scope ? "（当前范围和搜索下）" : ""}</div>}
          {others.length > 0 && <details className="inbox-fold">
            <summary>CI 等其他通知 <small>{others.length}</small></summary>
            {others.map(line)}
          </details>}
        </div>}
        {pagination}
        {footer}
      </div>
    </div>
    {detail}
    {needsOn && needsOpen && <NeedsDrawer report={issueDrafts} close={() => setNeedsOpen(false)} changed={() => void reloadAll().catch(e => setActionError(errorText(e)))} />}
    {asking && <Confirm title={handLabels(tab as Tab, asking.keys.length, asking.keys.length, asking.mode).title}
      text={confirmText(asking.keys.length, limit, asking.mode === "reply" ? "请 Agent 帮你回复（只写回复草稿）" : asking.mode === "handle" ? "请 Agent 处理并回复" : "交给 Agent")}
      ok={handLabels(tab as Tab, asking.keys.length, asking.keys.length, asking.mode).ok} close={() => setAsking(null)} confirm={() => { const { keys, mode } = asking; setAsking(null); void handoff(keys, mode); }} />}
    {sendAsking && <Confirm title="确认发送" text={`${sendConfirmText(sendAsking.length)}\n${sendAsking.join("\n")}`} ok={`发送 ${sendAsking.length} 条`}
      close={() => setSendAsking(null)} confirm={() => { const keys = sendAsking; setSendAsking(null); void sendAll(keys); }} />}
  </>;
}
