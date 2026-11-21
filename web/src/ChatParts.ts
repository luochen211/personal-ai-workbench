// 「对话」页的数据形状和小部件（后端 personal/chat.py）。不写 JSX，node --test 可以直接加载这个文件。
import { createElement as h } from "react";
import Markdown from "./markdown.ts";
import { spent } from "./RunParts.ts";

export interface ChatTool { name: string; detail: string }
export interface ChatDraft { repo: string; title: string; body: string }
export interface ChatMessage {
  role: "user" | "assistant"; text: string; at: string;
  tools?: ChatTool[]; drafts?: ChatDraft[]; error?: string; seconds?: number; cost?: number;
}
export interface ChatDetail {
  id: string; title: string; messages: ChatMessage[]; running: boolean;
  live?: { text: string; tools: ChatTool[]; started_at: string };
}
export interface ChatRow { id: string; title: string; updated_at: string | null; running: boolean }
export interface ChatList { chats: ChatRow[]; model: string; ready: boolean; program: string }

/** 回复里给卡片用的 ```issue-draft 段不在正文里重复显示；还没写完的那一段（没有收尾的 ```）也先藏起来。 */
export function visibleText(text: string) {
  return text.replace(/```issue-draft\s*\n[\s\S]*?(\n```|$)/g, "").trim();
}

/** 用过的查询写成一行：「查了 3 次：api autoloop/runs、读文件 README.md…」，最多列 4 个。 */
export function toolSummary(tools: ChatTool[] = []) {
  if (!tools.length) return "";
  const names = tools.slice(0, 4).map(t => `${t.name} ${t.detail.split("/").slice(-2).join("/")}`.trim());
  return `查了 ${tools.length} 次：${names.join("、")}${tools.length > 4 ? "…" : ""}`;
}

/** 一条消息：本人的靠右；助理的用 Markdown，下面一行写查了什么、用时，出错写原因。 */
export function MessageView({ message }: { message: ChatMessage }) {
  if (message.role === "user") return h("div", { className: "chat-msg mine" }, h("p", null, message.text));
  const text = visibleText(message.text);
  const facts = [toolSummary(message.tools), message.seconds !== undefined ? `用时 ${spent(message.seconds)}` : ""].filter(Boolean).join(" · ");
  return h("div", { className: "chat-msg" },
    text ? h(Markdown, { text }) : !message.error && h("p", { className: "meta" }, "（没有回复）"),
    message.error && h("p", { className: "run-error" }, `没答完：${message.error}`),
    facts && h("small", { className: "chat-facts" }, facts));
}

/** 正在回答：已经收到的字和最近几次查询，「已运行」按 now 每秒跳。 */
export function LiveView({ live, now = Date.now() }: { live: NonNullable<ChatDetail["live"]>; now?: number }) {
  const text = visibleText(live.text);
  const last = live.tools[live.tools.length - 1];
  const seconds = Math.max(0, Math.round((now - Date.parse(live.started_at)) / 1000));
  return h("div", { className: "chat-msg live" },
    text && h(Markdown, { text }),
    h("small", { className: "chat-facts" }, `${last ? `正在${last.name} ${last.detail}` : text ? "正在写" : "正在想"} · 已运行 ${spent(seconds)}`));
}
