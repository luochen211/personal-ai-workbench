import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "./markdown";
import { request } from "./api";
import { NOVEL_LABEL, briefBody, dayLabel, failureNote, gapNote, handoffNote, learningBrief, learningUrl, nextLabel, nextNovelStatus, novelUrl, readmeRest, runLabel, writing } from "./assistant";
import type { BoardReport, BriefReport, BriefRun, LearningHanded, LearningReport, LearningSaved, LearningTopic, NovelBook, NovelReport, NovelStatus } from "./api";

// 今日简报、事项两页：只读个人助理目录（默认 /Volumes/Luochen/LCS/assistant）。页面不显示文件名和路径。
// 学习：只读学习课题目录（默认 /Volumes/Luochen/学习资料/学习课题），例外是保存反馈和「写下一篇」交给 Agent。
const errorText = (e: unknown) => e instanceof Error ? e.message : "读取失败";
const when = (value: string) => new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });

export function BriefPanel() {
  const [data, setData] = useState<BriefReport | null>(null);
  const [picked, setPicked] = useState("");  // 空 = 默认那天（今天，或不晚于今天的最新一份）
  const [pending, setPending] = useState<string | null>(null);  // 正在切换到的日期
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [running, setRunning] = useState(false);
  const ticket = useRef(0);  // 只认最后一次请求的结果：先发后到的旧响应直接丢掉
  const load = useCallback(async (target: string, showing = target) => {
    const id = ++ticket.current;
    setPending(showing || null);
    try {
      const result = await request<BriefReport>(target ? `/api/brief?date=${encodeURIComponent(target)}` : "/api/brief");
      if (id !== ticket.current) return;
      setData(result); setError(""); setPicked(target && target !== result.default ? target : "");
    } catch (e) {
      if (id === ticket.current) setError(errorText(e));  // 读失败时下拉框回到正在显示的那天
    } finally {
      if (id === ticket.current) setPending(null);
    }
  }, []);
  useEffect(() => { void load(""); }, [load]);
  // 简报由助理仓库的脚本生成，这里只在顶部加一条只读的学习提示（INDEX.md 的下一步和还没填反馈的篇）
  const [study, setStudy] = useState<ReturnType<typeof learningBrief>>([]);
  useEffect(() => { request<LearningReport>("/api/learning").then(r => setStudy(learningBrief(r.topics))).catch(() => setStudy([])); }, []);
  const generate = async () => {
    const id = ++ticket.current;
    setPending(null); setRunning(true); setError(""); setNotice("");
    try {
      const result = await request<BriefRun>("/api/brief/run", {});
      if (id !== ticket.current) return;
      setData(result); setPicked("");
      setNotice(`已生成 ${dayLabel(result.today, result.today)}的简报。${result.previous ? "原来那份已另存。" : ""}`);
    } catch (e) { if (id === ticket.current) setError(errorText(e)); } finally { setRunning(false); }
  };
  const brief = data?.brief;
  const runButton = <button className="primary" disabled={running} onClick={() => void generate()}>{runLabel(data, running)}</button>;
  const failed = brief ? failureNote(brief.failures) : "";
  // 日期只在下拉框里出现一次：正文的「每日简报 日期」标题和「生成时间」行去掉，生成时刻放在下拉框旁边
  const body = brief ? briefBody(brief.markdown, brief.date) : null;
  return <>
    {error && <div className="error" role="alert">{error}</div>}
    {data?.error && <div className="error" role="alert">{data.error}</div>}
    {notice && <div className="success-note" role="status">{notice}</div>}
    {study.length > 0 && <div className="learning-brief">{study.map(s => <p key={s.name}><a href="#/learning">{s.name}</a>{s.todo && `：${s.todo}`}</p>)}</div>}
    {running && <div className="notice-line" role="status">正在读取飞书日程、飞书任务和 GitHub 未读通知，通常要几十秒到两三分钟。</div>}
    <div className="toolbar assistant-bar">
      {data && data.dates.length > 0 && <select aria-label="选择日期" value={pending ?? brief?.date ?? ""} onChange={e => { const value = e.target.value; void load(value === data.default ? "" : value, value); }}>
        {data.dates.map(d => <option key={d} value={d}>{dayLabel(d, data.today)}</option>)}
      </select>}
      {brief && body && <span className="meta brief-meta">{body.generated || when(brief.modified)} 生成</span>}
      <button className="bar-end" onClick={() => void load(picked)} disabled={running}>↻ 刷新</button>
      {brief && !data.error && runButton}
    </div>
    {!data ? !error && <div className="notice">正在读取简报…</div> : brief ? <>
      {!data.dates.includes(data.today) && <div className="notice-line" role="status">今天的简报还没有生成{brief.date === data.default ? `，下面是 ${dayLabel(brief.date)} 的` : ""}。{data.schedule}，也可以点「现在生成」。</div>}
      {failed && <div className="gap-note" role="alert">{failed}{brief.date === data.today ? "重新生成会先备份这一份再覆盖。" : ""}</div>}
      <article className="doc-panel"><Markdown text={body?.text ?? brief.markdown} /></article>
    </> : !data.error && <div className="empty-state">
      <strong>还没有简报</strong>
      <p>{data.notice}</p>
      <p className="meta">简报{data.schedule}；内容是今明两天飞书日程、未完成飞书任务和 GitHub 未读通知。</p>
      {runButton}
    </div>}
  </>;
}

