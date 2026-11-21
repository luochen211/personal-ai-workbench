import { useEffect, useState } from "react";
import Confirm from "./Confirm";
import { request } from "./api";
import type { ReplyDraft, SendResult, Switch } from "./api";
import { draftMeta, staleText, targetText } from "./DraftParts";

// 详情里的「回复草稿」：Agent 写好的每条回复（可以直接改），「确认发送」「重新生成」「丢弃」。
// 确认发送前后端重读一次讨论：对方在草稿生成后又回了，先问你仍发送还是重新生成。确认都用页面里的对话框。
// switchable：「帮我回复」那一轮说这条要改代码，多一个「改用处理并回复」（按处理并回复重新交给，交出去后这份草稿收起）。
const errorText = (e: unknown) => e instanceof Error ? e.message : "操作失败，请重试";
const fresh = (d: ReplyDraft) => Object.fromEntries(d.replies.map(r => [r.id, r.body]));

export function DraftPanel({ draft, changed, switchable = null }: { draft: ReplyDraft; changed: (message: string) => void; switchable?: Switch | null }) {
  const [bodies, setBodies] = useState<Record<string, string>>(() => fresh(draft));
  const version = `${draft.subject}|${draft.created_at}|${draft.replies.map(r => r.id).join(",")}`;
  useEffect(() => setBodies(fresh(draft)), [version]);  // 换了一份草稿（重新生成、发出一部分）才重置，刷新不冲掉正在改的字
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [stale, setStale] = useState<SendResult | null>(null);
  const [asking, setAsking] = useState<"discard" | "regenerate" | "switch" | null>(null);
  const edited = draft.replies.some(r => (bodies[r.id] ?? r.body) !== r.body);
  const empty = draft.replies.some(r => !(bodies[r.id] ?? "").trim());

  const act = async (name: string, run: () => Promise<void>) => {
    setBusy(name); setError("");
    try { await run(); } catch (e) { setError(errorText(e)); } finally { setBusy(""); }
  };
  const send = (force = false) => act("send", async () => {
    setStale(null);
    const result = await request<SendResult>("/api/replies/send", { subject: draft.subject, bodies, force });
    if (result.ok) { changed(`${draft.subject} 已发出 ${result.sent} 条回复`); return; }
    if (result.stale) { setStale(result); return; }
    setError(result.error || "发送失败，草稿还在");
    if (result.sent) changed("");  // 发出了一部分：重读草稿，只留没发出去的
  });
  const regenerate = () => act("regenerate", async () => {
    setAsking(null); setStale(null);
    const result = await request<{ error: string }>("/api/replies/regenerate", { subject: draft.subject });
    changed(result.error || `已请 Agent 重新写 ${draft.subject} 的回复，写好后回到「草稿待确认」；进度每 10 秒更新。`);
  });
  const switchMode = () => act("switch", async () => {
    setAsking(null); setStale(null);
    const result = await request<{ error: string }>("/api/replies/regenerate", { subject: draft.subject, mode: "handle" });
    changed(result.error || `已改用「处理并回复」把 ${draft.subject} 交给 Agent：先改代码，再写新的回复草稿；进度每 10 秒更新。`);
  });
  const discard = () => act("discard", async () => {
    setAsking(null);
    await request("/api/replies/discard", { subject: draft.subject });
    changed(`已丢弃 ${draft.subject} 的回复草稿，没有发出去。`);
  });

  return <section className="detail-section reply-draft"><h3>回复草稿 <small>{draftMeta(draft)}</small></h3>
    <p className="meta">可以直接改，点「确认发送」才用你的账号发到 GitHub。</p>
    {switchable && <p className="notice-line" role="status">这一轮是「帮我回复」（只回复），Agent 说这条要改代码：{switchable.line}</p>}
    {draft.work && <p className="draft-work"><b>Agent 已做：</b>{draft.work}</p>}
    {draft.based_error && <p className="notice-line" role="status">{draft.based_error}，发送前会再读一次。</p>}
    {draft.error && <p className="error" role="alert">上次发送失败：{draft.error}。草稿还在，可以再发。</p>}
    {draft.replies.map((r, n) => <label key={r.id} className="draft-item">
      <span className="draft-target">{draft.replies.length > 1 ? `第 ${n + 1} 条 · ` : ""}{targetText(r, draft.kind)}</span>
      <textarea value={bodies[r.id] ?? ""} rows={Math.min(14, Math.max(4, (bodies[r.id] ?? "").split("\n").length + 1))} disabled={!!busy}
        onChange={e => setBodies(b => ({ ...b, [r.id]: e.target.value }))} aria-label={`回复草稿第 ${n + 1} 条`} />
    </label>)}
    <div className="draft-actions">
      <button className="primary" disabled={!!busy || empty} onClick={() => void send()}>{busy === "send" ? "发送中…" : "确认发送"}</button>
      {switchable && <button disabled={!!busy} onClick={() => setAsking("switch")}>{busy === "switch" ? "提交中…" : "改用处理并回复"}</button>}
      <button disabled={!!busy} onClick={() => setAsking("regenerate")}>{busy === "regenerate" ? "提交中…" : "重新生成"}</button>
      <button className="ghost" disabled={!!busy} onClick={() => setAsking("discard")}>丢弃</button>
      {edited && <span className="meta">已改过，发送时用改过的字</span>}
      {empty && <span className="meta">有一条是空的，补上才能发</span>}
    </div>
    {error && <p className="error" role="alert">{error}</p>}
    {stale && <Confirm title="草稿可能过时" text={staleText(stale)} ok="仍发送" close={() => setStale(null)}
      confirm={() => void send(true)} extra={{ label: "重新生成", run: () => void regenerate() }} />}
    {asking === "regenerate" && <Confirm title="重新生成" text="丢掉这份草稿（包括你改过的字），Agent 重读讨论后写一份新的。Agent 已经做的代码改动和推送不受影响。"
      ok="重新生成" close={() => setAsking(null)} confirm={() => void regenerate()} />}
    {asking === "switch" && <Confirm title="改用处理并回复" text="按「处理并回复」重新交给 Agent：先按留言改代码、推送或开 PR，再写一份新的回复草稿。这份只回复的草稿收起（包括你改过的字）。"
      ok="改用处理并回复" close={() => setAsking(null)} confirm={() => void switchMode()} />}
    {asking === "discard" && <Confirm title="丢弃草稿" text="丢弃这份回复草稿，不发出去。Agent 已经做的代码改动和推送不受影响。"
      ok="丢弃" close={() => setAsking(null)} confirm={() => void discard()} />}
  </section>;
}
