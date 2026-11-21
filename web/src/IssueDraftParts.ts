// issue 草稿（后端 personal/issue_drafts.py）：从微信里提炼出的需求拟成的 GitHub issue 草稿，在收件箱工具条右端「新需求」按钮打开的抽屉里，
// 不进收件箱的列表。这里是抽屉用的算法：按钮上的数字、一行上的仓库标签、仓库下拉、创建前的检查、请求体、状态那一句。
// 你点创建才用你的账号创建（提议新建仓库的先建私有仓库），原话只在本机。不写 JSX，node --test 可以直接加载这个文件。
import type { IssueDraft, IssueDraftProject, IssueDraftsReport, ScanState } from "./api";

/** 仓库下拉里「新建仓库…」那一项的值（不会和 owner/name 撞）。 */
export const NEW_REPO = "+new";

/** 要开在哪：现有仓库（repo），或先新建一个私有仓库（newRepo，此时 repo 是空的）。 */
export interface Target { repo: string; newRepo: { name: string; description: string } | null }

/** 有没有「新需求」这个按钮：启用了微信，或已经有草稿、读过一轮。 */
export const showNeeds = (wechatEnabled: boolean, report: Pick<IssueDraftsReport, "drafts" | "scan"> | null | undefined) =>
  wechatEnabled || !!report && (report.drafts.length > 0 || report.scan.status !== "idle");

/** 展开后第一行写的来源：微信会话写「微信 · 会话名」；录音那几种的名字里已经带了来源（「得到 · 周会」）。 */
export const sourceLine = (s: { chat: string; kind: string }) => !s.kind || s.kind === "wechat" || s.kind === "group" || s.kind === "private"
  ? `微信${s.chat ? ` · ${s.chat}` : ""}` : s.chat || "录音";

/** 草稿交来时要开在哪。 */
export const targetOf = (d: Pick<IssueDraft, "repo" | "new_repo">): Target =>
  d.new_repo && !d.repo ? { repo: "", newRepo: { ...d.new_repo } } : { repo: d.repo, newRepo: null };

/** 下拉选了一项之后要开在哪；选「新建仓库…」时留着已经填的名字和说明。 */
export const pick = (value: string, before: Target): Target =>
  value === NEW_REPO ? { repo: "", newRepo: before.newRepo ?? { name: "", description: "" } } : { repo: value, newRepo: null };

/** 一行上的仓库标签：新建的写名字和「私有」，现有的写仓库短名，没选的写「未选仓库」。 */
export function repoTag(t: Target): { text: string; tone: "" | "warn" } {
  if (t.newRepo) return { text: `新建 ${t.newRepo.name || "仓库"} · 私有`, tone: "warn" };
  return { text: t.repo ? t.repo.split("/").pop()! : "未选仓库", tone: "" };
}

/** 收起时点 ✓ 先展开给你看，不直接创建：要新建仓库的、还没选仓库的、上次没建成的。 */
export const lookFirst = (d: Pick<IssueDraft, "repo" | "new_repo" | "error">) => !d.repo || !!d.new_repo || !!d.error;

/** 仓库下拉：候选项目在前（照草稿给的顺序），其余照登记表顺序；没有重复。 */
export function repoChoices(d: Pick<IssueDraft, "candidates" | "project_id">, projects: IssueDraftProject[]): IssueDraftProject[] {
  const first = [d.project_id, ...d.candidates].filter(Boolean);
  const head = first.map(id => projects.find(p => p.id === id)).filter((p): p is IssueDraftProject => !!p);
  return [...new Set([...head, ...projects])];
}

/** 仓库名能不能用（和后端同一条规则）：字母、数字和 . _ -，1–100 字，不以 . 开头，不以 .git 结尾。 */
export const repoNameOk = (name: string) => /^[A-Za-z0-9_-][A-Za-z0-9._-]{0,99}$/.test(name) && !name.toLowerCase().endsWith(".git");

/** 这条草稿原话的发言人（照出现的顺序，不重复）。 */
export const sendersOf = (d: Pick<IssueDraft, "excerpt">) => [...new Set(d.excerpt.map(m => m.from).filter(Boolean))];

