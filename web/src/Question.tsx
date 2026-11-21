import { useState } from "react";
import { request } from "./api";
import type { Question as Item } from "./api";
import Markdown from "./markdown";

// Agent 对一条 issue 的提问或处理结果：全文、选项和回答。回答经工作台调用 agent-autoloop answer。
// path：回答送到哪；缺省是 issue 的提问，PR 反馈的提问走 /api/prs/questions/<编号>/answer
export default function Question({ item, done, path }: { item: Item; done: (message: string) => void; path?: string }) {
  const [words, setWords] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const send = async (body: { choice?: number; text?: string }) => {
    setBusy(true); setError("");
    try { done((await request<{ message: string }>(path ?? `/api/questions/${item.id}/answer`, body)).message || "已回答"); }
    catch (e) { setError(e instanceof Error ? e.message : "回答没送出"); }
    finally { setBusy(false); }
  };
  const ask = item.kind === "ask";
  return <div className="waiting">
    <strong>{ask ? "Agent 的提问" : "Agent 的处理结果"}：{item.title}</strong>
    {item.text ? <Markdown text={item.text} /> : <p className="meta">（没有正文）</p>}
    <div className="answer-options">{(item.options.length ? item.options : ["看过了"]).map((option, i) =>
      <button key={option} className={item.recommend === i ? "primary" : ""} disabled={busy} onClick={() => void send({ choice: i + 1 })}>
        {option}{item.recommend === i ? "（推荐）" : ""}
      </button>)}</div>
    {ask && <div className="inline-form"><input aria-label="别的想法" placeholder="别的想法…" value={words} onChange={e => setWords(e.target.value)} />
      <button disabled={busy || !words.trim()} onClick={() => void send({ text: words })}>{busy ? "送出中…" : "回答"}</button></div>}
    {error && <div className="error" role="alert">{error}</div>}
  </div>;
}
