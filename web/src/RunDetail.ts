// 「运行记录」点开一轮的详情（抽屉里的正文）。数据来自 GET /api/autoloop/runs/<轮次>：只读 worker 的运行目录，正文和日志已在后端打码。
// 顺序：标题行（轮次、规则、对象、状态、开始、用时）→ 结论 → 回执 → 全文（默认折叠）→ 事件 → 失败时的退出码和 stderr。
// 不写 JSX，node --test 可以直接加载这个文件做渲染测试。
import { createElement as h, useState } from "react";
import type { ReactNode } from "react";
import Markdown from "./markdown.ts";
import { inboxHash } from "./InboxParts.ts";
import { spent, when } from "./RunParts.ts";

export interface RunAsk { title: string; text: string; options: string[]; recommend: number | null; card: number | null; open: boolean | null; tier: string }
export interface Receipt {
  error?: string;
  notice: { title: string; text: string }[];
  ask: RunAsk[];
  later: { at: string; about: string; key: string; cancel: boolean }[];
  resolve: { card: number | null; reason: string }[];
  events: { type: string; subject: string; summary: string }[];
}
export interface RunEvent { source: string; type: string; subject: string; summary: string; link: string; received_at: string; count: number }
export interface RunDetail {
  id: number; rule: string; agent: string; subjects: string[]; status: string; state: string; active: boolean; attempt: number;
  started_at: string | null; finished_at: string | null; error: string;
  format: string; delivered: boolean; truncated: boolean;
  conclusion: string; body: string; receipt: Receipt | null; outcome: { kind: string; value: string; line: string } | null;
  events: RunEvent[]; events_error: string; exit_code: number | null; stderr: string; folder_missing: boolean;
}

export const PR_RULE = "处理 PR 反馈";  // 它的对象是 PR
export const HANDLE_RULE = "处理 issue";  // 只有这条规则的提问能用 #/q/<编号> 直接定位到收件箱里的 issue
const SUBJECT = /^([\w.-]+)\/([\w.-]+)#([0-9]+)$/;

