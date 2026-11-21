// issue 详情里的小部件：结果行、审查进度、关联 PR、找不到的项目。（「交给 Agent」的说明见 NextStep.ts 的 flowNote）
// 不写 JSX，node --test 可以直接加载这个文件做渲染测试。
import { createElement as h } from "react";
import type { Issue, LinkedPrs, ReviewState, RunResult } from "./api";

/** 页面上的一句提示和它说的是哪一条（about 是 owner/repo#编号；列表上批量操作的提示是空串）。 */
export interface Note { text: string; about: string }
export const NO_NOTE: Note = { text: "", about: "" };
/** 详情里只显示说这一条的提示：别的 issue 的（比如先前在另一条详情里确认发送的「已发出 1 条回复」）和列表上的批量提示都不进来。 */
export const noteFor = (note: Note, subject: string) =>
  note.text && note.about && note.about.toLowerCase() === subject.toLowerCase() ? note.text : "";

// 自己仓库和上游仓库（scope 为 mine）的 issue 都能「交给 Agent」；离线快照没有写操作
export const canHandoff = (online: boolean, _issue?: Pick<Issue, "scope">) => online;

/** 一轮回执的结果行写成一句话：开了 PR 给链接，卡住了写原因，没写结果行说明为什么要人去看。还在跑的不显示。 */
export function RunResultLine({ result }: { result?: RunResult | null }) {
  if (!result) return null;
  const line = (tone: string, ...children: unknown[]) => h("p", { className: `result-line ${tone}`, role: "status" }, ...(children as []));
  if (result.kind === "pr") return line("ok", "结果：已开 PR ", h("a", { href: result.url, target: "_blank", rel: "noreferrer" }, `#${result.pr} ↗`), "，等你看。");
  if (result.kind === "no_change") return line("ok", "结果：没东西可改，没开 PR。");
  if (result.kind === "blocked") return line("bad", `结果：卡住了，${result.reason || "没写原因"}。`);
  if (result.kind === "failed") return line("bad", `结果：这一轮没跑完（${result.reason || "没说原因"}），要你看一下。`);
  if (result.kind === "review") return line(verdictTone[result.verdict ?? "WAIT"], `审查结论：${verdictName[result.verdict ?? "WAIT"]}。`);
  return line("warn", `结果不明：${result.reason || "回执没有结果行"}，要你看一下。`);
}

const verdictName: Record<string, string> = { APPROVE: "通过", CHANGES_REQUIRED: "要改", WAIT: "等待，稍后再审", STALE: "提交已更新，没审" };
const verdictTone: Record<string, string> = { APPROVE: "ok", CHANGES_REQUIRED: "bad", WAIT: "warn", STALE: "warn" };
const FINISHED = ["done", "nothing"];

/** 开了 PR 之后的审查进度：审查中、暂不审查的原因、结论、返工到第几轮；满了要本人处理。没审过不显示。
 * 结论只对审的那个提交有效：审完 PR 又有新提交就写「提交已更新，等重新审查」。知道审的提交就写出来。Agent 都不合并。 */
export function ReviewLine({ review }: { review?: ReviewState | null }) {
  if (!review) return null;
  const pr = review.url ? h("a", { href: review.url, target: "_blank", rel: "noreferrer" }, "PR ↗") : null;
  const line = (tone: string, text: string) => h("p", { className: `result-line ${tone}`, role: "status" }, text, pr && " ", pr);
  const rounds = `已返工 ${review.reworks} / ${review.max} 轮`;
  const at = review.sha ? `审的是 ${review.sha.slice(0, 7)}，` : "";
  if (review.active) return line("warn", `审查中（${at}${rounds}）。`);
  if (review.hold) return line(review.hold.startsWith("CI 没过") || review.hold.startsWith("返工") ? "bad" : "warn", `${review.hold}（${rounds}）。`);
  if (review.id !== null && !FINISHED.includes(review.state)) return line("warn", `这次审查没跑完（${at}${rounds}）；约两小时内还没有审查结论会提醒你。`);
  if (review.moved) return line("warn", `提交已更新，等重新审查（${review.sha ? `上次审的是 ${review.sha.slice(0, 7)}，` : ""}${rounds}）。`);
  if (!review.current) return line("", `上次审查：${verdictName[review.verdict ?? ""] ?? "结论不明"}；之后又处理过一轮，有新提交会重新审查（${rounds}）。`);
  if (review.verdict === "APPROVE") return line("ok", `审查通过（${at}${rounds}），可以合并；Agent 不合并，等你决定。`);
  if (review.verdict === "CHANGES_REQUIRED" && review.spent) return line("bad", `审查要改，返工第 ${review.reworks} 轮结束了却没推新提交，不会再自动返工，等你决定（${at}${rounds}）。`);
  if (review.verdict === "CHANGES_REQUIRED") return review.reworks < review.max
    ? line("bad", `审查要改（${at}${rounds}），转返工第 ${review.reworks + 1} 轮。`)
    : line("bad", `审查要改，已返工 ${review.max} 轮，不再自动返工，等你处理。`);
  if (review.verdict === "WAIT") return line("warn", `审查结论：等待，约一小时后再审（${at}${rounds}）。`);
  return line("warn", "审查回执没有结论行，要你看一下。");
}

