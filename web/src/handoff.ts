import type { Issue } from "./api";

// 「交给 Agent」按钮：未交给就交给；刚交给、正在处理时禁用；只有标签没有运行记录（启用监看前就带标签的 issue
// 不会自动派出），或显式交给后超过 15 分钟还没有新的一轮（后端标 stalled），放开按钮让人再交一次。
export const moving = ["已交给 Agent", "处理中", "审查中", "返工中"];
type Row = Pick<Issue, "stage" | "stalled">;

export const stuck = (i: Row) => i.stage === "已交给 Agent" && !!i.stalled;
export const waiting = (i: Row) => moving.includes(i.stage) && !stuck(i);

export function handoffButton(i: Row) {
  if (i.stage === "未交给 Agent") return { text: "交给 Agent", disabled: false, primary: true };
  if (waiting(i)) return { text: "已交给", disabled: true, primary: false };
  return { text: "再交给 Agent", disabled: false, primary: stuck(i) };
}

export function stallNote(i: Row, label: string) {
  if (i.stage !== "已交给 Agent") return "";
  if (i.stalled === "label_only") return `只有 ${label} 标签，还没有运行记录：启用监看前就带标签的 issue 不会自动派出，点「再交给 Agent」显式交给。`;
  if (i.stalled === "lost") return "交给后超过 15 分钟还没有新的一轮：派发可能丢了，可重试。";
  return "";
}
