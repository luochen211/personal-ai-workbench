import { useCallback, useEffect, useRef, useState } from "react";
import { request } from "./api";
import { LiveView, MessageView } from "./ChatParts";
import type { ChatDetail, ChatDraft, ChatList } from "./ChatParts";
import { when } from "./RunParts";

// 对话（#/chat，用户 2026-11-21 要求）：和只读的助理聊天。后端每条消息起一次带沙箱的 Claude Code，只能读和查；
// 要改东西时回复里带 issue 草稿卡片，点「存成草稿」才进收件箱的「新需求」，在那里确认后才创建 issue。
// 回答中每半秒读一次进度，「已运行」每秒跳；刷新页面不丢。
const errorText = (e: unknown) => e instanceof Error ? e.message : "读取失败";

function DraftCard({ chat, draft }: { chat: string; draft: ChatDraft }) {
  const [state, setState] = useState<{ busy?: boolean; note?: string; bad?: boolean }>({});
  const save = async () => {
    setState({ busy: true });
    try {
      const result = await request<{ saved: boolean; why?: string }>("/api/chat/draft", { id: chat, draft });
      setState({ note: result.saved ? "已存进收件箱的「新需求」，在那里确认后才创建 issue。" : `没有再存：${result.why}` });
    } catch (e) { setState({ note: errorText(e), bad: true }); }
  };
  return <div className="chat-draft">
    <div className="eyebrow">issue 草稿 · {draft.repo}</div>
    <strong>{draft.title}</strong>
    <p>{draft.body}</p>
    {state.note ? <p className={state.bad ? "run-error" : "meta"}>{state.note} {!state.bad && <a href="#/inbox">去收件箱 →</a>}</p>
      : <button className="primary" disabled={state.busy} onClick={() => void save()}>{state.busy ? "存着…" : "存成草稿"}</button>}
  </div>;
}

export function ChatPanel() {
  const [list, setList] = useState<ChatList | null>(null);
  const [chat, setChat] = useState<ChatDetail | null>(null);
  const [picked, setPicked] = useState<string | null>(null);  // null = 新对话
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const bottom = useRef<HTMLDivElement>(null);
  const loadList = useCallback(() => request<ChatList>("/api/chats").then(result => {
    setList(result); setError("");
    return result;
  }).catch(e => { setError(errorText(e)); return null; }), []);
  const loadChat = useCallback((id: string) => request<ChatDetail>(`/api/chats/${id}`)
    .then(result => { setChat(result); return result; }).catch(e => { setError(errorText(e)); return null; }), []);
  useEffect(() => {
    void loadList().then(result => { if (result?.chats[0]) setPicked(p => p ?? result.chats[0].id); });
  }, [loadList]);
  useEffect(() => {
    setChat(null); setConfirming(false);
    if (picked) void loadChat(picked);
  }, [picked, loadChat]);
  const running = !!chat?.running;
  useEffect(() => {
    // 回答中每半秒读一次进度、每秒走一次时钟；答完再读一遍列表（标题、顺序）
    if (!running || !picked) return;
    const poll = setInterval(() => void loadChat(picked).then(r => { if (r && !r.running) void loadList(); }), 500);
    const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => { clearInterval(poll); clearInterval(clock); };
  }, [running, picked, loadChat, loadList]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: "end" }); }, [chat?.messages.length, chat?.live?.text.length]);

  const send = async () => {
    const message = text.trim();
    if (!message || sending || running) return;
    setSending(true); setError("");
    try {
      const { id } = await request<{ id: string }>("/api/chat/send", { text: message, ...(picked ? { id: picked } : {}) });
      setText("");
      if (id !== picked) setPicked(id); else void loadChat(id);
      void loadList();
    } catch (e) { setError(errorText(e)); }
    finally { setSending(false); }
  };
  const stop = async () => {
    if (!picked) return;
    try { await request("/api/chat/stop", { id: picked }); } catch (e) { setError(errorText(e)); }
  };
  const remove = async () => {
    if (!picked) return;
    try {
      await request("/api/chat/delete", { id: picked });
      setPicked(null);
      const result = await loadList();
      if (result?.chats[0]) setPicked(result.chats[0].id);
    } catch (e) { setError(errorText(e)); }
  };

  return <div className="chat-layout">
    <aside className="chat-list">
      <button className={picked === null ? "primary" : ""} onClick={() => setPicked(null)}>＋ 新对话</button>
      {list?.chats.map(c => <button key={c.id} className={`chat-row${c.id === picked ? " on" : ""}`} onClick={() => setPicked(c.id)}>
        <strong>{c.title}</strong><small>{c.running ? "回答中…" : when(c.updated_at)}</small>
      </button>)}
    </aside>
    <section className="chat-main">
      {error && <div className="error" role="alert">{error}</div>}
      {list && !list.ready && <div className="error" role="alert">找不到 {list.program}：对话要用中台的沙箱启动 Claude，没有它不启动。</div>}
      <div className="chat-log">
        {!picked && <div className="chat-intro">
          <h2>问助理</h2>
          <p className="meta">助理只读：能读助理仓库和项目代码、查工作台、GitHub 和中台，不会改任何东西。要改代码或文档，它会拟一张 issue 草稿，你点「存成草稿」后去收件箱的「新需求」确认创建，再照常交给 Agent。</p>
          <p className="meta">模型 {list?.model ?? "…"} · 每条消息要先启动一次 Claude，开头会等几秒。</p>
        </div>}
        {chat?.messages.map((m, i) => <div key={i}>
          <MessageView message={m} />
          {m.drafts?.map((d, j) => <DraftCard key={j} chat={chat.id} draft={d} />)}
        </div>)}
        {chat?.live && <LiveView live={chat.live} now={now} />}
        <div ref={bottom} />
      </div>
      <div className="chat-input">
        <textarea value={text} rows={3} placeholder="问点什么，比如：某个项目现在卡在哪？（Enter 发送，Shift+Enter 换行）" aria-label="消息"
          onChange={e => setText(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }} />
        <div className="chat-actions">
          {running ? <button onClick={() => void stop()}>停止</button>
            : <button className="primary" disabled={!text.trim() || sending || (list ? !list.ready : true)} onClick={() => void send()}>{sending ? "发送中…" : "发送"}</button>}
          {picked && !running && (confirming
            ? <><button className="text-button" onClick={() => void remove()}>确认删除这个对话</button><button className="text-button" onClick={() => setConfirming(false)}>算了</button></>
            : <button className="text-button" onClick={() => setConfirming(true)}>删除对话</button>)}
        </div>
      </div>
    </section>
  </div>;
}