const checks: Record<string, [string, string]> = {
  SUCCESS: ["CI 通过", "ok"], FAILURE: ["CI 失败", "bad"], ERROR: ["CI 出错", "bad"], PENDING: ["CI 运行中", "warn"], EXPECTED: ["CI 等待中", "warn"],
};
const reviews: Record<string, [string, string]> = {
  APPROVED: ["已批准", "ok"], CHANGES_REQUESTED: ["要改", "bad"], REVIEW_REQUIRED: ["待审", "warn"],
};
export const checkLabel = (state: string): [string, string] => checks[state] ?? ["无 CI 结果", ""];
export const reviewLabel = (decision: string): [string, string] => reviews[decision] ?? ["暂无审查结论", ""];
const tag = (text: string, tone = "") => h("span", { className: tone ? `tag ${tone}` : "tag" }, text);

/** issue 详情里的「关联 PR」：GitHub 上引用这条 issue 的 open PR，只读，点开去 GitHub。 */
export function LinkedPrList({ repo, result, failed }: { repo: string; result: LinkedPrs | null; failed: string }) {
  const prs = result?.prs ?? [];
  let body;
  if (failed) body = h("div", { className: "error", role: "alert" }, `关联 PR 没读到：${failed}`);
  else if (!result) body = h("p", { className: "meta" }, "正在读取…");
  else body = [
    result.limited && h("div", { key: "limited", className: "notice-line", role: "status" }, result.limited),
    result.error && h("div", { key: "error", className: "error", role: "alert" }, `关联 PR 没读到：${result.error}`),
    prs.length > 0 ? h("ol", { key: "list", className: "timeline" }, prs.map(pr => {
      const [ci, ciTone] = checkLabel(pr.checks);
      const [review, reviewTone] = reviewLabel(pr.review);
      const name = pr.repo && pr.repo !== repo ? `${pr.repo}#${pr.number}` : `#${pr.number}`;
      return h("li", { key: pr.url },
        h("div", null, h("a", { href: pr.url, target: "_blank", rel: "noreferrer" }, h("strong", null, `${name} ${pr.title}`), " ↗")),
        h("small", null, pr.author || "未知作者"),
        pr.draft && tag("草稿"), tag(ci, ciTone), tag(review, reviewTone));
    })) : !result.error && !result.limited && h("p", { key: "empty", className: "meta" }, "没有引用这条 issue 的 open PR。"),
  ];
  return h("section", { className: "detail-section" },
    h("h3", null, "关联 PR", result && h("small", null, prs.length)),
    h("p", { className: "meta" }, "GitHub 上引用这条 issue 的 open PR，只读；5 分钟更新一次。"),
    body);
}

/** #/p/<项目> 里的项目不在当前列表：说清楚找不到，给回收件箱的入口。 */
export function MissingProject({ id, back }: { id: string; back: () => void }) {
  return h("div", { className: "notice", role: "alert" },
    h("p", null, `${id ? `找不到项目「${id}」。` : "网址里没有项目。"}它不在项目登记表里，或近一个月没有你本人的活动（收件箱只列近月有活动的项目）。`),
    h("a", { href: "#/inbox", onClick: (e: { preventDefault: () => void }) => { e.preventDefault(); back(); } }, "← 回收件箱"));
}
