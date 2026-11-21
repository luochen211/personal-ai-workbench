// 收件箱里的 issue 按「这条 issue 的下一步该谁做」归类（后端 personal/next_step.py）：标签和按钮（归类的依据在标签的悬停提示里）。
// 不写 JSX，node --test 可以直接加载这个文件做渲染测试。
import type { Issue, NextStep, ReplyMode } from "./api";
import { handoffButton } from "./handoff.ts";  // 带扩展名：node --test 直接加载

// 页面上标签的顺序：先是要你动手的，最后是 Agent 手里的
export const CLASSES: [NextStep["key"], string][] = [
  ["reply", "待你回"], ["decide", "待你定"], ["todo", "待动手"], ["wait", "等对方回"], ["pr", "PR 评审中"], ["close", "待关闭"], ["look", "待你看"],
  ["seen", "Agent 看过"], ["hold", "搁置"], ["record", "记录"], ["flow", "Agent 流程中"],
];
const tones: Record<string, string> = { reply: "bad", decide: "bad", todo: "warn", wait: "wait", pr: "ok", close: "", look: "look", seen: "look", hold: "", record: "", flow: "ok" };
export const classTone = (key: string) => tones[key] ?? "";

type Row = Pick<Issue, "stage" | "stalled" | "next">;
// force：Agent 上一轮已有结论、之后没有新动静（待你定、Agent 看过）时的「仍交给 Agent」，交给接口要带它才收
export interface NextButton { text: string; disabled: boolean; primary: boolean; drawerOnly: boolean; mode?: ReplyMode; force?: boolean }

/** 这一行的按钮：Agent 流程中沿用原来的「已交给 / 再交给 Agent」（issue 留言流程写「帮我回复 / 再帮我回复」）；
 * 其余按后端给的（交给 Agent、帮我回复、仍交给 Agent），没有就是 null。「仍交给 Agent」只在详情里出。
 * 待你定、Agent 看过（next.settled）只有详情里一个「仍交给 Agent」，带 force。 */
export function nextButton(i: Row): NextButton | null {
  if (i.next.settled) return { text: i.next.button || "仍交给 Agent", disabled: false, primary: false, drawerOnly: true, force: true };
  if (i.next.key === "flow") {
    const b = handoffButton(i);
    return { ...b, text: i.next.action === "feedback" ? b.text.replace("交给 Agent", "帮我回复") : b.text, drawerOnly: false };
  }
  if (!i.next.button) return null;
  return { text: i.next.button, disabled: false, primary: !i.next.drawer_only, drawerOnly: i.next.drawer_only };
}

/** 详情里的按钮：走 issue 留言流程的（待你回，不论哪个仓库；再交一轮）两个并列——「帮我回复」只写回复草稿，「处理并回复」改完再写草稿，
 *  建议（hint）的那个是主按钮；其余照 nextButton 一个。已交给、在派的那一个禁用按钮照旧。 */
export function nextButtons(i: Row & Partial<Pick<Issue, "hint">>): NextButton[] {
  const b = nextButton(i);
  if (!b) return [];
  if (i.next.action !== "feedback" || b.disabled || b.force) return [b];
  const pick = i.hint?.mode ?? "reply";
  return [{ ...b, primary: pick === "reply", mode: "reply" },
    { ...b, text: b.text.replace("帮我回复", "处理并回复"), primary: pick === "handle", mode: "handle" }];
}

const flows: Record<string, string> = {
  issue: "打 agent 标签并马上派给中台，「处理 issue」在新工作区改好、开 PR，不合并。",
  upstream: "不打标签，马上派给中台：Agent 先查有没有人已经在做，再 fork 到你的账号、改好后向上游开 PR。",
  push: "不打标签，马上派给中台：你有这个仓库的推送权限，「处理 issue」在原仓库新开分支改好、开 PR，不 fork、不合并。",
  feedback: "「帮我回复」只写回复草稿，不改代码、不推送；「处理并回复」先改代码开 PR（上游仓库先 fork 到你的账号，都不合并），再写回复草稿。",
};
const without: Record<string, string> = {
  pr: "没有按钮：下一步在 PR 评审，PR 的回复和返工在收件箱里那条 PR 上交给 Agent。",
  close: "没有按钮：已修好，等验收关闭。",
  wait: "没有按钮：下一步在对方，对方回了这条转「待你回」。",
  hold: "没有按钮：这条在搁置，项目恢复或有了新动静再看。",
  record: "没有按钮：这条是记录（总表、跟踪、调研一类），不是一件活。",
};

/** 「帮我回复」那一轮说要改代码（issue.switch）：详情里的一键「改用处理并回复」（有草稿时在草稿里出，这里不再出）。 */
export const switchButton = (i: Pick<Issue, "switch">, drafted = false): NextButton | null =>
  i.switch && !drafted ? { text: "改用处理并回复", disabled: false, primary: true, drawerOnly: true, mode: "handle" } : null;

const settledNote = (i: Pick<Issue, "next">) => {
  const s = i.next.settled!;
  return s.kind === "decide"
    ? `Agent 第 ${s.run} 轮的结论是要你定，之后没有新的留言、评审或提交，所以列表上不出交给按钮、批量交也会跳过。回答它的提问或在 GitHub 上回复后会放开；确实要再派一轮点「仍交给 Agent」。`
    : `Agent 第 ${s.run} 轮已经看过、结论是不用动，之后没有新的留言、评审或提交，所以列表上不出交给按钮、批量交也会跳过。有人回复、评审或提交后会放开；确实要再派一轮点「仍交给 Agent」。`;
};

/** 按钮点了走哪条流程（Agent 流程中的不写，沿用原来的说明）；没有按钮的写为什么没有；待你定、Agent 看过写为什么不再交。 */
export function flowNote(i: Pick<Issue, "next" | "scope" | "push">) {
  if (i.next.settled) return settledNote(i);
  if (i.next.key === "flow") return "";
  if (!i.next.button) return without[i.next.key] ?? "";
  if (i.next.action === "feedback") return flows.feedback;
  if (i.scope !== "mine") return flows.issue;
  return i.push ? flows.push : flows.upstream;
}
