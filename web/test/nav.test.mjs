// 左侧主导航（图标窄条）、并掉的入口和旧网址、页内标签（node --test 直接加载 .ts）
import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { NAV, Rail, Tabs, entryView, movedHash, sectionOf, viewOf } from "../src/nav.ts";

const html = (component, props) => renderToStaticMarkup(createElement(component, props));
const rail = (extra = {}) => html(Rail, { view: "", counts: {}, theme: "light", go: () => {}, home: () => {}, toggleTheme: () => {}, openPlugins: () => {}, ...extra });

test("the rail has five entries in order, icons only; 收件箱 replaces 消息、Issue and 待你处理", () => {
  assert.deepEqual(NAV.map(n => n.title), ["收件箱", "对话", "助理", "自媒体", "Agent"]);
  const out = rail();
  assert.equal((out.match(/<nav[^>]*>.*<\/nav>/)[0].match(/<button/g) ?? []).length, 5);
  for (const n of NAV) assert.match(out, new RegExp(`aria-label="${n.title}"`));
  assert.equal((out.match(/<svg class="rail-icon"/g) ?? []).length, 7);  // 五个入口加插件、主题切换
  assert.match(out, /aria-label="自媒体"[^>]*><svg[^>]*><rect x="3" y="5.5"/);  // 自媒体：视频框加播放键
  assert.match(out, /aria-label="收件箱"[^>]*><svg[^>]*><path d="M6 16.5v-5.3a6 6 0 0 1 12 0v5.3l1.6 2H4.4z"/);  // 沿用消息的铃铛图标
  assert.match(out, /class="brand" href="#\/inbox"/);
  assert.doesNotMatch(out, /[✓☀◧☷⇄↻✉◎]/);
  assert.doesNotMatch(out, /卡|仓库主人|维护者|协作者|PR 待处理|待你处理/);
});

test("old routes still open their page; 待你处理, the messages page, the issue list and the project grid move into the inbox", () => {
  for (const [hash, view] of [["#/todo", "inbox"], ["#/brief", "brief"], ["#/media", "media"], ["#/content", "content"], ["#/boards", "boards"], ["#/learning", "learning"], ["#/review", "review"], ["#/health", "health"], ["#/runs", "runs"], ["#/watch", "watch"],
    ["#/inbox", "inbox"], ["#/inbox?f=todo&s=o/app", "inbox"], ["#/messages", "inbox"], ["#/messages?r=o/app", "inbox"],
    ["#/prs", "inbox"], ["#/p/codewhale", "project"], ["#/", "inbox"], ["", "inbox"], ["#/issues", "inbox"], ["#/?v=project", "inbox"],
    ["#/i/o/r/3", "inbox"], ["#/bogus", "inbox"], ["#/runs/2079", "runs"], ["#/runs/x", "inbox"], ["#/chat", "chat"]]) assert.equal(viewOf(hash), view, hash);
  assert.equal(movedHash("#/prs"), "#/inbox");
  assert.equal(movedHash("#/todo"), "#/inbox");
  assert.equal(movedHash("#/issues"), "#/inbox?f=all");
  assert.equal(movedHash("#/"), "#/inbox");
  assert.equal(movedHash("#/messages?t=wait&s=o/app"), "#/inbox?f=wait&s=o/app");
  assert.equal(movedHash("#/?v=issue&p=codewhale"), "#/inbox?f=all&p=codewhale");
  assert.equal(movedHash("#/brief"), null);
  assert.equal(movedHash("#/inbox"), null);
  assert.equal(movedHash("#/p/codewhale"), null);
});

test("merged pages: 助理 holds 今日简报, 事项, 学习, 回顾 and 健康, Agent holds 运行记录 and 巡检状态", () => {
  assert.equal(sectionOf("brief").title, "助理");
  assert.equal(sectionOf("learning").title, "助理");
  assert.deepEqual(NAV.find(n => n.key === "assistant").tabs.map(t => t.title), ["今日简报", "事项", "学习", "回顾", "健康"]);
  assert.equal(sectionOf("boards").title, "助理");
  assert.equal(sectionOf("runs").title, "Agent");
  assert.equal(sectionOf("watch").title, "Agent");
  assert.equal(sectionOf("project").key, "inbox");  // 项目详情算在收件箱下
  assert.equal(sectionOf("inbox").title, "收件箱");
  const assistant = NAV.find(n => n.key === "assistant");
  assert.equal(entryView(assistant), "brief");
  assert.equal(entryView(assistant, { assistant: "boards" }), "boards");
  assert.equal(entryView(assistant, { assistant: "learning" }), "learning");
  assert.equal(entryView(assistant, { assistant: "runs" }), "brief");
  assert.equal(entryView(NAV.find(n => n.key === "inbox")), "inbox");
});

