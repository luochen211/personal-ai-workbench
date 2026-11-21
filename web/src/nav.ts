// 左侧主导航（图标窄条）和页内标签。不写 JSX，node --test 可以直接加载这个文件做渲染测试。
import { createElement as h } from "react";
import type { ReactNode } from "react";
import { legacyInboxHash, parseInboxHash } from "./InboxParts.ts";  // 带扩展名：node --test 直接加载

export interface Tab { key: string; title: string }
export interface NavItem { key: string; title: string; tabs?: Tab[] }

// 五个入口；「对话」（#/chat，用户 2026-11-21 要求）紧跟收件箱，是只读的助理，没有页内标签；网址 #/<页内标签>，收件箱是主页 #/inbox（原「消息」「Issue」「待你处理」三页并成的），项目详情 #/p/<项目> 不在导航里，算在收件箱下。
// 「助理」五个页内标签、「自媒体」两个、「Agent」两个，标签的 key 就是网址：#/brief、#/boards、#/learning、#/review、#/health、#/media、#/content、#/runs、#/watch。
// 「自媒体」放在「助理」和「Agent」之间：它和助理一样是本人在做的事（「在做的内容」就读自助理目录），Agent 是工作台自己的运行状态，留在最后。
export const NAV: NavItem[] = [
  { key: "inbox", title: "收件箱" },
  { key: "chat", title: "对话" },
  { key: "assistant", title: "助理", tabs: [{ key: "brief", title: "今日简报" }, { key: "boards", title: "事项" }, { key: "learning", title: "学习" }, { key: "review", title: "回顾" }, { key: "health", title: "健康" }] },
  { key: "media", title: "自媒体", tabs: [{ key: "media", title: "麦田" }, { key: "content", title: "在做的内容" }] },
  { key: "agent", title: "Agent", tabs: [{ key: "runs", title: "运行记录" }, { key: "watch", title: "巡检状态" }] },
];
const PAGES = [...NAV.flatMap(n => (n.tabs ?? []).map(t => t.key)), "chat"];

/** 旧网址换成的新网址（消息、PR 待处理、Issue 列表、按项目、待你处理都进收件箱，见 InboxParts.legacyInboxHash）；不用换时是 null。 */
export function movedHash(hash: string): string | null {
  return legacyInboxHash(hash);
}