/** 做完后 @ 谁：每个发言人一格，记下过对应的先带出账号。 */
export const mentionOf = (d: Pick<IssueDraft, "excerpt">, people: Record<string, string> | undefined): Record<string, string> =>
  Object.fromEntries(sendersOf(d).map(name => [name, people?.[name] ?? ""]));

/** GitHub 账号名能不能用（和后端同一条规则）；前面带 @ 也行，留空是不 @。 */
export const loginOk = (value: string) => { const v = value.trim().replace(/^@/, ""); return !v || /^[A-Za-z0-9](?:-?[A-Za-z0-9]){0,38}$/.test(v); };

/** 填了的账号（去掉 @ 和空白，不重复）。 */
export const mentionLogins = (mention: Record<string, string>) => [...new Set(Object.values(mention).map(v => v.trim().replace(/^@/, "")).filter(Boolean))];

/** 创建前的检查：缺什么；都有是空串。 */
export function missing(title: string, body: string, t: Target, mention: Record<string, string> = {}) {
  if (t.newRepo) {
    if (!t.newRepo.name.trim()) return "先写仓库名";
    if (!repoNameOk(t.newRepo.name.trim())) return "仓库名只能用字母、数字和 . _ -";
  } else if (!t.repo) return "先选仓库";
  if (!title.trim()) return "标题是空的";
  if (!body.trim()) return "正文是空的";
  if (!Object.values(mention).every(loginOk)) return "做完后 @ 的要填 GitHub 账号名";
  return "";
}

/** 创建的请求体：repo 和 new_repo 只带一个；mention 是 {微信里的名字: GitHub 账号}，留空的也带上（后端据此不再记这个对应）。 */
export function createBody(id: string, title: string, body: string, t: Target, mention?: Record<string, string>) {
  const who = mention ? { mention: Object.fromEntries(Object.entries(mention).map(([name, v]) => [name, v.trim().replace(/^@/, "")])) } : {};
  return t.newRepo
    ? { id, title, body, new_repo: { name: t.newRepo.name.trim(), description: t.newRepo.description.trim() }, ...who }
    : { id, title, body, repo: t.repo, ...who };
}

/** 主按钮上的字。 */
export const createLabel = (t: Target, busy = false) => busy ? "创建中…" : t.newRepo ? "建仓库并创建 issue" : "创建 issue";

/** 创建成了之后那一行灰字。 */
export const createdLine = (result: { repo_created?: boolean }) => result.repo_created ? "已建仓库并创建 · 在收件箱" : "已创建 · 在收件箱";

/** 上传录音之后那一句：正在转写；已有一轮在跑时，这段要再点一次才读。 */
export const uploadedLine = (r: { queued?: boolean }) => r.queued ? "已上传；有一轮正在读，结束后再点一次「读新需求」" : "已上传，正在本机转写、拟稿…";

/** 这一轮「读新需求」还在跑。 */
export const scanning = (s: Pick<ScanState, "status"> | null | undefined) => s?.status === "running";

const clock = (value: string) => value ? new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "";

/** 按钮旁边那一句：正在读、读到几条新拟几条（有没拟的话题再说几个）、没读成的原因；哪个微信号的库没刷新成附在后面。还没读过是空的。 */
export function scanLine(s: ScanState | null | undefined) {
  if (!s || s.status === "idle") return "";
  if (s.status === "running") return "正在读…";
  if (s.status === "failed") return `没读成：${s.error || "没有说明"}`;
  const stale = (s.refresh_notes ?? []).filter(Boolean);
  return `读到 ${s.found} 条，新拟 ${s.saved} 条草稿${s.skipped ? `，${s.skipped} 个话题没拟` : ""}${s.finished_at ? `（${clock(s.finished_at)}）` : ""}${stale.length ? ` · ${stale.join("；")}` : ""}`;
}

/** 那一句要不要标成有问题：没读成，或有库没刷新成。 */
export const scanBad = (s: ScanState | null | undefined) => s?.status === "failed" || s?.status === "done" && (s.refresh_notes ?? []).some(Boolean);
