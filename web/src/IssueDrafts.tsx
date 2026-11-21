import { useEffect, useRef, useState } from "react";
import Confirm from "./Confirm";
import { request } from "./api";
import type { IssueDraft, IssueDraftProject, IssueDraftsReport, ScanState } from "./api";
import { NEW_REPO, sourceLine, uploadedLine, createBody, createLabel, createdLine, lookFirst, mentionLogins, mentionOf, missing, pick, repoChoices, repoTag, scanBad, scanLine, scanning, sendersOf, targetOf } from "./IssueDraftParts";

// 「新需求」：收件箱工具条右端的小按钮和它打开的右侧抽屉（后端 personal/issue_drafts.py）。
// 抽屉里是从微信拟的 issue 草稿，一行一条；点标题展开可改标题、正文、仓库（或新建私有仓库），依据的原话只在本机，不进 issue。
const errorText = (e: unknown) => e instanceof Error ? e.message : "操作失败，请重试";
const when = (value: string) => value && !Number.isNaN(Date.parse(value)) ? new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : value;
const GONE_MS = 2000;  // 创建成了的那一行灰字留这么久
interface Created { ok: boolean; url?: string; repo?: string; repo_created?: boolean; error?: string }
interface Gone { id: string; at: number; line: string; url: string }

/** 工具条上的小按钮：有待评审的带数字，没有时只是不起眼的字。 */
export function NeedsButton({ count, open, toggle }: { count: number; open: boolean; toggle: () => void }) {
  return <button className={`needs-button${count ? " has" : ""}`} aria-expanded={open} aria-controls="needs-drawer" onClick={toggle}
    title="从微信等来源拟出的 issue 草稿，等你评审">
    <svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"><path d="M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z" /></svg>
    新需求{count > 0 && <em>{count}</em>}</button>;
}

function ScanHead({ scan, changed }: { scan: ScanState | null; changed: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const running = busy || scanning(scan);
  const start = async () => {
    setBusy(true); setError("");
    try { await request<ScanState>("/api/issue-drafts/scan", {}); changed(); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  };
  const pick = useRef<HTMLInputElement>(null);
  const [note, setNote] = useState("");
  const upload = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true); setError(""); setNote(`正在上传 ${file.name}…`);
    try {
      const response = await fetch("/api/issue-drafts/audio", { method: "POST", cache: "no-store", body: file,
        headers: { "Content-Type": "application/octet-stream", "X-Paihuo-Request": "1", "X-File-Name": encodeURIComponent(file.name) } });
      const result = await response.json();
      if (!response.ok) throw Error(result.error || `请求失败 (${response.status})`);
      setNote(uploadedLine(result)); changed();
    } catch (e) { setNote(""); setError(errorText(e)); } finally { setBusy(false); if (pick.current) pick.current.value = ""; }
  };
  const line = error ? `没成：${error}` : note && (busy || running) ? note : running ? "" : scanLine(scan);
  return <div className="needs-scan">
    <button disabled={running} onClick={() => void start()} title="读登记了的微信会话和新的录音、会议转写（得到、飞书妙记、本机录音文件），把里面的待办和需求拟成 issue 草稿；你确认后才创建">{running ? "正在读…" : "读新需求"}</button>
    <button className="ghost" disabled={busy} onClick={() => pick.current?.click()} title="上传一段录音：存在本机、在本机转写，再把里面的待办和需求拟成 issue 草稿">上传录音</button>
    <input ref={pick} type="file" hidden accept="audio/*,.m4a,.mp3,.wav,.aac,.flac,.ogg,.opus,.mp4,.mov,.webm" aria-label="选择录音文件" onChange={e => void upload(e.target.files?.[0])} />
    {line && <span className={`meta${error || scanBad(scan) ? " bad" : ""}`} role="status" title={line}>{line}</span>}
  </div>;
}