/** 网址对应的页面：页内标签的 key、"inbox"、"project"；认不出的进收件箱。 */
export function viewOf(hash: string): string {
  const target = movedHash(hash) ?? hash;
  if (parseInboxHash(target)) return "inbox";
  const page = target.match(/^#\/(\w+)$/)?.[1] ?? target.match(/^#\/(runs)\/[0-9]+$/)?.[1];  // #/runs/<轮次> 是运行记录里打开的一轮
  if (page && PAGES.includes(page)) return page;
  return target.startsWith("#/p/") ? "project" : "inbox";
}

/** 页面属于哪个导航入口；项目详情算在收件箱下。 */
export function sectionOf(view: string): NavItem {
  return NAV.find(n => n.key === view || (n.tabs ?? []).some(t => t.key === view)) ?? NAV.find(n => n.key === "inbox")!;
}

/** 点导航入口去哪一页：有页内标签的回到上次看的那个标签，没看过就是第一个。 */
export function entryView(item: NavItem, last: Record<string, string> = {}): string {
  if (!item.tabs) return item.key;
  return item.tabs.some(t => t.key === last[item.key]) ? last[item.key] : item.tabs[0].key;
}

// 线条图标：24×24、圆头圆角、描边粗细由 CSS 统一控制（当前页加粗）
const ICONS: Record<string, ReactNode[]> = {
  inbox: [h("path", { key: 1, d: "M6 16.5v-5.3a6 6 0 0 1 12 0v5.3l1.6 2H4.4z" }), h("path", { key: 2, d: "M10 21h4" })],
  assistant: [h("path", { key: 1, d: "M12 6.8C9.6 5.2 6.4 4.8 3.5 5.6v12.8c2.9-.8 6.1-.4 8.5 1.2 2.4-1.6 5.6-2 8.5-1.2V5.6c-2.9-.8-6.1-.4-8.5 1.2z" }), h("path", { key: 2, d: "M12 6.8v12.8" })],
  chat: [h("path", { key: 1, d: "M4.5 6.5a2.5 2.5 0 0 1 2.5-2.5h10a2.5 2.5 0 0 1 2.5 2.5v7a2.5 2.5 0 0 1-2.5 2.5H11l-4.5 3.5V16H7a2.5 2.5 0 0 1-2.5-2.5z" }), h("path", { key: 2, d: "M8.5 9h7M8.5 12h4.5" })],
  media: [h("rect", { key: 1, x: 3, y: 5.5, width: 18, height: 13, rx: 3.5 }), h("path", { key: 2, d: "M10.2 9.4v5.2l4.4-2.6z" })],
  agent: [h("rect", { key: 1, x: 4, y: 8, width: 16, height: 11.5, rx: 3.5 }), h("path", { key: 2, d: "M12 8V4.5M9.2 13v1.6M14.8 13v1.6M2 13v2.5M22 13v2.5" })],
  moon: [h("path", { key: 1, d: "M20 14.6A8.2 8.2 0 0 1 9.4 4a8.2 8.2 0 1 0 10.6 10.6z" })],
  plug: [h("path", { key: 1, d: "M9 3.5v4M15 3.5v4M6.5 7.5h11v3.5a5.5 5.5 0 0 1-11 0z" }), h("path", { key: 2, d: "M12 16.5v4" })],
  sun: [h("circle", { key: 1, cx: 12, cy: 12, r: 4 }), h("path", { key: 2, d: "M12 2.8v2M12 19.2v2M2.8 12h2M19.2 12h2M5.5 5.5l1.4 1.4M17.1 17.1l1.4 1.4M5.5 18.5l1.4-1.4M17.1 6.9l1.4-1.4" })],
};
export function Icon({ name }: { name: string }) {
  return h("svg", { className: "rail-icon", viewBox: "0 0 24 24", width: 24, height: 24, fill: "none", stroke: "currentColor",
    strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true, focusable: false }, ICONS[name]);
}

export interface RailProps {
  view: string;
  counts: Record<string, { value: number; note?: string }>;  // 导航入口 key → 角标数和它的说明
  theme: "light" | "dark";
  go: (item: NavItem) => void;
  home: () => void;
  toggleTheme: () => void;
  openPlugins: () => void;  // 插件弹窗（Plugins.tsx）
}

/** 图标窄条：一列图标，悬停或键盘聚焦时显示名字；当前页加粗高亮；待办数是图标右上角的小圆点。窄屏时变成底部一排。 */
export function Rail({ view, counts, theme, go, home, toggleTheme, openPlugins }: RailProps) {
  const current = sectionOf(view).key;
  const tip = (text: string) => h("span", { className: "rail-tip", "aria-hidden": true }, text);
  const themeName = theme === "dark" ? "浅色模式" : "深色模式";
  return h("aside", { className: "sidebar rail" },
    h("a", { className: "brand", href: "#/inbox", "aria-label": "派活 · 个人开发工作台", onClick: (e: { preventDefault: () => void }) => { e.preventDefault(); home(); } },
      h("span", { className: "brand-mark" }, "派"), tip("派活")),
    h("nav", { "aria-label": "主导航" }, NAV.map(n => {
      const on = n.key === current;
      const count = counts[n.key]?.value ?? 0;
      const label = count > 0 ? `${n.title}，${counts[n.key].note || `${count} 条`}` : n.title;
      return h("button", { key: n.key, className: on ? "active" : "", "aria-label": label, "aria-current": on ? "page" : undefined, onClick: () => go(n) },
        h(Icon, { name: n.key }),
        count > 0 && h("em", { className: "badge", "aria-hidden": true }, count > 99 ? "99+" : String(count)),
        tip(n.title));
    })),
    h("div", { className: "rail-bottom" },
      h("button", { "aria-label": "插件", onClick: openPlugins }, h(Icon, { name: "plug" }), tip("插件")),
      h("button", { "aria-label": themeName, onClick: toggleTheme }, h(Icon, { name: theme === "dark" ? "sun" : "moon" }), tip(themeName))));
}

/** 页内顶部的一排文字标签：选中项下方一条强调色短线。它就是这页的标题，页面不再写同名 h1（规则见 App.tsx 页头规则）。
 *  通用样式在 styles.css 的 .tabs / .tab，.page-tabs 让它贴着中栏顶边、字号按标题算。 */
export function Tabs({ tabs, value, pick, label }: { tabs: Tab[]; value: string; pick: (key: string) => void; label: string }) {
  return h("div", { className: "tabs page-tabs", role: "tablist", "aria-label": label }, tabs.map(t =>
    h("button", { key: t.key, role: "tab", className: "tab", "aria-selected": t.key === value, onClick: () => pick(t.key) }, t.title)));
}
