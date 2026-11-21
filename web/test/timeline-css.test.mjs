import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// 评论时间线的标题行（作者、时间）横排；正文 .markdown 也是 li 下的 div，不能跟着变成横排，否则每段排成一竖列
test("timeline header flex rule leaves the markdown body as normal block flow", () => {
  const css = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
  assert.ok(!/\.timeline li>div\{display:flex/.test(css));
  assert.ok(css.includes(".timeline li>div:not(.markdown){display:flex"));
});

// 页头规则：没有「工作空间 / 页名」顶栏（左侧导航已高亮当前页），贴顶的东西从中栏顶边算
test("pages have no breadcrumb top bar and sticky offsets start at the top", () => {
  const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
  assert.ok(!app.includes('className="topbar"'));
  assert.ok(!app.includes("<header"));
  assert.match(css, /--bar-h:0px/);
});
