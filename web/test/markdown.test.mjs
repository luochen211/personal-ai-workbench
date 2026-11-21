// 渲染测试：node --test test/（Node 24 直接加载 .ts）
import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import Markdown from "../src/markdown.ts";

const html = (text, repo = "o/r") => renderToStaticMarkup(createElement(Markdown, { text, repo }));

test("scripts and event handlers never reach the page", () => {
  const out = html('<script>alert(1)</script>\n\n<img src=x onerror="alert(2)">\n\n[x](javascript:alert(3))\n\n<a href="#" onclick="alert(4)">a</a>');
  assert.doesNotMatch(out, /<script/i);
  assert.doesNotMatch(out, /onerror|onclick/i);
  assert.doesNotMatch(out, /href="javascript:/i);
});

test("GitHub flavoured blocks render", () => {
  const out = html("## What and why\n\n- [x] done\n- [ ] todo\n  - nested\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n~~old~~ `code`\n\n```js\nconst x = 1;\n```\n\n> quote\n\n![shot](https://example.com/a.png)");
  assert.match(out, /<h2>What and why<\/h2>/);
  assert.match(out, /<input[^>]*type="checkbox"[^>]*disabled/);
  assert.match(out, /<table>/);
  assert.match(out, /<del>old<\/del>/);
  assert.match(out, /<code>code<\/code>/);
  assert.match(out, /<pre><code class="language-js">/);
  assert.match(out, /<blockquote>/);
  assert.match(out, /<img[^>]*src="https:\/\/example.com\/a.png"/);
});

test("references and links point at GitHub and open in a new tab", () => {
  const out = html("Refs #7274, see other/repo#12 and thanks @luochen211. Visit https://example.com. Not in `#9`.");
  assert.match(out, /href="https:\/\/github.com\/o\/r\/issues\/7274" target="_blank" rel="noopener noreferrer">#7274/);
  assert.match(out, /href="https:\/\/github.com\/other\/repo\/issues\/12"/);
  assert.match(out, /href="https:\/\/github.com\/luochen211"/);
  assert.match(out, /href="https:\/\/example.com"/);
  assert.match(out, /<code>#9<\/code>/);
});