export function BoardsPanel() {
  const [data, setData] = useState<BoardReport | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string[]>([]);
  const load = useCallback(() => request<BoardReport>("/api/boards")
    .then(result => { setData(result); setError(""); }).catch(e => setError(errorText(e))), []);
  useEffect(() => { void load(); }, [load]);
  const toggle = (name: string) => setOpen(o => o.includes(name) ? o.filter(n => n !== name) : [...o, name]);
  return <>
    {error && <div className="error" role="alert">{error}</div>}
    {data?.error && <div className="error" role="alert">{data.error}</div>}
    <div className="toolbar assistant-bar">
      <button className="bar-end" onClick={() => void load()}>↻ 刷新</button>
    </div>
    {!data ? !error && <div className="notice">正在读取事项…</div> : <div className="boards">{data.boards.map(b => {
      const expanded = open.includes(b.name);
      const note = gapNote(b);
      const rest = readmeRest(b.readme);  // 板块名、状态、下一步卡片上已有，展开只给其余各段
      return <section key={b.name} className={`board${note ? " needs-fill" : ""}`}>
        <button className="board-head" aria-expanded={rest.text ? expanded : undefined} disabled={!rest.text} onClick={() => toggle(b.name)}>
          <strong>{b.name}</strong>
          <span className="board-summary">{b.summary || "事项总表里没有这一行的一句话"}</span>
          {note && <span className="tag warn">下一步需补</span>}
          {rest.text && <span className="board-toggle" title={rest.titles.join("、") || undefined}>{expanded ? "收起 ▴" : rest.titles.length ? `展开其余 ${rest.titles.length} 段 ▾` : "展开其余 ▾"}</span>}
        </button>
        <div className="board-cols">
          <div><h3>状态</h3>{b.status ? <Markdown text={b.status} /> : <p className="meta">README 里没有「状态」段，各段在「展开其余」里。</p>}</div>
          <div><h3>下一步</h3>
            {note && <div className="gap-note" role="note">{note}</div>}
            {b.next && <Markdown text={b.next} />}
          </div>
        </div>
        {b.news.length > 0 && <div className="board-news"><h3>最近动态</h3>
          <ul>{b.news.map((n, i) => <li key={i}><span className="news-date">{n.date}</span>{n.text}</li>)}</ul>
        </div>}
        {expanded && rest.text && <div className="board-readme"><Markdown text={rest.text} /></div>}
        <p className="meta board-path">更新于 {when(b.modified)}</p>
      </section>;
    })}{!data.boards.length && !data.error && <div className="notice">事项目录下还没有板块。</div>}</div>}
  </>;
}

