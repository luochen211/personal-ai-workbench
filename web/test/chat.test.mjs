// 「对话」页的小部件：草稿段不在正文里重复、查询写成一行、正在回答的「已运行」（node --test 直接加载 .ts）
import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { LiveView, MessageView, toolSummary, visibleText } from "../src/ChatParts.ts";

test("issue-draft blocks are hidden from the text, finished or still being written", () => {
  assert.equal(visibleText("要改代码，拟了一条：\n```issue-draft\n{\"repo\":\"me/x\"}\n```\n就这样"), "要改代码，拟了一条：\n\n就这样");
  assert.equal(visibleText("拟了一条：\n```issue-draft\n{\"repo\":"), "拟了一条：");
  assert.equal(visibleText("普通代码\n```py\nprint(1)\n```"), "普通代码\n```py\nprint(1)\n```");
});

test("tool summary lists at most four", () => {
  assert.equal(toolSummary([]), "");
  const tools = [1, 2, 3, 4, 5].map(n => ({ name: "读文件", detail: `/a/b/c${n}.md` }));
  assert.equal(toolSummary(tools), "查了 5 次：读文件 b/c1.md、读文件 b/c2.md、读文件 b/c3.md、读文件 b/c4.md…");
});

test("messages: mine on the right, replies with facts and errors, live shows elapsed", () => {
  const mine = renderToStaticMarkup(createElement(MessageView, { message: { role: "user", text: "在吗", at: "" } }));
  assert.match(mine, /chat-msg mine/);
  const reply = renderToStaticMarkup(createElement(MessageView, { message: { role: "assistant", text: "**在**", at: "", seconds: 75,
    tools: [{ name: "查询", detail: "api autoloop/runs" }], error: "额度用完了" } }));
  assert.match(reply, /<strong>在<\/strong>/);
  assert.match(reply, /没答完：额度用完了/);
  assert.match(reply, /查了 1 次：查询 api autoloop\/runs · 用时 1 分 15 秒/);
  const live = renderToStaticMarkup(createElement(LiveView, { live: { text: "", tools: [], started_at: "2026-11-21T10:00:00Z" },
    now: Date.parse("2026-11-21T10:00:07Z") }));
  assert.match(live, /正在想 · 已运行 7 秒/);
});
