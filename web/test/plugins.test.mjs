// 插件弹窗（PluginParts.ts，后端 personal/plugins 的 overview）：检查项的记号、标题旁的一句、「装上」能不能点。
import { test } from "node:test";
import assert from "node:assert/strict";
import { canInstall, mark, summary } from "../src/PluginParts.ts";

const check = (ok) => ({ key: "k", label: "一项", ok, detail: "", hint: "" });
const plugin = (extra = {}) => ({ id: "wechat", name: "微信", installed: false, needs_config: true, checks: [check(true), check(false), check(null)], ...extra });

test("每一项检查的记号", () => {
  assert.deepEqual([true, false, null].map(ok => mark(check(ok))), ["✓", "✕", "–"]);
});

test("标题旁那一句：没装、还有几项没好、都好了", () => {
  assert.equal(summary(plugin()), "没装");
  assert.equal(summary(plugin({ installed: true })), "已装 · 1 项还没好");
  assert.equal(summary(plugin({ installed: true, checks: [check(true), check(null)] })), "已装 · 都好了");
});

test("第一次装要填读取工具目录和 python；已有配置不用填；装了就不能再装", () => {
  const empty = { vault_dir: "", python: "" }, filled = { vault_dir: "/r", python: "/p" };
  assert.equal(canInstall(plugin(), empty), false);
  assert.equal(canInstall(plugin(), { vault_dir: "/r", python: " " }), false);
  assert.equal(canInstall(plugin(), filled), true);
  assert.equal(canInstall(plugin({ needs_config: false }), empty), true);
  assert.equal(canInstall(plugin({ installed: true }), filled), false);
});
