import { useCallback, useEffect, useState } from "react";
import { request } from "./api";
import { REVIEW_NAME, reviewBars, reviewHandoffNote, reviewHead, reviewMissing, reviewOutput, reviewRange, reviewRunLabel, reviewUrl, runTime, writing } from "./assistant";
import type { ReviewHanded, ReviewKind, ReviewPeriod, ReviewReport, ReviewRun, ReviewSaved } from "./api";

// 回顾：只读助理目录的 回顾/周、回顾/月（助理仓库 scripts/review.py 写的 md 和离线 HTML）。例外三处写：保存三问的回答、
// 运行回顾脚本收集事实（现在生成 / 更新数据）、把「写总结和三问」交给 Agent。样式沿用学习标签的行和展开（.learning-*）。
const errorText = (e: unknown) => e instanceof Error ? e.message : "读取失败";
const NOT_WRITTEN = <p className="meta">还没写</p>;
const idOf = (p: Pick<ReviewPeriod, "kind" | "label">) => `${p.kind}/${p.label}`;
type Note = { text: string; bad?: boolean };

export function ReviewPanel() {
  const [data, setData] = useState<ReviewReport | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);  // 展开的那一期；null = 默认（最新一期周回顾），"" = 都收起
  const [editing, setEditing] = useState("");  // 正在写回答的那一题：期/题号
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<Record<string, Note>>({});
  const [saving, setSaving] = useState("");
  const [running, setRunning] = useState("");  // 正在跑脚本的那一期（或分区）
  const [handing, setHanding] = useState("");
  const load = useCallback(() => request<ReviewReport>("/api/review")
    .then(result => { setData(result); setError(""); }).catch(e => setError(errorText(e))), []);
  useEffect(() => { void load(); }, [load]);
  const all = [...data?.weeks ?? [], ...data?.months ?? []];
  const following = all.some(p => p.handoff && writing(p.handoff));
  useEffect(() => {
    // 交给 Agent 后每 10 秒看一次：写完了内容跟着刷新
    if (!following) return;
    const id = setInterval(() => void load(), 10000);
    return () => clearInterval(id);
  }, [following, load]);
  const shown = open ?? (data?.weeks[0] ? idOf(data.weeks[0]) : data?.months[0] ? idOf(data.months[0]) : "");
  const say = (key: string, note: Note) => setNotes(n => ({ ...n, [key]: note }));
  const patch = (next: ReviewPeriod) => setData(d => d && {
    ...d, weeks: d.weeks.map(p => idOf(p) === idOf(next) ? { ...next, handoff: p.handoff } : p),
    months: d.months.map(p => idOf(p) === idOf(next) ? { ...next, handoff: p.handoff } : p),
  });
  const generate = async (kind: ReviewKind, label: string) => {
    const key = `${kind}/${label}`;
    setRunning(key); say(kind, { text: "" }); say(key, { text: "" });
    try {
      const result = await request<ReviewRun>("/api/review/run", { kind, label });
      setData(result); setOpen(key);
      say(kind, { text: result.existed ? `${result.label} 的数据已更新，写好的总结和回答没动。` : `已生成 ${result.label} 的事实部分，总结和三问等回顾 Agent 写。` });
    } catch (e) { say(kind, { text: errorText(e), bad: true }); } finally { setRunning(""); }
  };
  const handoff = async (p: ReviewPeriod) => {
    const key = idOf(p);
    setHanding(key); say(key, { text: "" });
    try {
      const result = await request<ReviewHanded>("/api/review/next", { kind: p.kind, label: p.label });
      setData(d => d && { ...d, weeks: d.weeks.map(x => idOf(x) === key ? { ...x, handoff: result.handoff } : x),
        months: d.months.map(x => idOf(x) === key ? { ...x, handoff: result.handoff } : x) });
      if (result.error) say(key, { text: result.error, bad: true });
    } catch (e) { say(key, { text: errorText(e), bad: true }); } finally { setHanding(""); }
  };
  const save = async (p: ReviewPeriod, no: number) => {
    const key = `${idOf(p)}/${no}`;
    setSaving(key);
    try {
      const result = await request<ReviewSaved>("/api/review/answer", { kind: p.kind, label: p.label, no, text: drafts[key] ?? "", version: p.version });
      patch(result.period);
      const answer = result.period.questions.find(q => q.no === no)?.answer ?? "";
      setDrafts(d => ({ ...d, [key]: answer }));
      say(key, result.note ? { text: result.note, bad: true } : { text: answer ? "已保存" : "已清空" });
    } catch (e) { say(key, { text: errorText(e), bad: true }); } finally { setSaving(""); }
  };

  const row = (p: ReviewPeriod) => {
    const id = idOf(p);
    const expanded = shown === id;
    const missing = reviewMissing(p);
    const busy = handing === id || (!!p.handoff && writing(p.handoff));
    const said = notes[id]?.text || reviewHandoffNote(p.handoff);
    const bars = reviewBars(p.numbers);
    const output = reviewOutput(p.numbers);
    return <section key={id} className="board learning-topic">
      <button className="learning-head" aria-expanded={expanded} onClick={() => setOpen(expanded ? "" : id)}>
        <strong>{p.label}{p.partial && <span className="tag warn">还没过完</span>}</strong>
        <span className="learning-next" title={p.summary || undefined}>{reviewHead(p)}</span>
        <svg className={`learning-chevron${expanded ? " open" : ""}`} viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4" /></svg>
      </button>
      {expanded && <div className="learning-body">
        <div className="learning-actions">
          <span className="meta">{reviewRange(p)}{p.generated && ` · ${p.generated} 生成`}</span>
          {p.html && <a href={reviewUrl(p.kind, p.label)} target="_blank" rel="noreferrer">打开完整回顾 ↗</a>}
          {missing.length > 0 && <button disabled={busy} onClick={() => void handoff(p)}>{handing === id ? "交给中…" : busy ? "Agent 正在写…" : "让 Agent 写总结和三问"}</button>}
        </div>
        {said && <p className={`meta learning-status${notes[id]?.bad ? " bad" : ""}`} role="status">{said}</p>}
        <div className="review-part"><h3>总结</h3>{p.summary ? <p>{p.summary}</p> : NOT_WRITTEN}</div>
        <div className="review-part"><h3>时间去向（估算）</h3>
          {p.numbers.hours === undefined && !bars.length ? <p className="meta">这一期没有读到时间数据。</p> : <>
            {p.numbers.hours !== undefined && <p>本人合计约 {p.numbers.hours} 小时{p.numbers.days !== undefined && `，${p.numbers.days} 天有记录`}</p>}
            {bars.length > 0 && <ul className="review-bars">{bars.map(b => <li key={b.name}>
              <span>{b.name}</span>
              <span className="review-track" aria-hidden="true"><i style={{ width: `${b.width}%` }} /></span>
              <span className="review-hours">{b.hours} 小时{b.share !== null && ` · ${b.share}%`}</span>
            </li>)}</ul>}
          </>}
        </div>
        {output && <div className="review-part"><h3>产出</h3><p>{output}</p></div>}
        {p.has.top3 && <div className="review-part"><h3>下周最重要的 3 件（建议）</h3>
          {p.top3.length ? <ol className="review-items">{p.top3.map((t, n) => <li key={n}>{t}</li>)}</ol> : NOT_WRITTEN}</div>}
        {p.has.projects && <div className="review-part"><h3>各项目过三问</h3>
          {p.projects.length ? <ul className="review-items">{p.projects.map((t, n) => <li key={n}>{t}</li>)}</ul> : NOT_WRITTEN}</div>}
        <div className="review-part"><h3>三问</h3>
          {!p.written.questions ? NOT_WRITTEN : <ol className="learning-list review-questions">{p.questions.map(q => {
            const key = `${id}/${q.no}`;
            const editingThis = editing === key;
            const note = notes[key];
            return <li key={q.no}>
              <div className="learning-row">
                <span className="learning-no">{q.no}</span>
                <span className="learning-title">{q.text || <span className="meta">还没写</span>}</span>
                {q.text && <span className="learning-links">
                  <button className={`text-button feedback-toggle${q.answer ? " filled" : ""}${editingThis ? " on" : ""}`} aria-expanded={editingThis}
                    onClick={() => { setEditing(editingThis ? "" : key); setDrafts(d => key in d ? d : { ...d, [key]: q.answer }); }}>{q.answer ? "已回答" : "写回答"} {editingThis ? "▴" : "▾"}</button>
                </span>}
              </div>
              {q.answer && !editingThis && <p className="review-answer">{q.answer}</p>}
              {editingThis && <div className="learning-feedback">
                <textarea aria-label={`${p.label} 第 ${q.no} 题的回答`} rows={4} value={drafts[key] ?? q.answer} placeholder="直接写想法；下一期回顾会读这里的回答。"
                  onChange={e => { const value = e.target.value; setDrafts(d => ({ ...d, [key]: value })); say(key, { text: "" }); }} />
                <div className="learning-save">
                  <button className="primary" disabled={saving === key || (drafts[key] ?? q.answer) === q.answer} onClick={() => void save(p, q.no)}>{saving === key ? "保存中…" : "保存"}</button>
                  {note?.text && <span className={`meta${note.bad ? " bad" : ""}`} role={note.bad ? "alert" : "status"}>{note.text}</span>}
                </div>
              </div>}
            </li>;
          })}</ol>}
        </div>
      </div>}
    </section>;
  };

  const section = (kind: ReviewKind, rows: ReviewPeriod[]) => {
    if (!data) return null;
    const label = data.current[kind];
    const exists = rows.some(p => p.label === label);
    const run = running === `${kind}/${label}`;
    const note = notes[kind];
    const button = <button className="primary" disabled={!!running} onClick={() => void generate(kind, label)}>{reviewRunLabel(label, exists, run)}</button>;
    return <section className="review-section" aria-label={REVIEW_NAME[kind]}>
      <div className="review-bar">
        <h2>{REVIEW_NAME[kind]}</h2>
        <span className="meta">{data.schedule[kind]} 自动生成，下一次 {runTime(data.next_run[kind])}</span>
        {rows.length > 0 && button}
      </div>
      {run && <div className="notice-line" role="status">正在读提交记录、会话时间戳、中台运行记录和 GitHub，通常要十几秒到一两分钟。</div>}
      {note?.text && <div className={note.bad ? "error" : "success-note"} role={note.bad ? "alert" : "status"}>{note.text}</div>}
      {rows.length ? <div className="boards">{rows.map(row)}</div> : <div className="empty-state">
        <strong>还没有{REVIEW_NAME[kind]}</strong>
        <p>下一次自动生成：{runTime(data.next_run[kind])}（{data.schedule[kind]}）。</p>
        <p className="meta">也可以现在生成 {label}：只收集事实（时间去向、产出、还没定的事），总结和三问要等回顾 Agent 写，或生成后点「让 Agent 写总结和三问」。</p>
        {button}
      </div>}
    </section>;
  };

  return <>
    {error && <div className="error" role="alert">{error}</div>}
    {data?.error && <div className="error" role="alert">{data.error}</div>}
    {data?.hub_error && <div className="notice-line" role="status">没读到 Agent 的进度：{data.hub_error}</div>}
    <div className="toolbar assistant-bar">
      {data && <span className="view-count">{data.weeks.length} 期周回顾 · {data.months.length} 期月回顾</span>}
      <button className="bar-end" onClick={() => void load()}>↻ 刷新</button>
    </div>
    {!data ? !error && <div className="notice">正在读取回顾…</div> : !data.error && <>
      {section("week", data.weeks)}
      {section("month", data.months)}
    </>}
  </>;
}
