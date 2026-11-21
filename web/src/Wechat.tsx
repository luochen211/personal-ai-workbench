import type { ReactNode } from "react";
import Modal from "./Modal";
import type { WechatRow } from "./api";

// 收件箱「微信」范围里的图标和详情（后端 personal/plugins/wechat/inbox.py）。微信只读：详情里只有「已处理」「暂时不处理」，没有交给 Agent、回复草稿和原文链接。
const GLYPHS: Record<string, string> = { at: "@", quote: "”", mention: "名", all: "全", money: "¥", packet: "红", call: "☎" };
const when = (value: string) => value ? new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "";

/** 行前的类型图标：私聊是气泡，其余是一个字的小方块（@、引用、名、全、¥、红、电话）。 */
export function WechatIcon({ row }: { row: Pick<WechatRow, "icon" | "label" | "bucket"> }) {
  const tone = row.bucket === "reply" ? "open" : "plain";
  return <svg className={`kind-icon wechat-icon ${tone}`} viewBox="0 0 16 16" width="16" height="16" role="img" aria-label={`微信 · ${row.label}`}>
    {row.icon === "chat" ? <><path d="M2.5 4.5a2 2 0 0 1 2-2h7a2 2 0 0 1 2 2v4.5a2 2 0 0 1-2 2H7l-3 2.5v-2.5h0a2 2 0 0 1-1.5-2Z" /></>
      : <><rect x="2" y="2" width="12" height="12" rx="3" /><text x="8" y="11.2" textAnchor="middle" className="glyph">{GLYPHS[row.icon] ?? "·"}</text></>}
  </svg>;
}

function Lines({ lines, muted = false }: { lines: { time: string; from: string; text: string }[]; muted?: boolean }) {
  if (!lines.length) return null;
  return <ol className={`timeline wechat-lines${muted ? " muted" : ""}`}>{lines.map((m, n) => <li key={n}>
    <div>{m.from && <strong>{m.from}</strong>}<time>{when(m.time)}</time></div><p>{m.text}</p></li>)}</ol>;
}

/** 点开一行：几条原话（群里的带上文）、信号和备注、「已处理」和暂时不处理。 */
export function WechatDetail({ row, close, done, busy, error, snooze }: {
  row: WechatRow; close: () => void; done: () => void; busy: boolean; error: string; snooze: ReactNode;
}) {
  return <Modal title="微信消息" close={close}>
    <div className="eyebrow">微信 · {row.label}{row.count > 1 && row.kind !== "private" ? ` · ${row.count} 条` : ""}</div>
    <h2>{row.chat}</h2>
    <div className="message-meta">{row.bucket === "reply" ? <span className="tag bad">待你回</span> : row.bucket === "look" ? <span className="tag">待你看</span> : <span className="tag">搁置</span>}
      {row.notes.map(n => <span key={n} className="tag">{n}</span>)}</div>
    {row.hits.map((hit, n) => <section key={n} className="detail-section">
      {hit.context.length > 0 && <><h3>上文</h3><Lines lines={hit.context} muted /></>}
      <Lines lines={hit.messages} />
      {hit.more && <p className="meta">{hit.more}</p>}
    </section>)}
    <section className="detail-section">
      <div className="inbox-actions"><button className="primary" disabled={busy || row.bucket === "hold"} onClick={done}>{busy ? "提交中…" : "已处理"}</button></div>
      <p className="meta">只记在本机：这个会话有更新的消息才再出现。微信里不会有任何变化，工作台不发消息。</p>
      {error && <p className="error" role="alert">{error}</p>}
    </section>
    {snooze}
  </Modal>;
}
