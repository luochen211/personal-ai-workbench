import { useState } from "react";
import { request } from "./api";
import type { Snooze, SnoozeWait } from "./api";
import { SNOOZE_WAITS } from "./InboxParts";

// 暂时不处理（personal/snooze.py）：只记本机 .personal/snoozed.json，不写 GitHub。列表工具条、收件箱详情和 issue 详情共用
export interface SnoozeItem { subject: string; their_at: string }
const errorText = (e: unknown) => e instanceof Error ? e.message : "操作失败，请重试";
// 微信的键（wechat:<类>:<会话>）不给人看，写「这条微信」
const named = (subject: string) => subject.startsWith("wechat:") ? "这条微信" : subject;
const back = (wait: SnoozeWait) => wait === "news" ? "对方有新动静就回来" : `${SNOOZE_WAITS.find(w => w.key === wait)!.label}回来`;

export async function snooze(items: SnoozeItem[], wait: SnoozeWait) {
  const result = await request<{ snoozed: string[]; until: string }>("/api/snooze", { items, wait });
  return `${result.snoozed.length === 1 ? `${named(result.snoozed[0])} ` : `${result.snoozed.length} 条`}已放进「搁置」，${back(wait)}。`;
}

export async function restore(subjects: string[]) {
  const result = await request<{ restored: string[] }>("/api/snooze/restore", { items: subjects });
  return result.restored.length ? `${result.restored.map(named).join("、")} 已恢复。` : "这条已经不在暂时不处理里。";
}

/** 「暂时不处理」按钮，旁边一个小下拉选何时回来（默认有新动静再说）。 */
export function SnoozeButton({ label = "暂时不处理", disabled, busy, run }: {
  label?: string; disabled: boolean; busy?: boolean; run: (wait: SnoozeWait) => void;
}) {
  const [wait, setWait] = useState<SnoozeWait>("news");
  return <span className="snooze-control">
    <button disabled={disabled} onClick={() => run(wait)}>{busy ? "提交中…" : label}</button>
    <select aria-label="何时回来" value={wait} disabled={disabled} onChange={e => setWait(e.target.value as SnoozeWait)}>
      {SNOOZE_WAITS.map(w => <option key={w.key} value={w.key}>{w.label}</option>)}
    </select>
  </span>;
}

/** 详情里的一段：没设时是「暂时不处理」；设了写那一句和「恢复」。changed 收到提示后由调用方刷新列表。 */
export function SnoozeSection({ item, held, can, changed }: {
  item: SnoozeItem; held: Snooze | null | undefined; can: boolean; changed: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!held && !can) return null;
  const act = async (run: () => Promise<string>) => {
    setBusy(true); setError("");
    try { changed(await run()); } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  };
  return <section className="detail-section snooze-section">
    {held ? <div className="inbox-actions"><span className="meta">{held.line}</span>
      <button className="primary" disabled={busy} onClick={() => void act(() => restore([item.subject]))}>{busy ? "恢复中…" : "恢复"}</button></div>
      : <div className="inbox-actions"><SnoozeButton disabled={busy} busy={busy} run={wait => void act(() => snooze([item], wait))} /></div>}
    {error && <p className="error" role="alert">{error}</p>}
  </section>;
}
