export interface Project { id: string; name: string; repo_path: string; enabled: boolean; group: string; repository: string; issue_repository: string; source_status: string; last_commit_at: string | null; summary: string; summary_source: string; issue_scope: string; subgroup: string; tags: string[]; archived?: boolean; archived_at?: string }
// archived：projects.toml 里 archived = true 的项目，不在 projects 里，只在总览的「已归档」列出
export interface Payload { projects: Project[]; archived?: Project[]; subgroups?: Record<string, string[]>; generated_at: string; commit_window: { since: string; checked_at: string | null; github_error: string } }
export async function request<T>(path: string, data?: unknown): Promise<T> {
  const response = await fetch(path, {
    cache: "no-store",
    ...(data === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json", "X-Paihuo-Request": "1" }, body: JSON.stringify(data) }),
  });
  const result = await response.json();
  if (!response.ok) throw Error(result.error || `请求失败 (${response.status})`);
  return result as T;
}
export function embedded(): Payload | null {
  try { return JSON.parse(document.getElementById("paihuo-data")?.textContent || ""); }
  catch { return null; }
}
// 回执最后一行的结果：pr 开了 PR（带链接）；no_change 没东西可改；blocked 卡住了；unknown 没写结果行或读不到回执；failed 这一轮没跑完。还在跑的是 null
// review 是审查一轮的结论（verdict）
// STALE 只在审查回执里：派出后 PR 又有新提交，这一轮没审
export type Verdict = "APPROVE" | "CHANGES_REQUIRED" | "WAIT" | "STALE";
export interface RunResult { kind: "pr" | "no_change" | "blocked" | "unknown" | "failed" | "review"; pr?: number; url?: string; reason?: string; verdict?: Verdict }
// rule 是中台规则名（「处理 issue」或「审查 PR」）；rework 是返工第几轮，不是返工为 0
export interface IssueRun { id: number; state: string; summary: string; error: string; finished_at: string | null; created_at: string; link: string; result?: RunResult | null; rule?: string; rework?: number; pr_link?: string }
// 最近一次审查：verdict 还在审时为 null；current 为真表示结论对 PR 现在的 head 有效；moved 表示审完后 PR 有了新提交、等重新审查；
// spent 表示这个提交已经返工过一轮、那一轮结束了却没推新提交（等你决定）；sha 审的提交、head PR 现在的提交（都是前 12 位，不知道时为空）；hold 是监看暂时不派审查的原因（CI 没过等）；
// reworks 已返工几轮，max 最多几轮。没审过、只有 hold 时 id 为 null
export interface ReviewState { id: number | null; state: string; active: boolean; verdict: Verdict | null; current: boolean; moved: boolean; spent?: boolean; sha: string; head: string; hold: string; reworks: number; max: number; url: string; at: string | null }
// tier：提问的档位，ask 是【问你】（等你定），default 是【默认过】（Agent 已按推荐做了，不改就算定了）；处理结果（notice）为空
export interface Question { id: number; kind: string; title: string; text: string; options: string[]; recommend: number | null; created_at: string; tier?: "ask" | "default" | "" }
export interface Issue {
  subject: string; repo: string; number: number; project_id: string; project_name: string; title: string; url: string;
  labels: string[]; author: string; comments: number; created_at: string; updated_at: string; scope: string;
  // push：只读与你有关（scope 为 mine）、但你有推送权限的仓库（登记里 push_access = true），交给后在原仓库开 PR，不 fork
  push?: boolean;
  stage: string; stage_label?: string; review?: ReviewState | null; run: IssueRun | null; waiting: Question[]; handed_at: string | null; history: IssueRun[];
  // label_only：只有标签、没有运行记录；lost：显式交给后超过 15 分钟没有新的一轮
  stalled: "" | "label_only" | "lost";
  // 这一行的 Agent 流程：issue 是「处理 issue」，feedback 是「处理 issue 留言」（「帮我回复」「处理并回复」）
  flow?: "" | "issue" | "feedback";
  next: NextStep;
  // 待你回那一行的建议（只需回复 / 需要处理，personal/reply_mode.py），不在待你回的是 null；mode 是这一轮交给时选的模式
  hint?: ReplyHint | null; mode?: "" | ReplyMode;
  // their_at：对方最后一条留言的时间（设暂时不处理时记下）；snooze：你设为暂时不处理，这时 next.bucket 是 hold
  their_at?: string; snooze?: Snooze | null;
  // switch：「帮我回复」那一轮说要改代码，草稿和详情里出「改用处理并回复」
  switch?: Switch | null;
}
// Agent 上一轮已有结论、之后没有新动静（personal/settled.py）：decide 要你定，nothing 不用动；line 是 Agent 要你定的那一句或它的结论
export interface Settled { kind: "decide" | "nothing"; run: number; rule: string; flow: string; line: string; since: string; finished_at: string }
// 「帮我回复」（只回复）那一轮说这条要改代码：line 是那一轮的结论
export interface Switch { run: number; at: string; line: string }
// 暂时不处理（personal/snooze.py，只记本机 .personal/snoozed.json）：line 是行上那一句，until 空是「有新动静再说」，from 是原来的类别
export interface Snooze { at: string; until: string; line: string; from: string }
export type SnoozeWait = "news" | "1d" | "1w";
// 插件弹窗（personal/plugins 的 overview）：每个插件装没装、第一次装要不要给配置、「装好了没有」的几项检查（ok 为 null 是还说不准）
export interface PluginCheck { key: string; label: string; ok: boolean | null; detail: string; hint: string }
export interface PluginInfo { id: string; name: string; installed: boolean; needs_config: boolean; checks: PluginCheck[] }
export interface PluginsReport { plugins: PluginInfo[]; lines?: string[] }

// 微信（可选，personal/plugins/wechat/inbox.py）：没有 .personal/wechat.json 时 enabled 是 false，页面上不出现任何微信的东西。
// 一行是「一类信号 × 一个会话」；hits 是这一行的几条（每条几句话和上文），text 是行上那一两句；bucket 是 reply（计入角标）、look 或 hold
export type WechatKind = "private" | "at_me" | "quote_me" | "name_mention" | "at_all" | "pending_to_me" | "refunded_to_me" | "pending_from_me" | "red_packets" | "missed_calls";
export interface WechatLine { time: string; from: string; text: string }
export interface WechatHit { ts: number; messages: WechatLine[]; context: WechatLine[]; more?: string }
export interface WechatRow {
  key: string; kind: WechatKind; label: string; icon: string; chat: string; ts: number; at: string; text: string[]; notes: string[];
  hits: WechatHit[]; count: number; low_signal: boolean; bucket: "reply" | "look" | "hold"; snooze: Snooze | null;
}
// updated_at：微信数据（最近一次成功解析）的时间；hint：没权限、技能或 python 不对、检查失败时的一句提示
export interface WechatReport { enabled: boolean; rows?: WechatRow[]; hidden?: number; updated_at?: string; checked_at?: string; refreshed_at?: string; running?: boolean; hint?: string; has_data?: boolean }
// 「待你回」交给 Agent 的两种模式：reply 帮我回复（只写回复草稿，不改代码、不推送、不开或关 PR），handle 处理并回复（改代码、推送或开 PR，再写回复草稿）
export type ReplyMode = "reply" | "handle";
// 每行的建议：label 是「只需回复」或「需要处理」，why 是依据（谁说了什么），只是提示，两个按钮都能点
export interface ReplyHint { mode: ReplyMode; label: string; why: string }
// 下一步该谁做（personal/next_step.py）；button 为空就不出按钮，drawer_only 的只在详情里出
export interface NextStep {
  key: "flow" | "pr" | "close" | "reply" | "wait" | "todo" | "look" | "hold" | "record" | "decide" | "seen"; name: string; line: string; next: string; why: string;
  who: string; at: string; quote: string; url: string; prs: { number: number; title: string; url: string }[];
  action: "" | "issue" | "feedback"; drawer_only: boolean; button: string;
  // 收件箱筛选（合并后的）：PR 评审中、待关闭、等对方回并成 wait；Agent 等你决定、等你看和派发卡住的归 reply；
  // hold 是搁置（项目暂停、引用它的总表写了暂停、久未动），record 是记录（总表、跟踪、调研一类），都不出按钮
  bucket?: "reply" | "todo" | "agent" | "wait" | "look" | "hold" | "record";
  // 待你定（decide，归待你回）、Agent 看过（seen，归待你看）：Agent 上一轮的结论；交给要带 force（详情里的「仍交给 Agent」）
  settled?: Settled | null;
  // new：别人（真人）在你最后一次动作之后说了几条（personal/next_step.news）；notice：凭未读通知先归的待你回，正在重读这一条
  new?: number; notice?: boolean;
}
// 收件箱批量交给 issue（/api/issues/handoff）：逐条结果
export interface BatchHandoff { results: { subject: string; ok: boolean; flow: "" | "issue" | "feedback"; handed_at: string | null; error: string }[]; handed: number; routed: string; error: string }
export interface Handoff { subject: string; handed_at: string; routed: string; error: string; flow?: "issue" | "feedback" }
export interface IssueReport { issues: Issue[]; label: string; error: string; autoloop_error: string; fetched_at: string | null; limited: string; refreshing: boolean; upstream: Record<string, Upstream> }
export interface Upstream { open_prs: number; 待审: number; 要改: number; 草稿: number; 已合并: number; issues: number }
export interface IssueDetail {
  repo: string; number: number; title: string; body: string; url: string; labels: string[]; author: string;
  created_at: string; updated_at: string; comments: { author: string; body: string; created_at: string }[];
  notice: string;
}
export interface LinkedPr { repo: string; number: number; title: string; url: string; author: string; draft: boolean; review: string; checks: string }
export interface LinkedPrs { prs: LinkedPr[]; error: string; limited: string }
// 个人助理目录（/api/brief、/api/boards）：每日简报和事项板块，只读
export interface BriefFailure { source: string; reason: string }
export interface Brief { date: string; markdown: string; path: string; modified: string; failures: BriefFailure[] }
export interface BriefReport {
  root: string; dir: string; today: string; schedule: string; next_run: string;
  // latest：最新的文件日期（可能是用 --date 预览的明天）；default：不带日期时显示的那天
  dates: string[]; latest: string | null; default: string | null; brief: Brief | null; notice: string; error: string;
}
export interface BriefRun extends BriefReport { ok: boolean; output: string; previous: string }
export interface Board {
  name: string; summary: string; status: string | null; next: string | null; next_missing: boolean; next_gaps: string[];
  readme: string; path: string; abs_path: string; modified: string;
  news: BoardNews[];  // 板块 动态.md 里最近 5 条按日期的条目，新的在前；没有 动态.md 是空列表
}
export interface BoardNews { date: string; text: string }
export interface BoardReport { root: string; index: string; boards: Board[]; error: string }
// 学习课题目录（/api/learning）：INDEX.md 的每个课题和已写的各篇；只读，例外是保存反馈和「写下一篇」
export interface LearningArticle { no: string; title: string; html: boolean; feedback: string; filled: boolean; version: string }
// 「写下一篇」的进度：idle 没交过；pending 已交给、还没派出；lost 交给超过 15 分钟没派出；running 在写；done 写完；blocked 写不成；failed 没跑完；unknown 回执没写结果
export interface LearningHandoff { state: "idle" | "pending" | "lost" | "running" | "done" | "blocked" | "failed" | "unknown"; no: string; handed_at: string | null; reason?: string; finished_at?: string | null }
export interface LearningTopic {
  name: string; progress: string; updated: string; next: string; exists: boolean; path?: string; articles: LearningArticle[];
  next_no: string; home: boolean; plan: boolean; unfilled: string[]; handoff: LearningHandoff;
}
// 小说导读（导读根目录下带 index.html 的书目录）；阅读状态存在工作台运行数据 .personal/novel-status.json，不写导读目录
export type NovelStatus = "unread" | "reading" | "done";
export interface NovelBook { name: string; status: NovelStatus }
export interface NovelReport { root: string; books: NovelBook[]; compare: string; error: string }
export interface LearningReport { root: string; roots?: string[]; topics: LearningTopic[]; default: string; error: string; hub_error: string; novels?: NovelReport }
export interface LearningSaved { topic: string; no: string; feedback: string; filled: boolean; version: string }
// 健康（/api/health/daily）：助理目录 资料/raw/health/zepp-daily.csv 每天一行，只读。bed / wake 是相对醒来那天 0 点的分钟数（23:30 入睡是 -30）
export interface HealthDay {
  date: string; sleep_min: number | null; deep_min: number | null; light_min: number | null; rem_min: number | null; awake_min: number | null;
  bed: number | null; wake: number | null; hr_avg: number | null; hr_resting: number | null; hr_estimated: boolean; steps: number | null; partial: boolean;
}
export interface HealthSummary {
  nights: number; sleep_avg: number | null; aim: number; keep: number; short: number; bed_avg: number | null; wake_avg: number | null; bed_spread: number | null; late: number;
  deep_share: number | null; rem_share: number | null; resting_avg: number | null; resting_low: number | null; resting_high: number | null; resting_estimated: boolean;
  steps_avg: number | null; steps_days: number;
}
export interface HealthReport {
  today: string; updated: string; first_date: string; last_date: string; stale_days: number | null; stale: string; days: HealthDay[]; summary: HealthSummary | null;
  findings: string[]; targets: { keep: number; aim: number }; update_hint: string; empty: string; error: string;
  analysis: { text: string; through: string; written: string; outdated: boolean } | null;  // 助理目录 分析.md：AI 在导入后写的解读
}
// 回顾（/api/review）：助理目录 回顾/周、回顾/月 的每一期；只读，例外是保存三问的回答、运行回顾脚本、交给 Agent 写总结和三问
export type ReviewKind = "week" | "month";
export type ReviewBlock = "summary" | "top3" | "questions" | "projects";
export interface ReviewQuestion { no: number; text: string; answer: string }
export interface ReviewCategory { name: string; hours: number; share: number | null }
export interface ReviewNumbers { hours?: number; days?: number; categories?: ReviewCategory[]; merged_prs?: number; closed_issues?: number }
export interface ReviewPeriod {
  kind: ReviewKind; label: string; start: string; end: string; partial: boolean; generated: string; html: boolean; version: string;
  summary: string; top3: string[]; questions: ReviewQuestion[]; projects: string[];
  has: Record<ReviewBlock, boolean>; written: Record<ReviewBlock, boolean>; numbers: ReviewNumbers; handoff?: LearningHandoff;
}
export interface ReviewReport {
  today: string; current: Record<ReviewKind, string>; schedule: Record<ReviewKind, string>; next_run: Record<ReviewKind, string>;
  weeks: ReviewPeriod[]; months: ReviewPeriod[]; error: string; hub_error: string;
}
export interface ReviewRun extends ReviewReport { ok: boolean; kind: ReviewKind; label: string; existed: boolean }
export interface ReviewSaved { period: ReviewPeriod; note: string }
export interface ReviewHanded { kind: ReviewKind; label: string; handed_at: string; error: string; handoff: LearningHandoff }
export interface LearningHanded { topic: string; no: string; handed_at: string; error: string; handoff: LearningHandoff }
// 项目详情页的「我的 PR」和「近 30 天已完成的 issue」（/api/projects/<项目>/activity）
// news：GitHub 通知未读时的新消息标记（count 为 null 是条数不明）；last：最近动态，说不清谁做了什么时 who、what 为空
export interface PrNews { count: number | null; more: boolean; who: string; what: string; at: string; text: string; url: string }
export interface LastActivity { at: string; who: string; what: string }
export interface ProjectPr {
  number: number; title: string; url: string; state: "open" | "merged" | "closed"; draft: boolean; review: string; checks: string;
  created_at: string | null; updated_at: string | null; merged_at: string | null; closed_at: string | null;
  news?: PrNews | null; last?: LastActivity;
}
// 项目页顶部的未读消息：这个项目未读的 issue / PR 通知，最新的在前，最多 3 条
export interface UnreadMessage extends PrNews { number: number; title: string; kind: "pr" | "issue" }
export type ClosedBy = { kind: "pr"; number: number; url: string; title: string; commit?: string } | { kind: "commit"; oid: string; url: string } | { kind: "manual"; actor: string };
export interface DoneIssue { number: number; title: string; url: string; author: string; created_at: string | null; closed_at: string | null; reason: string; by: ClosedBy }
export interface Activity {
  repo: string; available: boolean; since?: string; prs?: { open: ProjectPr[]; merged: ProjectPr[]; closed: ProjectPr[]; news?: ProjectPr[] };
  unread?: UnreadMessage[]; unread_total?: number; news_note?: string;
  done_issues?: DoneIssue[]; counts?: { pending: number; open: number; merged: number }; loaded?: boolean;
  fetched_at?: string | null; closed_fetched_at?: string | null; refreshing?: boolean; limited?: string; error?: string;
}
// 收件箱的「近 30 天已完成」：全部项目近 30 天已完成的 issue（/api/issues/done）
export interface ProjectDoneIssue extends DoneIssue { repo: string; subject: string; project_id: string; project_name: string }
export interface DoneReport { issues: ProjectDoneIssue[]; since: string; loaded: boolean; fetched_at: string | null; refreshing: boolean; limited: string; error: string }
// 「PR 待处理」（/api/prs/feedback）：我开着的 PR 里，对方真人的回复比我最晚的动作还晚的，和交给过 Agent 的
// 回执结果：pushed 推回原分支（带提交链接）；closed 按对方意思关了；replied 只回复；no_action 不用动；blocked 卡住；unknown / failed 要人看
export interface PrRunResult { kind: "pushed" | "closed" | "replied" | "no_action" | "blocked" | "unknown" | "failed"; commit?: string; url?: string; reason?: string }
// maintainer：OWNER / MEMBER / COLLABORATOR 或被请求的评审人；role 是给人看的身份（路人、贡献者……）
export interface PrSaid { who: string; what: string; at: string; text: string; url: string; path: string; new: boolean; maintainer: boolean; role: string }
export interface PrRun { id: number; state: string; summary: string; error: string; finished_at: string | null; created_at: string; result?: PrRunResult | null }
export interface PrRow {
  subject: string; repo: string; number: number; title: string; url: string; project_id: string; project_name: string; local: string;
  open: boolean; draft: boolean; review: string; pending: boolean; new: number; their_at: string; mine_at: string; said: PrSaid[];
  handed_at: string | null; stage: string; waiting: Question[]; run: PrRun | null; history: PrRun[];
  stalled: "" | "label_only" | "lost"; section: "active" | "recent";
  // unseen：回执说推送或回复了，时间线里却还没有你账号在这一轮之后的动作；by_me：你自己在那之后动过手，收进最近处理过
  unseen: boolean; by_me: boolean;
  // state：按谁最后说话分的标签（reply 待你回、agent Agent 处理中、wait 等对方、done 已合并或关闭且没事要做）；
  // new：你最后一次动作之后对方新增的条数（等对方的行为 0）；author：谁开的（PR 都是你）；旧后端没给时由页面推出来
  state?: RowState; author?: string;
  // hint：只需回复还是需要处理的建议；mode：这一轮交给时选的模式（旧记录为空，按处理并回复）
  hint?: ReplyHint; mode?: "" | ReplyMode;
  // their_last：对方（不是你、不是机器人）最后一条回复、评审或提交的时间；snooze：你设为暂时不处理，这时 state 是 hold
  their_last?: string; snooze?: Snooze;
  // settled：Agent 上一轮已有结论、之后没有新动静，列表上不能交，详情里「仍交给 Agent」带 force；switch：只回复那一轮说要改代码
  settled?: Settled | null; switch?: Switch | null;
}
export type RowState = "reply" | "agent" | "wait" | "done" | "hold";
export interface PrReport { prs: PrRow[]; login: string; error: string; limited: string; note: string; autoloop_error: string; refreshing: boolean; fetched_at: string | null }
export interface PrHandoff { handed: { subject: string; handed_at: string }[]; skipped: { subject: string; reason: string }[]; routed: string; error: string }
// 收件箱里一条 PR 的处理情况（/api/prs/feedback 的行补上 kind）
// 回执结果：除了 PR 反馈的几种，issue 留言还有 pr（开了 PR 并回复，prs 是编号，urls 是链接）
export type FeedRunResult = PrRunResult | { kind: "pr"; prs: number[]; urls?: string[]; reason?: string };
export interface FeedRun extends Omit<PrRun, "result"> { result?: FeedRunResult | null }
export type FeedSaid = Pick<PrSaid, "who" | "what" | "at" | "text" | "url" | "path" | "new">;
// body_only：别人开的 issue 只有正文、还没人留言，也算待你回
export interface FeedRow extends Omit<PrRow, "said" | "run" | "history"> { kind: "pr" | "issue"; said: FeedSaid[]; run: FeedRun | null; history: FeedRun[]; body_only?: boolean }
// 中台里没结束的轮次（/api/agents，监看脚本的不算）：running 在跑，queued 排队，unknown 交给后超过 5 分钟没有回音
export interface AgentRun { id: number; rule: string; subject: string; started_at: string | null; state: "running" | "queued" | "unknown" }
// limit：最多同时几个（规则的 parallel 和本机接单程序容量 capacity 里小的；capacity 为 null 是本机没有接单程序）
export interface AgentsReport { running: number; queued: number; unknown: number; agents: AgentRun[]; parallel: number; capacity: number | null; limit: number; error: string }
export interface FeedHandoff { handed: { subject: string; kind: "pr" | "issue"; handed_at: string }[]; skipped: { subject: string; reason: string }[]; routed: string; error: string }
// 回复草稿（/api/replies/drafts，personal/reply_drafts.py）：「帮我回复」交给 Agent 后，Agent 把要发的回复写成草稿，你在「草稿待确认」里看、改，确认后才发
// review_comment_id：PR 行内评审线程首条意见的 id（回到那条线程里）；没有就是 issue / PR 下的普通评论
export interface DraftReply { id: string; to: string; body: string; review_comment_id: number | null; path: string }
// based_on：生成草稿时对方最后一条评论；error：上次发送失败的原因（草稿还在）
export interface ReplyDraft {
  subject: string; kind: "issue" | "pr"; created_at: string; work: string; replies: DraftReply[];
  based_on: { id: string; at: string; who: string } | null; based_error?: string; error?: string;
}
export interface DraftsReport { drafts: ReplyDraft[] }
// issue 草稿（personal/issue_drafts.py）：从微信里提炼出的需求拟成的 issue 草稿，你确认后才创建；excerpt 是依据的原话，只在本机
export interface IssueDraft {
  id: string; source: { chat: string; kind: string }; excerpt: { time: string; from: string; text: string }[]; message_at: string;
  project_id: string; repo: string; candidates: string[]; title: string; body: string; labels: string[];
  status: "pending" | "failed" | "skipped"; created_at: string; error: string;
  // skip_reason：没拟的话题（status skipped）模型为什么没拟成草稿
  // new_repo：提议新建的私有仓库（和 repo 二选一）；repo_created：这条草稿已经建好的仓库，再点创建不会重复建
  new_repo?: { name: string; description: string }; repo_created?: string; skip_reason?: string;
}
export interface IssueDraftProject { id: string; name: string; repo: string }
// 「读新需求」这一轮的状态：found 读到几条，saved 新拟了几条草稿，skipped 新交来几个没拟的话题
// refresh_notes：这一轮哪个微信号的明文库没刷新成（读到的是上一次刷新时的记录）
export interface ScanState { status: "idle" | "running" | "done" | "failed"; requested_at: string; finished_at: string; found: number; saved: number; skipped?: number; error: string; refresh_notes?: string[] }
// 创建过、做完后要 @ 人、还没 @ 的 issue；error 是上一轮没看成或没发成的原因
export interface IssueMention { title: string; url: string; logins: string[]; error: string }
// skipped：模型认为相关但没拟的话题（不算待评审，可以补成草稿）；owner：新建仓库建在这个账号名下（一律私有）；people：微信里的名字 → GitHub 账号（记下的对应，只含这些草稿里的发言人）
export interface IssueDraftsReport { drafts: IssueDraft[]; skipped?: IssueDraft[]; projects: IssueDraftProject[]; scan: ScanState; owner?: string;
  people?: Record<string, string>; mentions?: IssueMention[] }
export interface DraftNewer { id: string; who: string; at: string; text: string; url: string }
// 确认发送的结果：ok 全部发出；stale 对方在草稿生成后又回了几条，这次没发；error 发送失败的原因（sent 是已发出的条数）
export interface SendResult { ok: boolean; sent: number; stale?: number; newer?: DraftNewer[]; error?: string }
// 自媒体页「麦田」标签（personal/media.py）：state ok 读到了，down 麦田没在运行（start 是启动命令），denied 口令不对，error 其他；口令不在这里
export interface MaitianGroup { state: string; label: string; tone: "" | "ok" | "wait" | "bad"; platforms: string[] }
export interface MaitianEpisode { id: string; episode: number | null; title: string; schedule: string; groups: MaitianGroup[]; waiting: string[] }
export interface MaitianFollower { platform: string; name: string; total: number; week: number | null; day: string }
export interface MaitianData { name: string; episodes: MaitianEpisode[]; waiting: number; followers: MaitianFollower[]; problems: string[]; now: string }
export interface MaitianReport { state: "ok" | "down" | "denied" | "error"; demo: boolean; source: "local" | "maitian" | "demo"; start: string; maitian: MaitianData | null; error: string }
// 自媒体页「在做的内容」标签：助理目录 事项/自媒体与内容/README.md 里那张表，每格原样
export interface ContentRow { content: string; progress: string; publish: string }
export interface ContentReport { rows: ContentRow[]; modified: string; error: string }
