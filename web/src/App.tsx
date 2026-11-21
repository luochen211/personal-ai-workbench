import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Inbox, { useGithubMessages } from "./Inbox";
import Modal from "./Modal";
import Plugins from "./Plugins";
import Question from "./Question";
import { DecisionPackage } from "./Decisions";
import Markdown from "./markdown";
import { LinkedPrList, MissingProject, NO_NOTE, ReviewLine, RunResultLine, canHandoff, noteFor } from "./IssueParts";
import type { Note } from "./IssueParts";
import { classTone, flowNote, nextButtons, switchButton } from "./NextStep";
import { RunsPanel, WatchPanel } from "./Panels";
import { ChatPanel } from "./Chat";
import { following as followingPr } from "./PrParts";
import { SNOOZABLE, attachDrafts, inboxHash, issueBucket, mergeThreads, replyCount, withIssues, withMode } from "./InboxParts";
import { SnoozeSection } from "./Snooze";
import { DraftPanel } from "./Drafts";
import { BoardsPanel, BriefPanel, LearningPanel } from "./AssistantPanels";
import { ReviewPanel } from "./Review";
import { HealthPanel } from "./Health";
import { ContentPanel, MaitianPanel } from "./Media";
import { Rail, Tabs, entryView, sectionOf, viewOf as viewOfHash, movedHash } from "./nav";
import type { NavItem } from "./nav";
import { useTheme } from "./theme";
import { ProjectIssueEntry } from "./HomeParts";
import { embedded, request } from "./api";
import { stallNote, stuck, waiting } from "./handoff";
import { PrSection, ProjectUnread, hideClosed, hideDone, parseProjectHash, projectHash } from "./ProjectParts";
import type { PrSort } from "./ProjectParts";
import { loadAuto, saveAuto, startAutoRefresh } from "./autorefresh";
import type { Activity, AgentsReport, DoneReport, DraftsReport, Handoff, Issue, IssueDetail, IssueReport, IssueRun, LinkedPrs, Payload, PrReport, ReplyDraft, ReplyMode, WechatReport } from "./api";
import { wechatThreads } from "./WechatParts";
import { scanning } from "./IssueDraftParts";
import type { IssueDraftsReport } from "./api";

