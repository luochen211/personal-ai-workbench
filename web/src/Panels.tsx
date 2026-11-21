import { useEffect, useState } from "react";
import type { MouseEvent } from "react";
import { request } from "./api";
import Modal from "./Modal";
import { WatchView, duration, failed, finished, stateName, when } from "./RunParts";
import type { RunRow, WatchStatus } from "./RunParts";
import { RunDetailView, parseRunHash, runHash, subjectLinks } from "./RunDetail";
import type { RunDetail } from "./RunDetail";

// Agent 运行记录、巡检状态两页；数据都来自工作台对 agent-autoloop 的只读接口。页内标签就是标题，工具条只写计数（规则见 App.tsx 页头规则）。
// idle：被隐藏的空跑和重报条数（没事可做的轮次、把派过的事件再报一次的监看轮）
// retired：已从 rules.json 删掉的规则留下的轮次，默认不列，只报条数
interface RunLog { runs: RunRow[]; total: number; idle: number; retired: number; error: string }
const errorText = (e: unknown) => e instanceof Error ? e.message : "读取失败";

function useLoad<T>(path: string, every = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    request<T>(path).then(d => { if (live) { setData(d); setError(""); } }).catch(e => { if (live) setError(errorText(e)); });
    return () => { live = false; };
  }, [path, tick]);
  useEffect(() => {
    if (!every) return;
    const id = setInterval(() => setTick(t => t + 1), every);
    return () => clearInterval(id);
  }, [every]);
  return { data, error, reload: () => setTick(t => t + 1) };
}

// 一轮的详情抽屉（#/runs/<轮次>，刷新可直达）：进行中的每 10 秒重读一次，ESC 或「关闭」回到列表
// 每秒走一次的时钟：只在 on 时开，给「已运行」实时跳秒；数据本身仍按原来的间隔重读
function useClock(on: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!on) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [on]);
  return now;
}

function RunDrawer({ id, close }: { id: number; close: () => void }) {
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => { setRun(null); setError(""); }, [id]);
  useEffect(() => {
    let live = true;
    request<RunDetail>(`/api/autoloop/runs/${id}`).then(d => { if (live) { setRun(d); setError(""); } }).catch(e => { if (live) setError(errorText(e)); });
    return () => { live = false; };
  }, [id, tick]);
  const active = !!run?.active;
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setTick(t => t + 1), 10000);
    return () => clearInterval(timer);
  }, [active]);
  const now = useClock(active);
  return <Modal title="运行详情" close={close}>
    {error && <div className="error" role="alert">{error}</div>}
    {run ? <RunDetailView run={run} now={now} /> : !error && <p className="meta">正在读取第 {id} 轮…</p>}
  </Modal>;
}

export function RunsPanel() {
  const { data, error, reload } = useLoad<RunLog>("/api/autoloop/runs", 30000);
  const [opened, setOpened] = useState(() => parseRunHash(location.hash));
  useEffect(() => {
    const follow = () => setOpened(parseRunHash(location.hash));
    addEventListener("popstate", follow);
    addEventListener("hashchange", follow);
    return () => { removeEventListener("popstate", follow); removeEventListener("hashchange", follow); };
  }, []);
  const open = (id: number) => { if (location.hash !== runHash(id)) history.pushState(null, "", runHash(id)); setOpened(id); };
  const close = () => { history.pushState(null, "", "#/runs"); setOpened(null); };
  // 整行可点；行里的对象链接、编号链接照常走自己的去处
  const rowClick = (id: number) => (e: MouseEvent) => { if (!(e.target as HTMLElement).closest("a,button,input,select")) open(id); };
  const [rule, setRule] = useState("");
  const [onlyFailed, setOnlyFailed] = useState(false);
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [rule, onlyFailed]);
  const runs = (data?.runs ?? []).filter(r => (!rule || r.rule === rule) && (!onlyFailed || failed(r)));
  const rules = [...new Set((data?.runs ?? []).map(r => r.rule))];
  const now = useClock((data?.runs ?? []).some(r => r.state === "claimed"));
  const pages = Math.max(1, Math.ceil(runs.length / 50));
  return <>
    {(error || data?.error) && <div className="error" role="alert">{error || data?.error}</div>}
    <div className="toolbar"><div className="view-count">{data ? `${runs.length} 条${data.total > data.runs.length ? ` / 共 ${data.total}` : ""}` : "…"}</div>
      <select aria-label="筛选规则" value={rule} onChange={e => setRule(e.target.value)}><option value="">全部规则</option>{rules.map(r => <option key={r}>{r}</option>)}</select>
      <label className="check"><input type="checkbox" checked={onlyFailed} onChange={e => setOnlyFailed(e.target.checked)} /> 只看失败</label>
      <button onClick={reload}>↻ 刷新</button>
    </div>
    {!!(data?.idle || data?.retired) && <p className="meta">已隐藏 {[data.idle ? `${data.idle} 条没事可做或重报的轮次（比如没有新的带 agent 标签的 issue）` : "",
      data.retired ? `${data.retired} 条已停用规则的轮次` : ""].filter(Boolean).join("、")}。</p>}
    <div className="task-list">{runs.slice((page - 1) * 50, page * 50).map(r => <div key={r.id} className={`run-row clickable${opened === r.id ? " on" : ""}`} onClick={rowClick(r.id)}>
      <a className="num" href={runHash(r.id)} aria-label={`打开第 ${r.id} 轮详情`} onClick={e => { e.preventDefault(); open(r.id); }}>#{r.id}</a>
      <div className="run-main"><strong>{r.rule}{r.subjects.map(s => { const href = subjectLinks(s, r.rule)?.inbox; return href ? <a key={s} href={href}>{s}</a> : <span key={s}>{s}</span>; })}</strong>
        <p className={failed(r) ? "run-error" : ""}>{failed(r) ? `原因：${r.error || r.summary || "被停止"}` : r.summary || (finished(r) ? "（没有结论）" : r.error || "进行中")}</p></div>
      <div className="run-side"><span className={`tag ${failed(r) ? "bad" : finished(r) ? "ok" : "warn"}`}>{stateName[r.state] ?? r.state}{r.attempts > 1 ? ` · 试了 ${r.attempts} 次` : ""}</span><small>{when(r.created_at)} · {duration(r, now)}</small></div>
    </div>)}{data && !runs.length && <div className="notice">没有符合条件的运行。</div>}{!data && !error && <div className="notice">正在读取运行记录…</div>}</div>
    {opened !== null && <RunDrawer id={opened} close={close} />}
    {pages > 1 && <div className="pagination"><span>第 {page} / {pages} 页</span><div><button disabled={page === 1} onClick={() => setPage(p => p - 1)}>上一页</button><button disabled={page === pages} onClick={() => setPage(p => p + 1)}>下一页</button></div></div>}
  </>;
}

export function WatchPanel() {
  const { data, error, reload } = useLoad<WatchStatus>("/api/autoloop/watch", 60000);
  if (error) return <div className="error" role="alert">{error}</div>;
  if (!data) return <div className="notice">正在读取巡检状态…</div>;
  return <WatchView data={data} reload={reload} />;
}
