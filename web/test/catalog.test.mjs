// 原 Issue 列表（主页 #/）的旧网址：只用来换成收件箱的网址（node --test test/）
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseHome } from "../src/catalog.ts";

const home = (extra = {}) => ({ mode: "issue", group: "", sub: "", tag: "", project: "", ...extra });

test("old issue-list addresses are still read so they can move into the inbox", () => {
  for (const hash of ["", "#", "#/", "#/issues"]) assert.deepEqual(parseHome(hash), home(), hash);
  // 项目总览的旧网址：进「按项目」，分组、二级分类、标签不变
  assert.deepEqual(parseHome(encodeURI("#/?g=开源协作&s=编码 Agent&t=上游贡献")),
    home({ mode: "project", group: "开源协作", sub: "编码 Agent", tag: "上游贡献" }));
  assert.deepEqual(parseHome(encodeURI("#/?v=issue&p=codewhale")), home({ project: "codewhale" }));
  for (const hash of ["#/todo", "#/runs", "#/messages", "#/p/codewhale", "#/i/o/r/1", "#/q/3"]) assert.equal(parseHome(hash), null, hash);
});
