import { useCallback, useEffect, useState } from "react";
import Modal from "./Modal";
import Confirm from "./Confirm";
import { request } from "./api";
import type { PluginInfo, PluginsReport } from "./api";
import { canInstall, mark, summary } from "./PluginParts";

// 插件弹窗：左侧图标栏的「插件」打开。列出插件装没装，和装好了没有的几项检查；能「装上」「去掉」（去掉只停用、数据留着）。
// 彻底删除（--purge）只在命令行做，页面上只给命令。改动后 changed() 让收件箱重新读微信。
export default function Plugins({ close, changed }: { close: () => void; changed: () => void }) {
  const [report, setReport] = useState<PluginsReport | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [lines, setLines] = useState<string[]>([]);
  const [form, setForm] = useState({ vault_dir: "", python: "" });
  const [removing, setRemoving] = useState<PluginInfo | null>(null);
  const load = useCallback(async () => {
    try { setReport(await request<PluginsReport>("/api/plugins")); setError(""); } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const act = async (path: string, body: Record<string, string>) => {
    setBusy(true); setError("");
    try {
      const result = await request<PluginsReport>(path, body);
      setReport(result); setLines(result.lines ?? []); changed();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  return <Modal title="插件" close={close}>
    <h2>插件</h2>
    <p className="plugin-intro">装上才能用，去掉就停用；页面上的按钮和位置不变。去掉只停用，数据都留着，重新装上就回来。</p>
    {error && <div className="error" role="alert">{error}</div>}
    {lines.length > 0 && <div className="success-note" role="status">{lines.map(line => <div key={line}>{line}</div>)}</div>}
    {!report && !error && <p className="muted">正在读取…</p>}
    {report?.plugins.map(plugin => <section key={plugin.id} className="plugin-card">
      <header><h3>{plugin.name}</h3><span className={`tag ${plugin.installed ? "ok" : ""}`}>{summary(plugin)}</span></header>
      <ul className="plugin-checks">{plugin.checks.map(check => <li key={check.key} className={check.ok === false ? "bad" : check.ok ? "good" : ""}>
        <span className="plugin-mark" aria-hidden>{mark(check)}</span>
        <div><strong>{check.label}</strong><span className="muted">{check.detail}</span>{check.hint && <code>{check.hint}</code>}</div>
      </li>)}</ul>
      {!plugin.installed && plugin.needs_config && <div className="plugin-form">
        <label>读取工具的目录<input value={form.vault_dir} onChange={e => setForm({ ...form, vault_dir: e.target.value })} placeholder="目录里有 scripts/decrypt_all_dbs.py 和 scripts/vault_cli.py" /></label>
        <label>它要用的 Python<input value={form.python} onChange={e => setForm({ ...form, python: e.target.value })} placeholder="装了 pycryptodome、zstandard 的 python" /></label>
      </div>}
      <div className="plugin-actions">
        {plugin.installed
          ? <button disabled={busy} onClick={() => setRemoving(plugin)}>去掉</button>
          : <button className="primary" disabled={busy || !canInstall(plugin, form)} onClick={() => void act("/api/plugins/install", { id: plugin.id, ...form })}>{busy ? "装上中…" : "装上"}</button>}
        <button className="text-button" disabled={busy} onClick={() => void load()}>重新检查</button>
      </div>
      <p className="muted plugin-foot">完整步骤见仓库里的 <code>personal/plugins/{plugin.id}/README.md</code>；连数据一起删：<code>python3 -m personal.plugins uninstall {plugin.id} --purge</code></p>
    </section>)}
    {removing && <Confirm title={`去掉「${removing.name}」插件`} text="相关功能停用，中台里它的规则会删掉；数据都留着，重新装上就回来。" ok="去掉"
      close={() => setRemoving(null)} confirm={() => { const id = removing.id; setRemoving(null); void act("/api/plugins/uninstall", { id }); }} />}
  </Modal>;
}
