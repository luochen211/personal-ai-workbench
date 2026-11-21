// 插件弹窗（Plugins.tsx，后端 personal/plugins 的 overview）里的纯逻辑：每一项检查的记号和一句总结、「装上」按钮能不能点。
import type { PluginCheck, PluginInfo } from "./api";

/** 一项检查的记号：好了 ✓、没好 ✕、还说不准 –。 */
export function mark(check: PluginCheck): string {
  return check.ok === true ? "✓" : check.ok === false ? "✕" : "–";
}

/** 标题旁那一句：装没装，和检查里有几项没好。 */
export function summary(plugin: PluginInfo): string {
  const bad = plugin.checks.filter(c => c.ok === false).length;
  if (!plugin.installed) return "没装";
  return bad ? `已装 · ${bad} 项还没好` : "已装 · 都好了";
}

/** 第一次装要给读取工具目录和 python；已有配置就不用。 */
export function canInstall(plugin: PluginInfo, form: { vault_dir: string; python: string }): boolean {
  return !plugin.installed && (!plugin.needs_config || (!!form.vault_dir.trim() && !!form.python.trim()));
}
