import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { request } from "./api";
import Markdown from "./markdown";
import type { HealthDay, HealthReport } from "./api";
import { BOX, CHART_DAYS, analysisNote, bedChart, clock, dayTicks, lineChart, readout, recent, shortDay, sleepChart, slots, span, stepsChart, target, tiles } from "./HealthParts";
import type { Tick } from "./HealthParts";

// 健康：只读助理目录的 资料/raw/health/zepp-daily.csv（助理仓库 scripts/zepp_import.py 从 Zepp 导出包生成），不写任何东西。
// 「分析」是助理目录 分析.md 的原文（AI 在导入数据后写的解读），页面只显示；「从数据里看到的」是后端从数据算的几句话。
// 四张图共用一组横向格子，鼠标停在哪天，四张图同时标出那一天，图上方一行字写那天的数；每天的数另有表格。
const errorText = (e: unknown) => e instanceof Error ? e.message : "读取失败";
const STAGES = [{ key: "deep", name: "深睡" }, { key: "light", name: "浅睡" }, { key: "rem", name: "REM" }] as const;
const HEART = [{ key: "avg", name: "日均" }, { key: "resting", name: "静息" }] as const;
const plotRight = BOX.w - BOX.right;
const plotBottom = BOX.h - BOX.bottom;

function Legend({ items }: { items: readonly { key: string; name: string }[] }) {
  return <span className="health-legend">{items.map(i => <span key={i.key}><i className={`health-swatch hz-${i.key}`} aria-hidden="true" />{i.name}</span>)}</span>;
}

/** 一张图的外框：标题、图例、网格、横轴日期、每天一格的悬停区。children 画数据。 */
function Chart({ title, note, legend, days, ticks, hover, setHover, children }: {
  title: string; note?: string; legend?: ReactNode; days: HealthDay[]; ticks: Tick[]; hover: string; setHover: (date: string) => void; children: ReactNode;
}) {
  const cols = slots(days.length);
  return <section className="board health-card">
    <div className="health-card-head"><h3>{title}</h3>{legend}{note && <span className="meta">{note}</span>}</div>
    <svg className="health-chart" viewBox={`0 0 ${BOX.w} ${BOX.h}`} role="img" aria-label={`${title}，每天的数见下方表格`} onMouseLeave={() => setHover("")}>
      {ticks.map(t => <g key={t.label}>
        <line className="health-grid" x1={BOX.left} x2={plotRight} y1={t.y} y2={t.y} />
        <text className="health-axis" x={BOX.left - 6} y={t.y + 4} textAnchor="end">{t.label}</text>
      </g>)}
      {days.map((d, i) => d.date === hover && <rect key={d.date} className="health-band" x={cols[i].left} y={BOX.top} width={cols[i].band} height={plotBottom - BOX.top} />)}
      {children}
      {dayTicks(days.length).map(i => <text key={days[i].date} className="health-axis" x={cols[i].cx} y={BOX.h - 6} textAnchor="middle">{shortDay(days[i].date)}</text>)}
      {days.map((d, i) => <rect key={d.date} className="health-hit" x={cols[i].left} y={0} width={cols[i].band} height={BOX.h} onMouseEnter={() => setHover(d.date)} />)}
    </svg>
  </section>;
}