// 小说导读：导读根目录下每本书一行，左书名，右阅读状态（点一下换）和「打开导读」；末尾「人物选择对照」。
function NovelsSection({ report, expanded, onToggle }: { report: NovelReport; expanded: boolean; onToggle: () => void }) {
  const [books, setBooks] = useState(report.books);
  const [failed, setFailed] = useState<Record<string, string>>({});
  useEffect(() => setBooks(report.books), [report]);
  const toggle = async (book: NovelBook) => {
    const status = nextNovelStatus(book.status);
    setBooks(list => list.map(b => b.name === book.name ? { ...b, status } : b));  // 先换，失败再换回
    setFailed(f => ({ ...f, [book.name]: "" }));
    try { await request<{ book: string; status: NovelStatus }>("/api/learning/novel", { book: book.name, status }); } catch (e) {
      setBooks(list => list.map(b => b.name === book.name ? { ...b, status: book.status } : b));
      setFailed(f => ({ ...f, [book.name]: errorText(e) }));
    }
  };
  return <section className="board learning-topic learning-novels">
    <button className="learning-head" aria-expanded={expanded} onClick={onToggle}>
      <strong>小说导读</strong>
      {!expanded && <span className="learning-next">{(() => { const reading = books.filter(b => b.status === "reading").map(b => b.name);
        return reading.length ? `在读：${reading.join("、")}` : `${books.length} 本`; })()}</span>}
      <svg className={`learning-chevron${expanded ? " open" : ""}`} viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4" /></svg>
    </button>
    {expanded && <>
    {report.error && <p className="meta bad learning-status">{report.error}</p>}
    <ul className="learning-list">{books.map(b => <li key={b.name}>
      <div className="learning-row">
        <span className="learning-title">{b.name}</span>
        <span className="learning-links">
          <button className={`text-button novel-status ${b.status}`} title="点一下切换：未开始 → 在读 → 读完"
            aria-label={`《${b.name}》${NOVEL_LABEL[b.status]}，点一下改成${NOVEL_LABEL[nextNovelStatus(b.status)]}`} onClick={() => void toggle(b)}>{NOVEL_LABEL[b.status]}</button>
          <a href={novelUrl(`${b.name}/index.html`)} target="_blank" rel="noreferrer">打开 ↗</a>
        </span>
      </div>
      {failed[b.name] && <p className="meta bad" role="alert">{failed[b.name]}</p>}
    </li>)}
      {report.compare && <li><div className="learning-row"><span className="learning-title">人物选择对照</span>
        <span className="learning-links"><a href={novelUrl(report.compare)} target="_blank" rel="noreferrer">打开 ↗</a></span></div></li>}
    </ul>
    {!books.length && !report.error && <p className="meta learning-status">导读目录里还没有书。</p>}
    </>}
  </section>;
}

const articleKey = (topic: string, no: string) => `${topic}/${no}`;
// 小说导读和课题共用一个展开位：同一时间只展开一个
const NOVELS_KEY = "\u0000novels";

