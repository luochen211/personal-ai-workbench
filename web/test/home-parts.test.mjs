// 项目详情里的 issue 入口（node --test 直接加载 .ts）
import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ProjectIssueEntry } from "../src/HomeParts.ts";

const html = (component, props) => renderToStaticMarkup(createElement(component, props));

test("the project page has one line linking to its repository in the inbox", () => {
  let went = 0, prevented = 0;
  const props = { open: 12, done: 5, href: "#/inbox?f=all&s=codewhale-hq/Codewhale", go: () => went++ };
  const out = html(ProjectIssueEntry, props);
  assert.match(out, /这个项目的 issue：<b>open 12<\/b> · <b>已完成 5<\/b>/);
  assert.match(out, /href="#\/inbox\?f=all&amp;s=codewhale-hq\/Codewhale">→ 查看/);
  assert.match(html(ProjectIssueEntry, { ...props, open: null, done: null }), /open …<\/b> · <b>已完成 …/);
  const link = ProjectIssueEntry(props).props.children.at(-1);
  link.props.onClick({ preventDefault: () => prevented++ });
  assert.equal(went, 1); assert.equal(prevented, 1);
  assert.doesNotMatch(out, /卡/);
});