/** 运行详情的网址 #/runs/<轮次>；不是就是 null。 */
export function parseRunHash(hash: string): number | null {
  const m = hash.match(/^#\/runs\/([1-9][0-9]{0,8})$/);
  return m ? Number(m[1]) : null;
}
export const runHash = (id: number) => `#/runs/${id}`;

/** 对象（owner/repo#N）的两个去处：收件箱里的详情，GitHub 原页。PR 反馈的对象是 PR，收件箱里筛到那个仓库。 */
export function subjectLinks(subject: string, rule: string) {
  const m = subject.match(SUBJECT);
  if (!m) return null;
  const pr = rule === PR_RULE;
  return {
    inbox: pr ? inboxHash({ tab: "all", scope: `${m[1]}/${m[2]}` }) : `#/i/${m[1]}/${m[2]}/${m[3]}`,
    github: `https://github.com/${m[1]}/${m[2]}/${pr ? "pull" : "issues"}/${m[3]}`,
  };
}

/** 回答提问的去处：「处理 issue」的提问用 #/q/<编号> 打开它所属的 issue；别的规则打开对象在收件箱里的详情（回答在详情里）。 */
export function askHref(ask: RunAsk, run: Pick<RunDetail, "rule" | "subjects">) {
  if (ask.card && run.rule === HANDLE_RULE) return `#/q/${ask.card}`;
  const first = run.subjects.map(s => subjectLinks(s, run.rule)).find(Boolean);
  return first ? first.inbox : inboxHash();
}

/** 用时：结束了是开始到结束，还在跑是开始到现在（秒）。 */
export function elapsed(run: Pick<RunDetail, "started_at" | "finished_at" | "active">, now = Date.now()) {
  if (!run.started_at) return null;
  const end = run.finished_at && !run.active ? Date.parse(run.finished_at) : now;
  return Math.max(0, Math.round((end - Date.parse(run.started_at)) / 1000));
}

/** 结果行写成中文；认不出的照原样。 */
export function outcomeText(outcome: RunDetail["outcome"]) {
  if (!outcome) return "";
  const v = outcome.value;
  if (outcome.kind === "VERDICT") return ({ APPROVE: "审查通过", CHANGES_REQUIRED: "要求修改", WAIT: "等一等再审", STALE: "PR 又有新提交，这一轮没审" } as Record<string, string>)[v] ?? v;
  const pr = v.match(/^PR=([0-9]+)$/);
  if (pr) return `开了 PR #${pr[1]}`;
  const blocked = v.match(/^BLOCKED=(.*)$/);
  if (blocked) return `卡住：${blocked[1]}`;
  return ({ NO_CHANGE: "不用改", REPLIED: "已写回复草稿" } as Record<string, string>)[v] ?? v;
}

export const statusTone = (status: string) => status === "完成" ? "ok" : status === "失败" || status === "没跑成" ? "bad" : "warn";

function laterLine(l: Receipt["later"][number]) {
  if (l.cancel) return `取消复查${l.key ? `：${l.key}` : ""}`;
  return `${l.at ? when(l.at) : "—"} 复查：${l.about || l.key}`;
}

const block = (title: string, children: ReactNode, extra?: ReactNode) =>
  h("div", { className: "receipt-block" }, h("h4", null, title, extra), children);

function ReceiptView({ run }: { run: RunDetail }) {
  const r = run.receipt;
  const result = run.outcome && h("p", { className: "receipt-result" }, "结果：", h("strong", null, outcomeText(run.outcome)), h("code", null, run.outcome.line));
  if (!r && !result) return null;
  const parts: ReactNode[] = [];
  if (r?.error) parts.push(h("p", { key: "e", className: "run-error" }, r.error));
  if (r && !r.error) {
    if (r.notice.length) parts.push(block("通知", r.notice.map((n, i) => h("div", { key: i, className: "receipt-item" }, h("strong", null, n.title), n.text && h("p", null, n.text))), undefined));
    if (r.ask.length) parts.push(block("提问", r.ask.map((a, i) => h("div", { key: i, className: "receipt-item" },
      h("strong", null, a.title, a.tier === "default" && h("span", { className: "tag" }, "已按推荐做")),
      a.text && h("p", null, a.text),
      a.options.length > 0 && h("ol", { className: "receipt-options", start: 0 }, a.options.map((o, n) =>
        h("li", { key: n, className: n === a.recommend ? "recommended" : undefined }, o, n === a.recommend && h("span", { className: "tag ok" }, "推荐")))),
      a.open === true ? h("p", { className: "receipt-answer" }, h("span", { className: "tag warn" }, "等你回答"), h("a", { href: askHref(a, run) }, "去收件箱回答 →"))
        : a.open === false ? h("p", { className: "meta" }, "已回答或已关闭") : null))));
    if (r.later.length) parts.push(block("复查", h("ul", null, r.later.map((l, i) => h("li", { key: i }, laterLine(l))))));
    if (r.resolve.length) parts.push(block("收尾", h("ul", null, r.resolve.map((x, i) => h("li", { key: i }, `${x.card ? `提醒 #${x.card}` : "提醒"}：${x.reason || "已解决"}`)))));
    if (r.events.length) parts.push(block("派出的事件", h("ul", null, r.events.map((e, i) => h("li", { key: i }, h("strong", null, e.subject || e.type), e.summary && ` ${e.summary}`))), h("small", null, r.events.length)));
  }
  return h("section", { className: "detail-section" }, h("h3", null, "回执"), result, ...parts.map((p, i) => h("div", { key: i }, p)));
}

function Folded({ label, children }: { label: [string, string]; children?: ReactNode }) {
  const [open, setOpen] = useState(false);
  return h("div", { className: "folded" },
    h("button", { className: "text-button", "aria-expanded": open, onClick: () => setOpen(!open) }, open ? label[1] : label[0]),
    open && children);
}

/** 抽屉正文。repo 给 Markdown 补 #编号 链接用。 */
export function RunDetailView({ run, now = Date.now() }: { run: RunDetail; now?: number }) {
  const seconds = elapsed(run, now);
  const repo = run.subjects[0]?.match(SUBJECT);
  const repoName = repo ? `${repo[1]}/${repo[2]}` : undefined;
  const failed = run.status === "失败" || run.status === "没跑成" || (run.exit_code !== null && run.exit_code !== 0);
  return h("div", { className: "run-detail" },
    h("div", { className: "eyebrow" }, `第 ${run.id} 轮${run.attempt > 1 ? ` · 第 ${run.attempt} 次执行` : ""}`),
    h("h2", null, run.rule || "（规则未知）"),
    run.subjects.length > 0 && h("div", { className: "run-subjects" }, run.subjects.map(s => {
      const links = subjectLinks(s, run.rule);
      return h("span", { key: s }, links ? h("a", { href: links.inbox }, s) : s,
        links && h("a", { className: "source-link inline", href: links.github, target: "_blank", rel: "noreferrer" }, "在 GitHub 打开 ↗"));
    })),
    h("p", { className: "run-facts" }, h("span", { className: `tag ${statusTone(run.status)}` }, run.status),
      h("span", null, `开始 ${when(run.started_at)}`),
      h("span", null, run.active ? `已运行 ${spent(seconds)}` : `用时 ${spent(seconds)}`)),
    h("section", { className: "detail-section" }, h("h3", null, "结论"),
      run.conclusion ? h(Markdown, { text: run.conclusion, repo: repoName })
        : h("p", { className: "meta" }, run.active ? "还在跑，跑完后显示结论。" : run.error ? `原因：${run.error}` : "没有结论。")),
    h(ReceiptView, { run }),
    run.body && h("section", { className: "detail-section" }, h("h3", null, "全文", run.truncated && h("small", null, "只读了输出的最后 1 MB")),
      h(Folded, { label: ["展开全文", "收起全文"] }, h(Markdown, { text: run.body, repo: repoName }))),
    h("section", { className: "detail-section" }, h("h3", null, "事件", run.events.length > 0 && h("small", null, run.events.reduce((n, e) => n + (e.count || 1), 0))),
      run.events.length ? h("ol", { className: "timeline" }, run.events.map((e, i) => h("li", { key: i },
        h("div", null, h("strong", null, e.subject || e.type || "—", e.count > 1 && ` × ${e.count}`), h("time", null, [e.source, e.subject ? e.type : ""].filter(Boolean).join(" · "))),
        e.summary && h("p", null, e.summary))))
        : h("p", { className: "meta" }, run.events_error || "这一轮没有事件（定时运行）。")),
    failed && h("section", { className: "detail-section" }, h("h3", null, "失败"),
      h("p", null, run.exit_code !== null ? `退出码 ${run.exit_code}` : "没有退出码（进程没跑完或被停止）"),
      run.stderr ? h(Folded, { label: ["展开 stderr 最后 30 行", "收起 stderr"] }, h("pre", { className: "mono-log" }, run.stderr))
        : run.error && run.error !== run.conclusion ? h(Folded, { label: ["展开中台记下的原因", "收起原因"] }, h("pre", { className: "mono-log" }, run.error))
        : h("p", { className: "meta" }, "stderr 是空的。")),
    run.folder_missing && h("p", { className: "meta" }, "运行目录已清理（worker 留 14 天），只剩中台记下的结论。"));
}
