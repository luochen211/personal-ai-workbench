import { useCallback, useEffect, useState } from "react";
import Markdown from "./markdown";
import { request } from "./api";
import { count, scheduleLabel, stateLine, totals, weekLabel } from "./MediaParts";
import type { ContentReport, MaitianReport } from "./api";

// 自媒体页两个标签。麦田：工作台后端带口令读麦田的只读接口（personal/media.py），页面拿不到口令；「打开麦田大屏」走后端跳转。
// 在做的内容：只读助理目录 事项/自媒体与内容/README.md 里那张表，原样列出。页面不显示文件名和路径。
const errorText = (e: unknown) => e instanceof Error ? e.message : "读取失败";

export function MaitianPanel() {
  const [data, setData] = useState<MaitianReport | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const load = useCallback(async () => {
    setLoading(true);
    try { setData(await request<MaitianReport>("/api/media/maitian")); setError(""); }
    catch (e) { setError(errorText(e)); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const m = data?.maitian;
  const line = data ? stateLine(data) : "";
  const sum = m ? totals(m.followers) : null;
  return <>
    {error && <div className="error" role="alert">{error}</div>}
    <div className="toolbar assistant-bar">
      {m && m.waiting > 0 && <span className="view-count"><strong className="media-wait">{m.waiting} 个平台等你点发布</strong></span>}
      <button className="bar-end" onClick={() => void load()} disabled={loading}>↻ 刷新</button>
      {data?.state !== "down" && <a className="button primary" href="/media/maitian" target="_blank" rel="noreferrer">打开麦田大屏 ↗</a>}
    </div>
    {!data ? !error && <div className="notice">正在读取麦田…</div> : <>
      {line && <div className={data.state === "ok" ? "notice-line" : "gap-note"} role="status">{line}{data.state === "down" && <>：<code className="media-cmd">{data.start}</code></>}</div>}
      {m && m.problems.length > 0 && <div className="gap-note" role="alert">要你处理：{m.problems.join("；")}</div>}
      {m && <div className="boards">
        <section className="board media-block">
          <h2 className="media-head">最近几期</h2>
          <ul className="media-list">{m.episodes.map(e => <li key={e.id} className={e.waiting.length ? "media-waiting" : ""}>
            <div className="media-row media-ep">
              <span className="learning-no">{e.episode ?? ""}</span>
              <strong className="media-title">{e.title}</strong>
              <span className="media-when">{scheduleLabel(e.schedule) || "没排期"}</span>
            </div>
            <p className="media-states">{e.groups.map(g => <span key={g.state} className={`media-state ${g.tone}`}>
              <b>{g.label}</b> {g.platforms.join("、")}</span>)}{!e.groups.length && <span className="meta">还没有平台</span>}</p>
          </li>)}</ul>
          {!m.episodes.length && <p className="meta media-empty">麦田里还没有登记的期。</p>}
        </section>
        <section className="board media-block">
          <h2 className="media-head">粉丝{sum && m.followers.length > 0 && <small>合计 {count(sum.total)} · 近 7 天 {weekLabel({ week: sum.week })}</small>}</h2>
          <ul className="media-list">{m.followers.map(f => <li key={f.platform}><div className="media-row">
            <span className="media-title">{f.name}</span>
            <span className="media-num">{count(f.total)}</span>
            <span className={`media-num media-week${f.week && f.week > 0 ? " up" : ""}`} title="近 7 天">{weekLabel(f)}</span>
          </div></li>)}</ul>
          {!m.followers.length && <p className="meta media-empty">还没有粉丝记录。</p>}
        </section>
      </div>}
    </>}
  </>;
}

export function ContentPanel() {
  const [data, setData] = useState<ContentReport | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => request<ContentReport>("/api/media/content")
    .then(result => { setData(result); setError(""); }).catch(e => setError(errorText(e))), []);
  useEffect(() => { void load(); }, [load]);
  return <>
    {error && <div className="error" role="alert">{error}</div>}
    {data?.error && <div className="error" role="alert">{data.error}</div>}
    <div className="toolbar assistant-bar">
      {data && data.rows.length > 0 && <span className="view-count">{data.rows.length} 项</span>}
      <button className="bar-end" onClick={() => void load()}>↻ 刷新</button>
    </div>
    {!data ? !error && <div className="notice">正在读取在做的内容…</div> : data.rows.length > 0 && <section className="board media-block">
      <ul className="media-list">{data.rows.map((r, n) => <li key={n}>
        <div className="media-row">
          <strong className="media-title media-md"><Markdown text={r.content} /></strong>
          {r.publish && <span className={`tag${r.publish.includes("已上线") || r.publish.includes("已发布") ? " ok" : ""}`}>{r.publish}</span>}
        </div>
        {r.progress && <div className="media-progress media-md"><Markdown text={r.progress} /></div>}
      </li>)}</ul>
    </section>}
  </>;
}
