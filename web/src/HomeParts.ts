// 项目详情里的 issue 入口。不写 JSX，node --test 可以直接加载这个文件做渲染测试。
import { createElement as h } from "react";

/** 项目详情里的一行入口：这个项目 open 和近 30 天已完成的 issue 数，点「查看」去收件箱（全部）并筛到这个项目的仓库。
 * 数还没读到时是 null。 */
export function ProjectIssueEntry({ open, done, href, go }: { open: number | null; done: number | null; href: string; go: () => void }) {
  return h("p", { className: "project-issues" }, "这个项目的 issue：",
    h("b", null, `open ${open ?? "…"}`), " · ", h("b", null, `已完成 ${done ?? "…"}`), " ",
    h("a", { href, onClick: (e: { preventDefault: () => void }) => { e.preventDefault(); go(); } }, "→ 查看"));
}
