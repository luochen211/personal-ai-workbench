// 决定包：一条 issue 上 Agent 还没答的提问放在一屏里一起答，默认选中推荐项（思路借自 ai-daming/daming-skills 的 issue-pr-pipeline，MIT）。
// 【问你】等你定；【默认过】Agent 已按推荐做了，不改就算定了。只有点送出按钮才送出：不自动聚焦、不包在 form 里，
// 回车、空格、后台刷新都不会把整包按推荐送出。回答按顺序逐条交给中台，可能分成同一个 PR 上的几轮处理。
// 不写 JSX，node --test 可以直接加载这个文件做渲染测试。
import { createElement as h, useState } from "react";
import { request } from "./api.ts";  // 带扩展名：node --test 直接加载
import type { Issue, Question } from "./api";
import Markdown from "./markdown.ts";

export interface Choice { choice: number | null; text: string }
export interface Answer { id: number; choice?: number; text?: string }

/** 包里的提问：只要 ask，【问你】在前，同档按提出的先后。 */
export function packageOf(waiting: Question[]) {
  const rank = (q: Question) => q.tier === "default" ? 1 : 0;
  return waiting.filter(q => q.kind === "ask").sort((a, b) => rank(a) - rank(b) || a.created_at.localeCompare(b.created_at) || a.id - b.id);
}

/** 一问当前的选择：没动过的就是推荐项；没写推荐的空着，要人自己选。 */
export const pickOf = (q: Question, picks: Record<number, Choice>): Choice => picks[q.id] ?? { choice: q.recommend ?? null, text: "" };

/** 要送出的回答：写了别的想法就送这段话（不按选项）；否则送选中的选项（从 1 起）。没选也没写的返回 missing。 */
export function answersOf(asks: Question[], picks: Record<number, Choice>) {
  const answers: Answer[] = [], missing: Question[] = [];
  for (const q of asks) {
    const pick = pickOf(q, picks);
    if (pick.text.trim()) answers.push({ id: q.id, text: pick.text.trim() });
    else if (pick.choice !== null) answers.push({ id: q.id, choice: pick.choice + 1 });
    else missing.push(q);
  }
  return { answers, missing };
}

/** 全部还是推荐项、没写别的想法。 */
/** 「别的想法」输入框里按回车：不送出，跳到下一问的输入框（最后一问就留在原处）。输入法选词时的回车不管。 */
export function noteKeyDown(event: { key: string; nativeEvent?: { isComposing?: boolean }; preventDefault: () => void; currentTarget: HTMLElement }) {
  if (event.key !== "Enter" || event.nativeEvent?.isComposing) return;
  event.preventDefault();
  const notes = Array.from(event.currentTarget.closest(".decisions")?.querySelectorAll<HTMLElement>(".decision-note") ?? []);
  notes[notes.indexOf(event.currentTarget) + 1]?.focus();
}

export const allRecommended = (asks: Question[], picks: Record<number, Choice>) =>
  asks.every(q => q.recommend !== null && pickOf(q, picks).choice === q.recommend && !pickOf(q, picks).text.trim());

const TIER_LABEL: Record<string, [string, string]> = { ask: ["问你", "bad"], default: ["默认过", "warn"] };

export function DecisionPackage({ issue, done, refresh }: {
  issue: Pick<Issue, "repo" | "number" | "waiting">; done: (message: string) => void; refresh?: () => void;
}) {
  const asks = packageOf(issue.waiting);
  const [picks, setPicks] = useState<Record<number, Choice>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!asks.length) return null;
  const set = (q: Question, change: Partial<Choice>) => setPicks(p => ({ ...p, [q.id]: { ...pickOf(q, p), ...change } }));
  const { answers, missing } = answersOf(asks, picks);
  const asking = asks.filter(q => q.tier !== "default").length;
  const submit = async () => {
    if (busy) return;
    if (missing.length) { setError(`还有 ${missing.length} 条没选：${missing.map(q => q.title).join("；")}`); return; }
    setBusy(true); setError("");
    try { done((await request<{ message: string }>(`/api/issues/${issue.repo}/${issue.number}/decisions`, { answers })).message || "已回答"); }
    catch (e) {
      // 可能已送出一部分：刷新后答过的消失，剩下的留在这里可以再送
      setError(e instanceof Error ? e.message : "回答没送出");
      refresh?.();
    }
    finally { setBusy(false); }
  };
  const item = (q: Question) => {
    const [word, tone] = TIER_LABEL[q.tier || "ask"] ?? TIER_LABEL.ask;
    const pick = pickOf(q, picks);
    return h("fieldset", { key: q.id, className: `decision ${q.tier || "ask"}` },
      h("legend", null, h("span", { className: `tag ${tone}` }, word), q.title),
      q.text ? h(Markdown, { text: q.text }) : null,
      h("div", { className: "answer-options", role: "radiogroup", "aria-label": q.title }, q.options.map((option, i) =>
        h("button", { key: option, type: "button", role: "radio", "aria-checked": pick.choice === i && !pick.text.trim(),
          className: pick.choice === i && !pick.text.trim() ? "primary" : "", disabled: busy, onClick: () => set(q, { choice: i, text: "" }) },
          option, q.recommend === i ? "（推荐）" : ""))),
      h("input", { className: "decision-note", "aria-label": `${q.title}：别的想法`, placeholder: "别的想法（写了就不按选项）…", value: pick.text,
        disabled: busy, onKeyDown: noteKeyDown, onChange: (e: { target: { value: string } }) => set(q, { text: e.target.value }) }));
  };
  return h("section", { className: "waiting decisions", "aria-label": "要你定的事" },
    h("strong", null, `要你定的事 ${asks.length} 条`, asking < asks.length ? `（问你 ${asking} 条，默认过 ${asks.length - asking} 条）` : ""),
    h("p", { className: "meta" }, asking < asks.length ? "「默认过」的 Agent 已按推荐做了，不改就算定了。" : "", "已默认选中推荐项，点下面的按钮才送出。回答按顺序逐条交给中台，可能分成同一个 PR 上的几轮处理，Agent 每轮按拿到的那几条接着做。"),
    asks.map(item),
    h("div", { className: "decision-actions" },
      h("button", { type: "button", className: "primary", disabled: busy, onClick: () => void submit() },
        busy ? "送出中…" : allRecommended(asks, picks) ? "全部按推荐送出" : `送出 ${answers.length} 条回答`)),
    error ? h("div", { className: "error", role: "alert" }, error) : null);
}