function NeedRow({ draft, projects, owner, people, changed, created }: {
  draft: IssueDraft; projects: IssueDraftProject[]; owner: string; people: Record<string, string>; changed: () => void; created: (line: string, url: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState(draft.title);
  const [body, setBody] = useState(draft.body);
  const [target, setTarget] = useState(() => targetOf(draft));
  const [mention, setMention] = useState(() => mentionOf(draft, people));
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [asking, setAsking] = useState(false);
  const choices = repoChoices(draft, projects);
  const lack = missing(title, body, target, mention);
  const names = sendersOf(draft);
  const at = mentionLogins(mention);
  const tag = repoTag(target);
  const failed = error || draft.error;

  const create = async () => {
    setBusy("create"); setError("");
    try {
      const result = await request<Created>("/api/issue-drafts/create", createBody(draft.id, title, body, target, mention));
      if (result.ok) { created(createdLine(result), result.url || ""); return; }
      setError(result.error || "没创建成，草稿还在"); setOpen(true);
      changed();  // 仓库建成而 issue 没建成时，草稿已经换成那个仓库
    } catch (e) { setError(errorText(e)); setOpen(true); } finally { setBusy(""); }
  };
  // 仓库建成了、issue 没建成：后端把草稿改成开在那个仓库，下拉跟着换，再点只建 issue
  useEffect(() => { if (draft.repo_created) setTarget(targetOf(draft)); }, [draft.repo_created, draft.repo]);
  const reject = async () => {
    setAsking(false); setBusy("reject"); setError("");
    try { await request("/api/issue-drafts/reject", { id: draft.id }); changed(); }
    catch (e) { setError(errorText(e)); setOpen(true); } finally { setBusy(""); }
  };
  const quick = () => { if (open || lookFirst(draft) || lack) setOpen(true); else void create(); };

  return <li className="needs-item">
    <div className="needs-row">
      <button className="needs-title" aria-expanded={open} onClick={() => setOpen(o => !o)}>
        <span className="needs-caret" aria-hidden="true">{open ? "▾" : "▸"}</span><span className="needs-text">{title || "（没有标题）"}</span></button>
      {failed && !open && <span className="tag bad" title={failed}>上次没建成</span>}
      <span className={`tag ${tag.tone}`} title={target.newRepo ? `确认后在 ${owner} 下新建私有仓库` : target.repo}>{tag.text}</span>
      {!open && <>
        <button className="needs-icon yes" disabled={!!busy} onClick={quick} aria-label={`创建：${title}`}
          title={lookFirst(draft) || lack ? "先展开看一眼再创建" : `在 ${target.repo} 创建 issue${at.length ? `，做完后 @${at.join(" @")}` : ""}`}>{busy === "create" ? "…" : "✓"}</button>
        <button className="needs-icon no" disabled={!!busy} onClick={() => setAsking(true)} aria-label={`不创建：${title}`} title="不创建">✕</button>
      </>}
    </div>
    {open && <div className="needs-edit">
      <p className="meta">{sourceLine(draft.source)}{draft.message_at ? ` · ${when(draft.message_at)}` : ""} · 发出去的只有标题、正文{draft.labels.length ? "和标签" : ""}</p>
      {failed && <p className="error" role="alert">{error || `上次没建成：${draft.error}`}</p>}
      <label><span>仓库{!draft.repo && !draft.new_repo && "（未归类：选一次，这个会话下次直接对应）"}</span>
        <select value={target.newRepo ? NEW_REPO : target.repo} disabled={!!busy} onChange={e => setTarget(pick(e.target.value, target))} aria-label="仓库">
          {!target.repo && !target.newRepo && <option value="">选一个仓库…</option>}
          {choices.map(p => <option key={p.id} value={p.repo}>{p.name === p.repo.split("/").pop() ? p.repo : `${p.name} · ${p.repo}`}</option>)}
          <option value={NEW_REPO}>新建仓库…</option>
        </select></label>
      {target.newRepo && <div className="needs-new-repo">
        <span className="meta"><span className="tag warn">私有</span> 建在 {owner} 下，确认后才建</span>
        <input type="text" value={target.newRepo.name} disabled={!!busy} placeholder="仓库名，例如 survey-h5" aria-label="新仓库名" spellCheck={false}
          onChange={e => setTarget({ repo: "", newRepo: { ...target.newRepo!, name: e.target.value } })} />
        <input type="text" value={target.newRepo.description} disabled={!!busy} placeholder="一句话说明" aria-label="新仓库说明" maxLength={200}
          onChange={e => setTarget({ repo: "", newRepo: { ...target.newRepo!, description: e.target.value } })} />
      </div>}
      <label><span>标题</span>
        <input type="text" value={title} disabled={!!busy} onChange={e => setTitle(e.target.value)} aria-label="issue 标题" /></label>
      <label><span>正文</span>
        <textarea value={body} rows={Math.min(14, Math.max(6, body.split("\n").length + 1))} disabled={!!busy} onChange={e => setBody(e.target.value)} aria-label="issue 正文" /></label>
      {draft.labels.length > 0 && <p className="meta">标签：{draft.labels.join("、")}</p>}
      {names.length > 0 && <div className="needs-mention">
        <span className="meta">做完后 @（填对方的 GitHub 账号，留空不 @；填一次下次自动带出）</span>
        {names.map(name => <label key={name}><span title={name}>{name}</span>
          <input type="text" value={mention[name] ?? ""} disabled={!!busy} placeholder="GitHub 账号" aria-label={`${name} 的 GitHub 账号`} spellCheck={false} autoCapitalize="off"
            onChange={e => setMention(m => ({ ...m, [name]: e.target.value }))} /></label>)}
      </div>}
      {draft.excerpt.length > 0 && <details className="needs-evidence"><summary>依据的消息（只在本机） <small>{draft.excerpt.length} 条，不进 issue</small></summary>
        <ol className="timeline wechat-lines muted">{draft.excerpt.map((m, n) => <li key={n}>
          <div>{m.from && <strong>{m.from}</strong>}<time>{when(m.time)}</time></div><p>{m.text}</p></li>)}</ol></details>}
      <div className="needs-actions">
        <button className="primary" disabled={!!busy || !!lack} onClick={() => void create()}>{createLabel(target, busy === "create")}</button>
        <button className="ghost" disabled={!!busy} onClick={() => setAsking(true)}>不创建</button>
        {lack && <span className="meta">{lack}</span>}
      </div>
    </div>}
    {asking && <Confirm title="不创建" text={`丢掉「${title}」这份草稿，不创建 issue；同一件事之后不会再拟稿。`} ok="不创建" close={() => setAsking(false)} confirm={() => void reject()} />}
  </li>;
}

/** 没拟的话题一行：标题和模型没拟的原因；点标题看它拟的正文和依据的原话。「补成草稿」挪到上面的待评审，「不要」和不创建一样。 */
function SkippedRow({ draft, changed }: { draft: IssueDraft; changed: () => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const act = async (kind: "promote" | "reject") => {
    setBusy(kind); setError("");
    try { await request(`/api/issue-drafts/${kind}`, { id: draft.id }); changed(); }
    catch (e) { setError(errorText(e)); } finally { setBusy(""); }
  };
  return <li className="needs-item">
    <div className="needs-row">
      <button className="needs-title" aria-expanded={open} onClick={() => setOpen(o => !o)}>
        <span className="needs-caret" aria-hidden="true">{open ? "▾" : "▸"}</span><span className="needs-text">{draft.title}</span></button>
      <button className="needs-icon yes" disabled={!!busy} onClick={() => void act("promote")} aria-label={`补成草稿：${draft.title}`} title="补成草稿，挪到上面待评审">{busy === "promote" ? "…" : "＋"}</button>
      <button className="needs-icon no" disabled={!!busy} onClick={() => void act("reject")} aria-label={`不要：${draft.title}`} title="不要（同一件事之后不再拟）">✕</button>
    </div>
    <p className="meta needs-reason">没拟：{draft.skip_reason || "没有说明"}{draft.source.chat ? ` · ${draft.source.chat}` : ""}{draft.message_at ? ` · ${when(draft.message_at)}` : ""}</p>
    {error && <p className="error" role="alert">{error}</p>}
    {open && <div className="needs-edit">
      <pre className="needs-preview">{draft.body}</pre>
      {draft.excerpt.length > 0 && <details className="needs-evidence"><summary>依据的消息（只在本机） <small>{draft.excerpt.length} 条</small></summary>
        <ol className="timeline wechat-lines muted">{draft.excerpt.map((m, n) => <li key={n}>
          <div>{m.from && <strong>{m.from}</strong>}<time>{when(m.time)}</time></div><p>{m.text}</p></li>)}</ol></details>}
    </div>}
  </li>;
}

/** 右侧抽屉：读微信的按钮和上次结果，下面一行一条草稿；再下面是模型没拟的话题。没有遮罩，背后的收件箱照常能用；Esc 和 ✕ 关。 */
export function NeedsDrawer({ report, close, changed }: { report: IssueDraftsReport | null; close: () => void; changed: () => void }) {
  const shut = useRef<HTMLButtonElement>(null);
  const closing = useRef(close);
  closing.current = close;
  useEffect(() => {
    shut.current?.focus();
    // 有确认框或详情弹窗开着时，Esc 归它们
    const key = (e: KeyboardEvent) => { if (e.key === "Escape" && !document.querySelector("dialog[open]")) closing.current(); };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, []);
  // 刚创建成的：那一行换成一句灰字，留两秒
  const [gone, setGone] = useState<Gone[]>([]);
  useEffect(() => {
    if (!gone.length) return;
    const id = setTimeout(() => setGone(g => g.filter(x => Date.now() - x.at < GONE_MS)), GONE_MS - (Date.now() - gone[0].at) + 20);
    return () => clearTimeout(id);
  }, [gone]);
  const drafts = (report?.drafts ?? []).filter(d => !gone.some(g => g.id === d.id));
  const skipped = report?.skipped ?? [];
  return <aside className="needs-drawer" id="needs-drawer" role="dialog" aria-label="新需求">
    <div className="needs-top">
      <h2>新需求{drafts.length > 0 && <small>{drafts.length} 条待评审</small>}</h2>
      <button ref={shut} className="needs-icon plain" onClick={close} aria-label="关闭" title="关闭（Esc）">✕</button>
    </div>
    <ScanHead scan={report?.scan ?? null} changed={changed} />
    <div className="needs-body">
      {gone.map(g => <p key={g.id} className="needs-gone" role="status">✓ {g.line}{g.url && <> · <a href={g.url} target="_blank" rel="noreferrer">{g.url.replace(/^https:\/\/github\.com\//, "")}</a></>}</p>)}
      {drafts.length > 0 && <ul className="needs-list">{drafts.map(d => <NeedRow key={d.id} draft={d} projects={report?.projects ?? []} owner={report?.owner || "你的账号"} people={report?.people ?? {}} changed={changed}
        created={(line, url) => { setGone(g => [...g, { id: d.id, at: Date.now(), line, url }]); changed(); }} />)}</ul>}
      {!drafts.length && !gone.length && <p className="needs-empty">{!report ? "正在读取…" : skipped.length ? "没有待评审的需求。下面是模型没拟的话题，看一眼要不要补。" : "没有待评审的需求。点上面的「读新需求」读一轮。"}</p>}
      {skipped.length > 0 && <details className="needs-skipped" open={!drafts.length || undefined}>
        <summary>没拟的话题 <small>{skipped.length} 个 · 模型觉得相关但没拟成草稿，点 ＋ 补成草稿</small></summary>
        <ul className="needs-list">{skipped.map(d => <SkippedRow key={d.id} draft={d} changed={changed} />)}</ul>
      </details>}
    </div>
    {(report?.mentions?.length ?? 0) > 0 && <details className="needs-waiting">
      <summary>做完后要 @ 的 <small>{report!.mentions!.length} 条，issue 关闭为完成后自动评论</small></summary>
      <ul>{report!.mentions!.map(m => <li key={m.url}><a href={m.url} target="_blank" rel="noreferrer">{m.title || m.url.replace(/^https:\/\/github\.com\//, "")}</a>
        <span className="meta">@{m.logins.join(" @")}</span>{m.error && <span className="meta bad" title={m.error}>上一轮没成：{m.error}</span>}</li>)}</ul>
    </details>}
    <div className="needs-foot">点标题展开可改 · 原话只在本机，不进 issue</div>
  </aside>;
}