export function HealthPanel() {
  const [data, setData] = useState<HealthReport | null>(null);
  const [error, setError] = useState("");
  const [hover, setHover] = useState("");
  const load = useCallback(() => request<HealthReport>("/api/health/daily")
    .then(result => { setData(result); setError(""); }).catch(e => setError(errorText(e))), []);
  useEffect(() => { void load(); }, [load]);

  const body = () => {
    if (!data || data.error) return null;
    if (data.empty) return <div className="empty-state"><strong>{data.empty}</strong><p className="meta">{data.update_hint}</p></div>;
    const days = recent(data.days);
    const cols = slots(days.length);
    const sleep = sleepChart(days, data.targets);
    const bed = bedChart(days);
    const heart = lineChart([{ key: "avg", values: days.map(d => d.hr_avg) }, { key: "resting", values: days.map(d => d.hr_resting) }]);
    const steps = stepsChart(days);
    const hovered = days.find(d => d.date === hover);
    const shared = { days, hover, setHover };
    const estimated = !!data.summary?.resting_estimated;
    const note = data.analysis && analysisNote(data.analysis, data.last_date);
    const cut = data.days.length > days.length ? `图里是最近 ${CHART_DAYS} 天` : "";
    return <>
      {data.stale && <div className="notice-line" role="status">{data.stale}{data.update_hint}</div>}
      <div className="summary health-tiles">{tiles(data.summary).map(t => <div key={t.label} className="stat"><span>{t.label}</span><b>{t.value}</b><span>{t.note}</span></div>)}</div>
      {data.analysis && note && <section className="board health-card health-analysis">
        <div className="health-card-head"><h3>分析</h3><span className={`meta${note.warn ? " bad" : ""}`}>{note.text}</span></div>
        <Markdown text={data.analysis.text} />
      </section>}
      {data.findings.length > 0 && <section className="board health-card">
        <div className="health-card-head"><h3>从数据里看到的</h3>
          {data.summary && data.summary.nights < 14 && <span className="meta">只有 {data.summary.nights} 晚的数据，数据多了这些结论可能变</span>}</div>
        <ul className="health-findings">{data.findings.map(f => <li key={f}>{f}</li>)}</ul>
      </section>}
      <p className="meta health-readout" role="status">{hovered ? readout(hovered, data.targets) : `把鼠标停在图上的某一天，看那天的数。${cut}`}</p>
      <Chart title="每晚睡了多久" legend={<Legend items={STAGES} />} ticks={sleep.ticks} {...shared}>
        {sleep.lines.map(l => <g key={l.key}>
          <line className="health-target" x1={BOX.left} x2={plotRight} y1={l.y} y2={l.y} />
          <text className="health-axis" x={plotRight + 6} y={l.y + 4}>{l.label}</text>
        </g>)}
        {sleep.bars.map((b, i) => b.parts.map(p => <rect key={`${b.date}${p.key}`} className={`hz-${p.key}`} x={cols[i].x} y={p.y} width={cols[i].w} height={p.h} rx={2} />))}
      </Chart>
      <Chart title="几点睡、几点醒" note="竖条从入睡画到醒来" ticks={bed.ticks} {...shared}>
        {bed.bars.map((b, i) => b && <rect key={b.date} className="hz-deep" x={cols[i].x} y={b.y} width={cols[i].w} height={b.h} rx={3} />)}
      </Chart>
      <Chart title="心率（次/分）" legend={<Legend items={HEART} />} note={estimated ? "静息心率是用当天心率的第 10 百分位估算的" : undefined} ticks={heart.ticks} {...shared}>
        {heart.lines.map(l => <g key={l.key} className={`hz-line-${l.key}`}>
          <path className="health-line" d={l.path} />
          {days.length <= 45 && l.points.map((p, i) => p && <circle key={i} className="health-dot" cx={p.x} cy={p.y} r={4} />)}
          {l.end && <text className="health-axis" x={plotRight + 6} y={l.end.y + 4}>{HEART.find(h => h.key === l.key)?.name}</text>}
        </g>)}
      </Chart>
      <Chart title="每天步数" note="浅色的是不满一整天的（开始戴表那天、导出当天）" ticks={steps.ticks} {...shared}>
        {steps.bars.map((b, i) => b && <rect key={b.date} className={`hz-deep${b.partial ? " health-partial" : ""}`} x={cols[i].x} y={b.y} width={cols[i].w} height={b.h} rx={2} />)}
      </Chart>
      <details className="board health-card health-table">
        <summary>每天的数据（{data.days.length} 天）</summary>
        <div className="health-scroll"><table>
          <thead><tr><th>日期</th><th>入睡</th><th>醒来</th><th>睡眠</th><th>对照目标</th><th>深睡</th><th>浅睡</th><th>REM</th><th>清醒</th><th>日均心率</th><th>静息心率</th><th>步数</th></tr></thead>
          <tbody>{[...data.days].reverse().map(d => {
            const goal = d.sleep_min !== null ? target(d.sleep_min, data.targets) : null;
            const cell = (v: number | null, show: (n: number) => string = String) => v === null ? "—" : show(v);
            return <tr key={d.date}>
              <th scope="row">{shortDay(d.date)}</th><td>{cell(d.bed, clock)}</td><td>{cell(d.wake, clock)}</td><td>{cell(d.sleep_min, span)}</td>
              <td>{goal ? <span className={`tag ${goal.tone}`}>{goal.text}</span> : "—"}</td>
              <td>{cell(d.deep_min, span)}</td><td>{cell(d.light_min, span)}</td><td>{cell(d.rem_min, span)}</td><td>{cell(d.awake_min, span)}</td>
              <td>{cell(d.hr_avg)}</td><td>{cell(d.hr_resting)}{d.hr_resting !== null && d.hr_estimated ? "*" : ""}</td>
              <td>{cell(d.steps, n => n.toLocaleString("en-US"))}{d.steps !== null && d.partial ? "（不是整天）" : ""}</td>
            </tr>;
          })}</tbody>
        </table></div>
        {estimated && <p className="meta">* 导出包里没有静息心率，用当天心率的第 10 百分位估算。</p>}
      </details>
    </>;
  };

  return <>
    {error && <div className="error" role="alert">{error}</div>}
    {data?.error && <div className="error" role="alert">{data.error}</div>}
    <div className="toolbar assistant-bar">
      {data?.last_date && <span className="view-count">{data.first_date} 至 {data.last_date} · {data.days.length} 天 · 来自 Zepp 手表，{data.updated.slice(0, 16).replace("T", " ")} 导入</span>}
      <button className="bar-end" onClick={() => void load()}>↻ 刷新</button>
    </div>
    {!data ? !error && <div className="notice">正在读取健康数据…</div> : body()}
  </>;
}