export function LearningPanel() {
  const [data, setData] = useState<LearningReport | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);  // 展开的课题；null = 默认（最近更新的那个），"" = 都收起
  const [editing, setEditing] = useState("");  // 正在填反馈的那篇：课题/篇号
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<Record<string, { text: string; bad?: boolean }>>({});
  const [saving, setSaving] = useState("");
  const [handing, setHanding] = useState("");
  const load = useCallback(() => request<LearningReport>("/api/learning")
    .then(result => { setData(result); setError(""); }).catch(e => setError(errorText(e))), []);
  useEffect(() => { void load(); }, [load]);
  const following = (data?.topics ?? []).some(t => writing(t.handoff));
  useEffect(() => {
    // 交给后每 10 秒看一次：写完了列表跟着刷新
    if (!following) return;
    const id = setInterval(() => void load(), 10000);
    return () => clearInterval(id);
  }, [following, load]);
  const shown = open ?? data?.default ?? "";
  const patch = (name: string, change: (t: LearningTopic) => LearningTopic) =>
    setData(d => d && { ...d, topics: d.topics.map(t => t.name === name ? change(t) : t) });
  const save = async (topic: LearningTopic, no: string, version: string) => {
    const key = articleKey(topic.name, no);
    setSaving(key);
    try {
      const result = await request<LearningSaved>("/api/learning/feedback", { topic: topic.name, no, text: drafts[key] ?? "", version });
      patch(topic.name, t => {
        const articles = t.articles.map(a => a.no === no ? { ...a, feedback: result.feedback, filled: result.filled, version: result.version } : a);
        return { ...t, articles, unfilled: articles.filter(a => !a.filled).map(a => a.no) };
      });
      setDrafts(d => ({ ...d, [key]: result.feedback }));
      setNotes(n => ({ ...n, [key]: { text: result.filled ? "已保存" : "已清空" } }));
    } catch (e) { setNotes(n => ({ ...n, [key]: { text: errorText(e), bad: true } })); } finally { setSaving(""); }
  };
  const handoff = async (topic: LearningTopic) => {
    setHanding(topic.name);
    setNotes(n => ({ ...n, [topic.name]: { text: "" } }));
    try {
      const result = await request<LearningHanded>("/api/learning/next", { topic: topic.name });
      patch(topic.name, t => ({ ...t, handoff: result.handoff }));
      if (result.error) setNotes(n => ({ ...n, [topic.name]: { text: result.error, bad: true } }));
    } catch (e) { setNotes(n => ({ ...n, [topic.name]: { text: errorText(e), bad: true } })); } finally { setHanding(""); }
  };
  return <>
    {error && <div className="error" role="alert">{error}</div>}
    {data?.error && <div className="error" role="alert">{data.error}</div>}
    {data?.hub_error && <div className="notice-line" role="status">没读到 Agent 的进度：{data.hub_error}</div>}
    <div className="toolbar assistant-bar">
      <button className="bar-end" onClick={() => void load()}>↻ 刷新</button>
    </div>
    {!data ? !error && <div className="notice">正在读取学习课题…</div> : <div className="boards">{data.topics.map(t => {
      const expanded = shown === t.name;
      const busy = handing === t.name || writing(t.handoff);
      const said = notes[t.name]?.text || handoffNote(t.handoff);
      return <section key={t.name} className="board learning-topic">
        <button className="learning-head" aria-expanded={expanded} onClick={() => setOpen(expanded ? "" : t.name)}>
          <strong>{t.name}{!t.exists && <span className="tag bad">目录不在</span>}</strong>
          <span className="learning-next" title={t.next || undefined}>{t.next || "下一步没写"}</span>
          <svg className={`learning-chevron${expanded ? " open" : ""}`} viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4" /></svg>
        </button>
        {expanded && t.exists && <div className="learning-body">
          <div className="learning-actions">
            {t.home && <a href={learningUrl(t.name, "index.html", t.path)} target="_blank" rel="noreferrer">课题首页 ↗</a>}
            <button className="primary" disabled={busy || !t.next_no} onClick={() => void handoff(t)}>{nextLabel(t, handing === t.name)}</button>
          </div>
          {said && <p className={`meta learning-status${notes[t.name]?.bad ? " bad" : ""}`} role="status">{said}</p>}
          <ol className="learning-list">{t.articles.map(a => {
            const key = articleKey(t.name, a.no);
            const editingThis = editing === key;
            const note = notes[key];
            return <li key={a.no}>
              <div className="learning-row">
                <span className="learning-no">{a.no}</span>
                <span className="learning-title">{a.title || `第 ${a.no} 篇`}</span>
                <span className="learning-links">
                  {a.html ? <a href={learningUrl(t.name, `${a.no}.html`, t.path)} target="_blank" rel="noreferrer">打开 ↗</a> : <span className="meta">还没生成网页</span>}
                  <button className={`text-button feedback-toggle${a.filled ? " filled" : ""}${editingThis ? " on" : ""}`} aria-expanded={editingThis}
                    onClick={() => { setEditing(editingThis ? "" : key); setDrafts(d => key in d ? d : { ...d, [key]: a.feedback }); }}>{a.filled ? "已填反馈" : "填反馈"} {editingThis ? "▴" : "▾"}</button>
                </span>
              </div>
              {editingThis && <div className="learning-feedback">
                <textarea aria-label={`第 ${a.no} 篇的学习反馈`} rows={6} value={drafts[key] ?? a.feedback} placeholder="哪里看懂了、哪里没看懂、想展开什么，最好再写一件最近遇到的真事。"
                  onChange={e => { const value = e.target.value; setDrafts(d => ({ ...d, [key]: value })); setNotes(n => ({ ...n, [key]: { text: "" } })); }} />
                <div className="learning-save">
                  <button className="primary" disabled={saving === key || (drafts[key] ?? a.feedback) === a.feedback} onClick={() => void save(t, a.no, a.version)}>{saving === key ? "保存中…" : "保存"}</button>
                  {note?.text && <span className={`meta${note.bad ? " bad" : ""}`} role={note.bad ? "alert" : "status"}>{note.text}</span>}
                </div>
              </div>}
            </li>;
          })}</ol>
          {!t.articles.length && <p className="meta">还没有文章。</p>}
        </div>}
      </section>;
    })}{!data.topics.length && !data.error && <div className="notice">INDEX.md 里还没有课题。</div>}
      {data.novels && <NovelsSection report={data.novels} expanded={shown === NOVELS_KEY} onToggle={() => setOpen(shown === NOVELS_KEY ? "" : NOVELS_KEY)} />}</div>}
  </>;
}