// issue 就是任务：按「下一步该谁做」归类（见 NextStep.ts），只有该 Agent 做的才出按钮；点了打 agent 标签或记交给记录，页面跟着看处理进度。
// 左侧导航四个入口和页内标签见 nav.ts；收件箱（Inbox.tsx）合了原「消息」「Issue」「待你处理」三页
const panels = ["chat", "brief", "boards", "learning", "review", "health", "media", "content", "runs", "watch"];
const RESTORE = "改 projects.toml 的 archived 即可恢复";  // 页面不写配置，取消归档在登记表里改
const assistantViews = ["brief", "boards", "learning", "review", "health", "media", "content"];  // 离线快照里没有数据的页
// 页头规则（用户 2026-11-18 定：同一件信息在一屏里只出现一次，留能用的那个，删装饰性的重复）。各页照这四条做：
// 1. 没有顶栏面包屑：左侧导航已高亮当前页，「工作空间 / 收件箱」只是重复。只有带层级的页（项目详情）在页头标题上方写
//    一行可点的位置「收件箱 / 分组」，当前页的名字只写在 h1 里，不在位置行里再写一遍。
// 2. 一级页面不写和导航同名的 h1：有页内标签（助理、自媒体、Agent）时，页内标签就是标题；没有页内标签的页保留 h1（项目详情写项目名）。
// 3. 页内标签下面的工具条不再写同名标签（「学习 2 个课题」「运行 300」），只留计数，用 .view-count（「2 个课题」「300 条」）；
//    工具条里另有分区小标题时才用 .view-label，且不能和页内标签同名。
// 4. 页内左列第一项和页名同名（收件箱）时去掉页面 h1，留左列那项；同步时间和按钮放进列表上方的工具条。
// 收件箱带筛选和范围：#/inbox?f=wait&s=owner/repo。旧的消息、PR 待处理、Issue 列表、待你处理的网址先换成收件箱的（见 InboxParts.legacyInboxHash）
const viewOf = () => {
  const moved = movedHash(location.hash);
  if (moved) history.replaceState(null, "", moved);
  return viewOfHash(location.hash);
};
const errorMessage = (error: unknown) => error instanceof Error ? error.message : "操作失败，请重试";
const day = (value: string) => value.slice(0, 10);
const tone = (stage: string) => stage === "等你决定" ? "bad" : stage === "等你看" ? "warn" : stage === "已处理" ? "ok" : "";
// 刚交给的 issue 每 10 秒看一次进度，一小时后或判定派发可能丢了时回到手动或 60 秒刷新
const following = (i: Issue) => !!i.handed_at && waiting(i) && Date.now() - Date.parse(i.handed_at) < 3600000;
const runState = (run: IssueRun) => ({ done: "已结束", claimed: "处理中", queued: "排队中", cancelled: "已停止", unknown: "状态未知" } as Record<string, string>)[run.state] ?? run.state;
const FRESH_AGE = 120;  // 项目详情页的数据超过 2 分钟就让后端在后台重读
const FOLLOW_UPS = 5;  // 后端在后台重读时，隔 3 秒再取一次，最多这么多次
const clock = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";
// 通知点开的网址：#/i/<owner>/<repo>/<编号> 打开 issue 详情，#/q/<编号> 打开这条 Agent 提问所属的 issue
const parseRoute = () => {
  const issue = location.hash.match(/^#\/i\/([\w.-]+)\/([\w.-]+)\/([0-9]+)$/);
  if (issue) return { subject: `${issue[1]}/${issue[2]}#${issue[3]}` };
  const question = location.hash.match(/^#\/q\/([0-9]+)$/);
  return question ? { question: Number(question[1]) } : null;
};

export default function App() {
  const [data, setData] = useState<Payload | null>(embedded);
  const [report, setReport] = useState<IssueReport | null>(null);
  const [view, setView] = useState(viewOf);
  const lastTab = useRef<Record<string, string>>({});  // 「助理」「自媒体」「Agent」上次看的页内标签
  const [messages, setMessages] = useState<{ project?: string } | null>(null);
  const [pluginsOpen, setPluginsOpen] = useState(false);  // 插件弹窗（左侧图标栏的「插件」）
  const [inboxKey, setInboxKey] = useState(0);
  const [project, setProject] = useState(() => parseProjectHash(location.hash)?.id ?? "");
  const [selected, setSelected] = useState<Issue | null>(null);
  const [route, setRoute] = useState<{ subject?: string; question?: number } | null>(parseRoute);
  const [detail, setDetail] = useState<IssueDetail | null>(null);
  const [linked, setLinked] = useState<LinkedPrs | null>(null);
  const [linkedError, setLinkedError] = useState("");
  const selectedKey = useRef<string | null>(null);
  const [error, setError] = useState("");
  const [detailError, setDetailError] = useState("");
  // 提示带上说的是哪一条：页面顶上照常显示，issue 详情里只显示这一条自己的（noteFor），别的 issue 的「已发出」不串过来
  const [note, setNote] = useState<Note>(NO_NOTE);
  const notice = note.text;
  const setNotice = (text: string, about = "") => setNote({ text, about });
  const [busy, setBusy] = useState(false);
  const [auto, setAutoState] = useState(() => loadAuto());
  const setAuto = (on: boolean) => { setAutoState(on); saveAuto(on); };
  const [activity, setActivity] = useState<Activity | null>(null);
  // 项目详情「我的 PR」的排序：默认最近动态，选「编号」写进网址 #/p/<项目>?sort=number
  const [prSort, setPrSortState] = useState<PrSort>(() => parseProjectHash(location.hash)?.sort ?? "recent");
  const [activityError, setActivityError] = useState("");
  // 收件箱的「近 30 天已完成」：全部项目近 30 天已完成的 issue，只在收件箱读
  const [done, setDone] = useState<DoneReport | null>(null);
  const [doneError, setDoneError] = useState("");
  // 你开着的 PR（全部仓库）：收件箱的 PR 行和导航上的待你回数共用这一份
  const [prReport, setPrReport] = useState<PrReport | null>(null);
  const [prError, setPrError] = useState("");
  // 中台里在跑和排队的 Agent：收件箱顶部的计数和「最多同时几个」
  const [agentsReport, setAgentsReport] = useState<AgentsReport | null>(null);
  const [agentsError, setAgentsError] = useState("");
  // 「帮我回复」后 Agent 写好、等你确认发送的回复草稿（本机 .personal/reply-drafts.json）
  const [drafts, setDrafts] = useState<ReplyDraft[]>([]);
  const [draftError, setDraftError] = useState("");
  // 可选的微信范围（personal/plugins/wechat/inbox.py）：没有 .personal/wechat.json 时 enabled 为 false，页面上不出现微信
  const [wechat, setWechat] = useState<WechatReport | null>(null);
  // 从微信拟的 issue 草稿和「读新需求」的状态（本机 .personal/issue-drafts.json）
  const [issueDrafts, setIssueDrafts] = useState<IssueDraftsReport | null>(null);
  const [issueDraftError, setIssueDraftError] = useState("");
  // 刷新时读这些 ref：自动刷新的定时器不用随它们重建
  const focusRef = useRef("");
  const listRef = useRef(false);
  const modalRef = useRef(false);
  const followRef = useRef(0);
  const followTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [theme, toggleTheme] = useTheme();
  const online = location.protocol.startsWith("http");

  const feed = useGithubMessages(online);
  const notifications = feed.report?.notifications ?? [];
  const reloadMessages = useRef(feed.reload);
  reloadMessages.current = feed.reload;

  const loadPrs = useCallback(async (force = false) => {
    // GitHub 请求由后端缓存挡住：开着的 PR 5 分钟一次，时间线只在 PR 有新动态时重读
    try { setPrReport(await (force ? request<PrReport>("/api/prs/feedback/refresh", {}) : request<PrReport>("/api/prs/feedback"))); setPrError(""); }
    catch (e) { setPrError(errorMessage(e)); }
  }, []);
  const loadAgents = useCallback(async () => {
    try { setAgentsReport(await request<AgentsReport>("/api/agents")); setAgentsError(""); }
    catch (e) { setAgentsError(errorMessage(e)); }
  }, []);
  const loadDrafts = useCallback(async () => {
    try { setDrafts((await request<DraftsReport>("/api/replies/drafts")).drafts); setDraftError(""); }
    catch (e) { setDraftError(`回复草稿没读到：${errorMessage(e)}`); }
  }, []);
  const loadIssueDrafts = useCallback(async () => {
    try { setIssueDrafts(await request<IssueDraftsReport>("/api/issue-drafts")); setIssueDraftError(""); }
    catch (e) { setIssueDraftError(`issue 草稿没读到：${errorMessage(e)}`); }
  }, []);
  const loadWechat = useCallback(async (kick = false) => {
    // 一键读取（force）时让后端在后台刷新一轮微信数据，不等它跑完；读不到就当没启用，不打扰别的
    try {
      if (kick) await request("/api/wechat/refresh", {});
      setWechat(await request<WechatReport>("/api/wechat"));
    } catch { /* 微信是可选的，读不到不报错 */ }
  }, []);
  const refresh = useCallback(async (force = false, followUp = false) => {
    // 项目详情页：issue 和 PR 超过 2 分钟就让后端后台重读；后端在重读时隔 3 秒再取一次，读到新的就显示
    const focus = focusRef.current;
    const scope = focus ? `?focus=${encodeURIComponent(focus)}&max_age=${FRESH_AGE}` : "";
    const path = `/api/projects/${encodeURIComponent(focus)}/activity`;
    const prs = focus ? (force ? request<Activity>(`${path}/refresh`, {}) : request<Activity>(`${path}?max_age=${FRESH_AGE}`))
      .then(result => { if (focusRef.current === focus) { setActivity(result); setActivityError(""); } return result; })
      .catch(e => { if (focusRef.current === focus) setActivityError(errorMessage(e)); return null; }) : Promise.resolve(null);
    const closed = listRef.current ? (force ? request<DoneReport>("/api/issues/done/refresh", {}) : request<DoneReport>("/api/issues/done"))
      .then(result => { setDone(result); setDoneError(""); return result; })
      .catch(e => { setDoneError(errorMessage(e)); return null; }) : Promise.resolve(null);
    void reloadMessages.current();  // GitHub 消息：读本机缓存，超过 5 分钟再同步一次
    void loadPrs();
    void loadAgents();
    void loadDrafts();
    void loadIssueDrafts();
    void loadWechat(force);
    const [board, issues, recent, finished] = await Promise.all([
      request<Payload>("/api/board"),
      force ? request<IssueReport>("/api/issues/refresh", {}) : request<IssueReport>(`/api/issues${scope}`),
      prs,
      closed,
    ]);
    setData(board); setReport(issues);
    if (!followUp) followRef.current = 0;
    if (followTimer.current) clearTimeout(followTimer.current);
    if ((issues.refreshing || recent?.refreshing || finished?.refreshing) && followRef.current < FOLLOW_UPS) {
      followRef.current += 1;
      followTimer.current = setTimeout(() => void refresh(false, true).catch(() => {}), 3000);
    }
  }, [loadPrs, loadAgents, loadDrafts, loadIssueDrafts, loadWechat]);
  const scanRunning = scanning(issueDrafts?.scan);
  useEffect(() => {
    // 「读新需求」在跑时每 4 秒读一次，读完（或没读成）就停
    if (!scanRunning || !online) return;
    const id = setInterval(() => void loadIssueDrafts(), 4000);
    return () => clearInterval(id);
  }, [scanRunning, online, loadIssueDrafts]);
  const wechatRunning = !!wechat?.running;
  useEffect(() => {
    // 微信数据在后台刷新：每 10 秒看一次，刷新完就停
    if (!wechatRunning || !online) return;
    const id = setInterval(() => void loadWechat(), 10000);
    return () => clearInterval(id);
  }, [wechatRunning, online, loadWechat]);
  useEffect(() => { if (online) void refresh().catch(e => setError(errorMessage(e))); }, [online, refresh]);
  const prTracking = (prReport?.prs ?? []).some(r => followingPr(r));
  useEffect(() => {
    // 刚交给的 PR 每 10 秒看一次进度
    if (!prTracking || !online) return;
    const id = setInterval(() => void loadPrs(), 10000);
    return () => clearInterval(id);
  }, [prTracking, online, loadPrs]);
  useEffect(() => {
    // 每 60 秒刷新：弹窗开着、正在输入时跳过这一轮；页面在后台时暂停，切回来立即刷新一次
    if (!auto || !online) return;
    return startAutoRefresh({ run: () => void refresh().catch(e => setError(errorMessage(e))), modalOpen: () => modalRef.current,
      doc: document as unknown as Parameters<typeof startAutoRefresh>[0]["doc"] });
  }, [auto, online, refresh]);
  const tracking = (report?.issues ?? []).some(following);
  useEffect(() => {
    if (!tracking || !online) return;
    const id = setInterval(() => void refresh().catch(e => setError(errorMessage(e))), 10000);
    return () => clearInterval(id);
  }, [tracking, online, refresh]);
  useEffect(() => {
    // 列表刷新后，打开着的详情跟着换成最新进度
    setSelected(s => s && (report?.issues.find(i => i.subject === s.subject) ?? s));
  }, [report]);

  const operate = async (action: () => Promise<void>) => {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  };
  const openIssue = async (issue: Issue) => {
    selectedKey.current = issue.subject;
    setSelected(issue); setDetail(null); setDetailError(""); setLinked(null); setLinkedError("");
    if (online) request<LinkedPrs>(`/api/issues/${issue.repo}/${issue.number}/prs`)
      .then(result => { if (selectedKey.current === issue.subject) setLinked(result); })
      .catch(e => { if (selectedKey.current === issue.subject) setLinkedError(errorMessage(e)); });
    try {
      const result = await request<IssueDetail>(`/api/issues/${issue.repo}/${issue.number}`);
      if (selectedKey.current === issue.subject) setDetail(result);
    } catch (e) { if (selectedKey.current === issue.subject) setDetailError(errorMessage(e)); }
  };
  const closeIssue = () => {
    selectedKey.current = null; setSelected(null); setDetail(null); setLinked(null); setError("");
    if (/^#\/[iq]\//.test(location.hash)) history.replaceState(null, "", location.pathname + location.search);
  };
  const answered = (subject: string) => (message: string) => { setNotice(message, subject); void refresh().catch(e => setError(errorMessage(e))); };
  useEffect(() => {
    // 从通知打开：列表读到后再定位 issue 或提问所属的 issue
    if (!route || !report) return;
    const issue = route.subject ? report.issues.find(i => i.subject === route.subject)
      : report.issues.find(i => i.waiting.some(w => w.id === route.question));
    setView("inbox"); setRoute(null);
    if (issue) { void openIssue(issue); return; }
    setError(route.subject ? `${route.subject} 不在当前项目的 open issue 里` : "这条提问已经回答过，或者不属于当前项目的 issue");
  }, [route, report]);
  // 交给后先在页面上把这几条改成「已交给 Agent」（收件箱里落到 Agent 处理中），等下一次读 GitHub 再以实际为准
  const markHanded = (handed: [Issue, Handoff][]) => {
    const results = new Map(handed.map(([i, r]) => [i.subject, r]));
    const mark = (i: Issue): Issue => {
      const result = results.get(i.subject);
      if (!result) return i;
      const kind = result.flow ?? "issue";
      return { ...i, stage: "已交给 Agent", stage_label: "已交给 Agent", stalled: "", handed_at: result.handed_at, flow: kind,
        next: { ...i.next, key: "flow", name: "Agent 流程中", line: "已交给 Agent", next: "已交给 Agent", button: "", action: kind, drawer_only: false },
        labels: kind === "feedback" || i.scope === "mine" || i.labels.includes(label) ? i.labels : [...i.labels, label] };
    };
    setReport(r => r && { ...r, issues: r.issues.map(mark) });
    setSelected(s => s && mark(s));
  };
  // mode：走 issue 留言流程的两种交给，reply 帮我回复（只写回复草稿），handle 处理并回复；别的流程不带
  // force：待你定、Agent 看过的「仍交给 Agent」（Agent 上一轮已有结论、之后没有新动静，后端不带它就拒绝）
  const handoff = (issue: Issue, mode?: ReplyMode, force = false) => operate(async () => {
    const result = await request<Handoff>(`/api/issues/${issue.repo}/${issue.number}/handoff`, withMode(force ? { force } : {}, mode));
    markHanded([[issue, result]]);
    const how = result.flow !== "feedback" ? "" : mode === "reply" ? "帮你回复（只写回复草稿）" : "处理并回复";
    setNotice(result.error || `${issue.subject} 已交给 Agent${how}，正在派出；处理进度每 10 秒更新。`, issue.subject);
  });

  const archived = data?.archived ?? [];
  const allProjects = view === "project" ? [...data?.projects ?? [], ...archived] : data?.projects ?? [];
  const navigate = (key: string) => {
    setView(key);
    if (key === "inbox") setInboxKey(k => k + 1);  // 从导航进收件箱：筛选回到待你回
    const target = `#/${key}`;
    if (location.hash !== target) history.pushState(null, "", target);
  };
  const openInbox = (target: string) => {
    // 项目详情的入口：去收件箱并筛到这个项目的仓库
    history.pushState(null, "", target);
    setView("inbox"); setInboxKey(k => k + 1);
  };
  const openProject = (id: string) => {
    setProject(id); setView("project");
    if (parseProjectHash(location.hash)?.id !== id) location.hash = projectHash(id, prSort).slice(1);
  };
  const setPrSort = (sort: PrSort) => {
    setPrSortState(sort);
    if (view === "project" && project) history.replaceState(null, "", projectHash(project, sort));
  };
  useEffect(() => {
    // 项目详情的网址：#/p/<项目>，支持直接打开和浏览器后退
    const follow = () => {
      const target = parseRoute();
      if (target) { setRoute(target); return; }
      const match = parseProjectHash(location.hash);
      if (match) { setProject(match.id); setPrSortState(match.sort); }
      setView(viewOf());
      setInboxKey(k => k + 1);  // 后退、前进到收件箱：筛选按网址重来
    };
    follow();
    addEventListener("popstate", follow);
    return () => removeEventListener("popstate", follow);
  }, []);
  const current = allProjects.find(p => p.id === project);
  focusRef.current = view === "project" && current?.issue_repository ? current.id : "";
  listRef.current = view === "inbox";
  modalRef.current = !!selected || !!messages || pluginsOpen;
  const focused = focusRef.current;
  const projectOpen = current ? hideClosed((report?.issues ?? []).filter(i => i.project_id === current.id), activity) : [];
  useEffect(() => {
    // 打开项目详情：先清掉上一个项目的 PR，再读（超过 2 分钟的后端在后台重读）
    setActivity(null); setActivityError("");
    if (online && focused) void refresh().catch(e => setError(errorMessage(e)));
  }, [focused, online, refresh]);
  const synced = feed.report?.synced_at;
  const lastSynced = useRef(synced);
  useEffect(() => {
    // GitHub 消息同步完：项目详情的新消息标记跟着读一次（后端只读本机收件记录）
    if (lastSynced.current && synced && synced !== lastSynced.current && online && focused) void refresh().catch(() => {});
    lastSynced.current = synced;
  }, [synced, focused, online, refresh]);
  const lastView = useRef(view);
  useEffect(() => {
    // 切到收件箱：读全部项目的「已完成」（后端用缓存，过期的在后台增量读）；直接打开收件箱时首次刷新已经读了
    if (online && view === "inbox" && lastView.current !== "inbox") void refresh().catch(e => setError(errorMessage(e)));
    lastView.current = view;
  }, [view, online, refresh]);
  const label = report?.label ?? "agent";
  const projectMessages = current ? notifications.filter(n => n.project_id === current.id) : [];
  // 收件箱的行：通知、你开的 PR、当前项目的 open issue（已知关闭了的不列），同一条只一行
  const activeIds = useMemo(() => new Set((data?.projects ?? []).map(p => p.id)), [data]);
  const openIssues = useMemo(() => hideDone((report?.issues ?? []).filter(i => activeIds.has(i.project_id)), done?.issues ?? []), [report, done, activeIds]);
  const threads = useMemo(() => [...attachDrafts(withIssues(mergeThreads(notifications, { prs: prReport }), openIssues,
    { login: prReport?.login || "" }), drafts), ...wechatThreads(wechat)], [notifications, prReport, openIssues, drafts, wechat]);
  const replies = replyCount(threads);
  const agentsFeed = useMemo(() => ({ report: agentsReport, failed: agentsError, reload: loadAgents }), [agentsReport, agentsError, loadAgents]);
  const prFeed = useMemo(() => ({ report: prReport, failed: prError, reload: loadPrs }), [prReport, prError, loadPrs]);
  const missing = view === "project" && !!data && !current;
  const section = sectionOf(view);
  if (section.tabs) lastTab.current[section.key] = view;
  const go = (item: NavItem) => navigate(entryView(item, lastTab.current));
  const counts = {
    inbox: { value: replies, note: `${replies} 条待你回` },
  };
  const selectedDraft = selected ? drafts.find(d => d.subject.toLowerCase() === selected.subject.toLowerCase()) : undefined;
  const footer = data && <footer><div><span>issue 读取于 {report?.fetched_at?.replace("T", " ").slice(0, 19) || "—"}</span>{data.commit_window && <span className="commits-range">项目范围：{day(data.commit_window.since)} 至今有本人提交 · 核对于 {data.commit_window.checked_at ? day(data.commit_window.checked_at) : "尚未核对"}</span>}</div>{online && <button className="text-button" disabled={busy} onClick={() => void operate(async () => {
    const result = await request<{ active_projects: number; github_error: string; discovered: string[] }>("/api/commits/refresh", {});
    await refresh(true);
    const found = result.discovered.length ? `新登记 ${result.discovered.length} 个上游项目：${result.discovered.join("、")}。` : "";
    setNotice(result.github_error ? `已核对本地提交；${result.github_error}。当前 ${result.active_projects} 个项目。${found}` : `近一个月有活动的项目：${result.active_projects} 个。${found}`);
  })}>{busy ? "核对中…" : "重新核对近月范围"}</button>}{online && <label title="弹窗开着或正在输入时跳过这一轮；页面在后台时暂停。GitHub 由后端缓存挡住，不会每分钟都读"><input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} /> 每 60 秒刷新</label>}</footer>;

  return <div className="shell">
    <Rail view={view} counts={counts} theme={theme} go={go} home={() => navigate("inbox")} toggleTheme={toggleTheme} openPlugins={() => setPluginsOpen(true)} />
    <main>
      <div className="workspace">
        {view === "project" && current && <div className="page-heading">
          <div><div className="eyebrow crumbs"><button className="crumb" onClick={() => navigate("inbox")}>收件箱</button><span className="slash">/</span>{current.group}</div><h1>{current.name}</h1>{current.archived && <p><span className="tag warn">已归档{current.archived_at ? `（${current.archived_at}）` : ""}</span> 项目已结束：不再读 issue、不巡检、不派活，也不计入收件箱和各页数量。{RESTORE}，数据都还在。</p>}</div>
          {online ? <div className="actions">
            {current.issue_repository && <a className="button primary" href={`https://github.com/${current.issue_repository}/issues/new`} target="_blank" rel="noreferrer">＋ 在 GitHub 新建 issue ↗</a>}
            <button onClick={() => void operate(() => refresh(true))} disabled={busy}>{busy ? "刷新中…" : "↻ 刷新"}</button>
          </div> : <span className="snapshot">离线快照</span>}
        </div>}
        {section.tabs && <Tabs tabs={section.tabs} value={view} pick={navigate} label={section.title} />}
        {error && !selected && <div className="error" role="alert">{error}</div>}
        {notice && <div className="success-note" role="status">{notice}</div>}
        {report?.limited && <div className="notice-line" role="status">{report.limited}</div>}
        {report?.error && <div className="error" role="alert">{report.error}</div>}
        {report?.autoloop_error && <div className="error" role="alert">{report.autoloop_error}</div>}
        {draftError && view === "inbox" && <div className="error" role="alert">{draftError}</div>}
        {issueDraftError && view === "inbox" && <div className="error" role="alert">{issueDraftError}</div>}
        {online && view === "chat" ? <ChatPanel />
        : online && view === "brief" ? <BriefPanel />
        : online && view === "boards" ? <BoardsPanel />
        : online && view === "learning" ? <LearningPanel />
        : online && view === "review" ? <ReviewPanel />
        : online && view === "health" ? <HealthPanel />
        : online && view === "media" ? <MaitianPanel />
        : online && view === "content" ? <ContentPanel />
        : !online && assistantViews.includes(view) ? null
        : online && view === "runs" ? <RunsPanel />
        : online && view === "watch" ? <WatchPanel />
        : view === "inbox" ? <Inbox key={inboxKey} online={online} feed={feed} agents={agentsFeed} prs={prFeed} onProject={openProject}
            threads={threads} openIssues={openIssues} done={done} doneError={doneError} data={data} wechat={wechat} issueDrafts={issueDrafts}
            onIssue={i => void openIssue(i)} onRefresh={force => refresh(force)} onHanded={markHanded} footer={footer} />
        : missing ? <MissingProject id={project} back={() => navigate("inbox")} />
        : data ? <>
          {view === "project" && current && <dl className="project-facts">
            <div><dt>简介</dt><dd>{current.summary || "暂无简介"}</dd></div>
            <div><dt>工作目录</dt><dd>{current.repo_path || "无本地检出"}</dd></div>
            <div><dt>GitHub 仓库</dt><dd>{current.issue_repository || current.repository ? <a href={`https://github.com/${current.issue_repository || current.repository}`} target="_blank" rel="noreferrer">{current.issue_repository || current.repository} ↗</a> : "仅本地登记"}</dd></div>
            <div><dt>最近本人活动</dt><dd>{current.last_commit_at ? day(current.last_commit_at) : "—"}</dd></div>
            <div><dt>GitHub 消息</dt><dd><button className="text-button" onClick={() => setMessages({ project: current.id })}>{projectMessages.length} 条{projectMessages.some(n => n.unread) ? ` · ${projectMessages.filter(n => n.unread).length} 未读` : ""} →</button></dd></div>
          </dl>}
          {view === "project" && current && <ProjectUnread activity={activity} />}
          {view === "project" && current && current.issue_repository && <ProjectIssueEntry open={report ? projectOpen.length : null}
            done={activity ? activity.done_issues?.length ?? 0 : null} href={inboxHash({ tab: "all", scope: current.issue_repository })} go={() => openInbox(inboxHash({ tab: "all", scope: current.issue_repository }))} />}
          {online && view === "project" && focused && <PrSection activity={activity} failed={activityError} sort={prSort} setSort={setPrSort} />}
          {footer}
        </> : <div className="notice">{error ? "请启动本机工作台服务后重试。" : "正在读取项目…"}</div>}
        {!online && panels.includes(view) && <div className="notice">离线快照没有这一页的数据：请启动工作台服务。</div>}
      </div>
    </main>
    {selected && <Modal title="Issue 详情" close={closeIssue}>
      <div className="eyebrow">{selected.subject}</div><h2>{selected.title}</h2>
      {selected.snooze ? <span className="tag">搁置</span> : <span className={`tag ${classTone(selected.next.key)}`} title={selected.next.why}>{selected.next.name}</span>}{!selected.snooze && selected.next.key === "flow" && <span className={`tag ${tone(selected.stage)}`}>{selected.stage_label || selected.stage}</span>}{selected.labels.map(l => <span key={l} className="tag">{l}</span>)}
      <a className="source-link" href={selected.url} target="_blank" rel="noreferrer">在 GitHub 打开 ↗</a>
      {selectedDraft && <DraftPanel draft={selectedDraft} switchable={selected.switch} changed={message => { if (message) setNotice(message, selected.subject); void refresh().catch(e => setError(errorMessage(e))); }} />}
      {online && <SnoozeSection key={selected.subject} item={{ subject: selected.subject, their_at: selected.their_at ?? "" }} held={selected.snooze}
        can={!selected.snooze && (!!selectedDraft || SNOOZABLE.includes(issueBucket(selected)))}
        changed={message => { setNotice(message, selected.subject); void refresh().catch(e => setError(errorMessage(e))); }} />}
      <section className="detail-section"><h3>Agent 处理</h3>
        {canHandoff(online, selected) && !selected.snooze && [switchButton(selected, !!selectedDraft), ...nextButtons(selected)].flatMap(b => b ? [b] : []).map(b => <button key={`${b.text}-${b.mode ?? "one"}`} className={b.primary ? "primary" : b.drawerOnly ? "ghost" : ""} disabled={busy || b.disabled} onClick={() => void handoff(selected, b.mode, !!b.force)}>{busy ? "交给中…" : b.text}</button>)}
        {online && !selectedDraft && selected.switch && <p className="meta">Agent 第 {selected.switch.run} 轮（只回复）说这条要改代码：{selected.switch.line}</p>}
        {online && (selected.snooze ? <p className="meta">暂时不处理中不能交给 Agent，先点「恢复」。</p> : flowNote(selected) && <p className="meta">{flowNote(selected)}</p>)}
        {error && <div className="error" role="alert">{error}</div>}
        {noteFor(note, selected.subject) && <div className="success-note" role="status">{notice}</div>}
        {selected.stage === "已交给 Agent" && (stuck(selected) ? <p className="notice-line" role="status">{stallNote(selected, label)}</p>
          : <p className="meta">{selected.handed_at ? `${selected.handed_at.replace("T", " ").slice(0, 16)} 交给 Agent，正在派出。` : `已打 ${label} 标签，工作台的监看闸门（每 5 分钟看一次）会派出。`}</p>)}
        <DecisionPackage key={selected.subject} issue={selected} done={answered(selected.subject)} refresh={() => void refresh().catch(e => setError(errorMessage(e)))} />
        {selected.waiting.filter(w => w.kind !== "ask").map(w => <Question key={w.id} item={w} done={answered(selected.subject)} />)}
        {selected.run && <dl><dt>{selected.run.state === "done" ? "处理结论" : "最近一轮"} · 第 {selected.run.id} 轮 · {runState(selected.run)}</dt><dd><Markdown text={selected.run.summary || selected.run.error || "还没有结论"} repo={selected.repo} /><RunResultLine result={selected.run.result} /></dd></dl>}
        <ReviewLine review={selected.review} />
        {selected.history.length > 0 && <><h3>运行记录 <small>{selected.history.length}</small></h3><ol className="timeline">{selected.history.map(r => <li key={r.id}>
          <div><strong>第 {r.id} 轮{r.rule === "审查 PR" ? " · 审查" : r.rework ? ` · 返工第 ${r.rework} 轮` : ""} · {runState(r)}</strong><time>{clock(r.created_at)} → {clock(r.finished_at)}</time></div>
          <Markdown text={r.summary || r.error || "还没有结论"} repo={selected.repo} /><RunResultLine result={r.result} />{r.link && <small>会话：{r.link}</small>}
        </li>)}</ol></>}
      </section>
      {online && <LinkedPrList repo={selected.repo} result={linked} failed={linkedError} />}
      {detailError && <div className="error" role="alert">{detailError}</div>}
      {detail?.notice && <div className="notice-line" role="status">{detail.notice}</div>}
      <section className="detail-section"><h3>正文{detail?.author && <small>{detail.author} · {clock(detail.created_at)}</small>}</h3>{detail ? detail.body ? <Markdown text={detail.body} repo={selected.repo} /> : <p className="meta">（没有正文）</p> : !detailError && <p className="meta">正在读取…</p>}</section>
      {detail && detail.comments.length > 0 && <section className="detail-section"><h3>评论 <small>{detail.comments.length}</small></h3><ol className="timeline">{detail.comments.slice().sort((a, b) => a.created_at.localeCompare(b.created_at)).map((c, n) => <li key={n}><div><strong>{c.author}</strong><time>{clock(c.created_at)}</time></div><Markdown text={c.body} repo={selected.repo} /></li>)}</ol></section>}
      <p className="meta">创建于 {day(selected.created_at)} · 更新于 {day(selected.updated_at)}{selected.author && ` · ${selected.author}`}</p>
    </Modal>}
    {pluginsOpen && <Plugins close={() => setPluginsOpen(false)} changed={() => void loadWechat()} />}
    {messages && <Modal title="最近 GitHub 消息" close={() => setMessages(null)}><Inbox online={online} feed={feed} initialProject={messages.project} onProject={id => { setMessages(null); closeIssue(); openProject(id); }} /></Modal>}
  </div>;
}