test("the current page is marked and counts sit on the icon", () => {
  const out = rail({ view: "boards", counts: { inbox: { value: 3, note: "3 条待你回" } } });
  assert.match(out, /class="active" aria-label="助理" aria-current="page"/);
  assert.equal((out.match(/aria-current/g) ?? []).length, 1);
  assert.match(out, /aria-label="收件箱，3 条待你回"[^>]*>.*?<em class="badge" aria-hidden="true">3<\/em>/);
  assert.equal((out.match(/class="badge"/g) ?? []).length, 1);  // 0 不显示
  assert.match(rail({ counts: { inbox: { value: 120 } } }), />99\+<\/em>/);
  assert.match(rail({ counts: { inbox: { value: 7, note: "7 条待你回" } } }), /aria-label="收件箱，7 条待你回"/);
  assert.match(rail({ view: "project" }), /class="active" aria-label="收件箱"/);
  assert.match(rail({ view: "inbox" }), /class="active" aria-label="收件箱"/);
});

test("the plugins button and the theme switch sit at the bottom of the rail; the switch names what it switches to", () => {
  let toggled = 0, opened = 0;
  assert.match(rail({ theme: "light" }), /class="rail-bottom"><button aria-label="插件">.*<button aria-label="深色模式"/);
  assert.match(rail({ theme: "dark" }), /aria-label="浅色模式"/);
  const tree = Rail({ view: "", counts: {}, theme: "light", go: () => {}, home: () => {}, toggleTheme: () => toggled++, openPlugins: () => opened++ });
  const [plugins, theme] = tree.props.children[2].props.children;
  plugins.props.onClick();
  theme.props.onClick();
  assert.equal(toggled, 1);
  assert.equal(opened, 1);
});

test("clicking an entry hands it to go", () => {
  const went = [];
  const tree = Rail({ view: "", counts: {}, theme: "light", go: n => went.push(n.key), home: () => {}, toggleTheme: () => {} });
  for (const button of tree.props.children[1].props.children) button.props.onClick();
  assert.deepEqual(went, ["inbox", "chat", "assistant", "media", "agent"]);
});

test("page tabs are a tablist with the selected one marked", () => {
  const picked = [];
  const tabs = NAV.find(n => n.key === "agent").tabs;
  const out = html(Tabs, { tabs, value: "watch", pick: () => {}, label: "Agent" });
  assert.match(out, /^<div class="tabs page-tabs" role="tablist" aria-label="Agent">/);
  assert.match(out, /role="tab" class="tab" aria-selected="false">运行记录</);
  assert.match(out, /role="tab" class="tab" aria-selected="true">巡检状态</);
  for (const button of Tabs({ tabs, value: "runs", pick: k => picked.push(k), label: "Agent" }).props.children) button.props.onClick();
  assert.deepEqual(picked, ["runs", "watch"]);
});

test("自媒体 holds 麦田 (#/media) and 在做的内容 (#/content); the rail marks it on either tab", () => {
  const media = NAV.find(n => n.key === "media");
  assert.deepEqual(media.tabs.map(t => [t.key, t.title]), [["media", "麦田"], ["content", "在做的内容"]]);
  assert.equal(sectionOf("media").title, "自媒体");
  assert.equal(sectionOf("content").title, "自媒体");
  assert.equal(entryView(media), "media");
  assert.equal(entryView(media, { media: "content" }), "content");
  assert.equal(movedHash("#/media"), null);
  assert.match(rail({ view: "content" }), /class="active" aria-label="自媒体" aria-current="page"/);
  assert.doesNotMatch(html(Tabs, { tabs: media.tabs, value: "media", pick: () => {}, label: "自媒体" }), /卡/);
});
