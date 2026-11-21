// 收件箱：通知、你开的 PR、登记项目的 open issue 合成一行（按 owner/repo#编号 去重，issue 合并后的类别优先）；
// 筛选和左列条数、+N 口径、重复的 PR 只留一条、角标数、原消息页漏掉的几条、哪些行能交给 Agent、超过 10 条先确认、
// 范围（某个仓库）、每行那一句不重复类别、CI 只在收件箱、每行那一句话、排序、相对时间、网址（含旧的消息、Issue、待你处理页）
import { test } from "node:test";
import assert from "node:assert/strict";
import { CONFIRM_OVER, TABS, agentRows, ago, clock, confirmText, doneInScope, handVia, inScope, inTab, inboxHash, issueBucket, legacyInboxHash, mergeThreads,
  needsConfirm, parseInboxHash, projectOfRepo, replyCount, replyHint, repoCounts, rowLine, rowState, searchText, sideCounts, sortThreads, statusLine,
  withIssues } from "../src/InboxParts.ts";

const ME = "luochen211";
const message = (id, extra = {}) => ({ id, repository: "o/app", title: `t${id}`, type: "Issue", category: "", reason: "提到你",
  reason_code: "mention", unread: true, updated_at: "2026-11-18T01:00:00Z", url: `https://github.com/o/app/issues/${id}`, state: "open", done: false,
  classification: "workspace", project_id: "app", project_name: "App", candidates: [], number: Number(id), ...extra });
const row = (subject, extra = {}) => ({ subject, kind: "pr", repo: subject.split("#")[0], number: Number(subject.split("#")[1]), title: subject,
  url: `https://github.com/${subject.replace("#", "/pull/")}`, project_id: "orders", project_name: "订单", stage: "未交给 Agent", stalled: "",
  handed_at: null, section: "active", open: true, pending: true, new: 2, their_at: "2026-11-17T00:00:00Z", mine_at: "", said: [], waiting: [],
  run: null, history: [], unseen: false, by_me: false, author: ME, state: "reply", ...extra });
const prs = rows => ({ prs: rows, login: ME });
const NAMES = { reply: "待你回", todo: "待动手", wait: "等对方回", pr: "PR 评审中", close: "待关闭", look: "待你看", hold: "搁置", record: "记录", flow: "Agent 流程中" };
// issue 那一行（personal/issues.py）：key 是细类，bucket 不给时页面照 next_step.bucket 推；按钮照 next_step 给：待动手「交给 Agent」、待你看只在详情里「仍交给 Agent」
const issue = (subject, key, extra = {}, next = {}) => ({ subject, repo: subject.split("#")[0], number: Number(subject.split("#")[1]), project_id: "app",
  project_name: "App", title: `issue ${subject}`, url: `https://github.com/${subject.replace("#", "/issues/")}`, labels: [], author: "boss", comments: 0,
  created_at: "2026-11-01T00:00:00Z", updated_at: "2026-11-16T00:00:00Z", scope: "", stage: key === "flow" ? "处理中" : "未交给 Agent", stalled: "",
  handed_at: null, history: [], waiting: [], run: null,
  next: { key, name: NAMES[key], line: `${NAMES[key]}那一句`, next: "", why: "", who: "", at: "", quote: "", url: "", prs: [], action: "",
    drawer_only: key === "look", button: { todo: "交给 Agent", look: "仍交给 Agent" }[key] ?? "", ...next }, ...extra });
const unify = (messages, { p = null, issues = [] } = {}) => withIssues(mergeThreads(messages, { prs: p }), issues, { login: ME });

test("rows follow who spoke last; an older backend without state is worked out the same way", () => {
  assert.equal(rowState(row("a#1", { state: undefined })), "reply");  // 对方最后说话
  assert.equal(rowState(row("a#1", { state: undefined, stage: "处理中" })), "agent");
  assert.equal(rowState(row("a#1", { state: undefined, stage: "已交给 Agent", handed_at: "2026-11-18T00:00:00Z" })), "agent");
  assert.equal(rowState(row("a#1", { state: undefined, stage: "已交给 Agent", stalled: "lost" })), "reply");  // 派发可能丢了
  assert.equal(rowState(row("a#1", { state: undefined, stage: "等你决定" })), "reply");
  assert.equal(rowState(row("a#1", { state: undefined, section: "recent", pending: false })), "wait");  // 你最后说话
  assert.equal(rowState(row("a#1", { state: undefined, section: "recent", open: false })), "done");
  assert.equal(rowState(row("a#1", { state: "wait", section: "active" })), "wait");  // 后端给了就用后端的
});

test("a PR listed twice stays once and hands off through the PR route", () => {
  const rows = agentRows(prs([row("o/orders#7", { kind: undefined }), row("O/Orders#7", { kind: undefined }), row("up/x#3", { kind: undefined, state: "wait" })]));
  assert.deepEqual(rows.map(r => [r.subject, r.kind, r.route]), [["o/orders#7", "pr", "prs"], ["up/x#3", "pr", "prs"]]);
  assert.equal(mergeThreads([], { prs: prs([row("o/orders#7"), row("o/orders#7")]) }).length, 1);
});

test("the nav badge counts the rows waiting on you, once each, issues by their next-step class", () => {
  const p = prs([row("o/orders#1"), row("o/orders#2", { state: "agent", stage: "处理中" }), row("o/orders#3", { state: "wait" }), row("up/x#3")]);
  assert.equal(replyCount(unify([], { p })), 2);  // o/orders#1、up/x#3
  // issue 的类别优先：o/orders#1 那一行是 issue、等对方回，不算；o/app#9 是待你回，算
  assert.equal(replyCount(unify([], { p, issues: [issue("o/orders#1", "wait"), issue("o/app#9", "reply")] })), 2);
  assert.equal(replyCount([]), 0);
});

test("+N only counts what they added after your last action, and only where it is on you or the Agent", () => {
  const [one, two, three, four] = mergeThreads([message("1", { new: 5, type: "PullRequest", url: "https://github.com/o/app/pull/1" }), message("2", { new: 4 })], {
    prs: prs([row("o/app#1", { new: 2 }), row("o/orders#3", { state: "wait", new: 0 }), row("o/orders#4", { state: "agent", stage: "处理中", new: 1 })]) });
  assert.equal(one.newCount, 2);  // 不是 GitHub 上次读过之后的 5
  assert.equal(two.newCount, null);  // 没有谁最后说话的数据：算不出就不写
  assert.equal(three.newCount, null);
  assert.equal(four.newCount, 1);
  assert.equal(two.status, null);
});

test("tabs: state rows sit in their tab, plain notifications and CI only under 全部, unread mode skips archived ones", () => {
  const rows = mergeThreads([message("1"), message("9", { type: "CheckSuite", url: "https://github.com/o/app", number: null }),
    message("5", { classification: "archived" }), message("6", { unread: false })],
  { prs: prs([row("o/orders#2"), row("o/orders#3", { state: "wait" }), row("o/orders#4", { state: "agent" }), row("o/orders#5", { state: "done", open: false })]) });
  const ids = (tab, read) => rows.filter(t => inTab(t, tab, read)).map(t => t.key);
  assert.deepEqual(ids("reply"), ["o/orders#2"]);
  assert.deepEqual(ids("wait"), []);  // 你开的 PR 在等对方：只在「我发的 PR」里
  assert.deepEqual(ids("mine"), ["o/orders#2", "o/orders#3", "o/orders#4"]);
  assert.deepEqual(ids("agent"), ["o/orders#4"]);
  assert.equal(ids("all").length, 8);  // 已合并、CI 也在全部里
  assert.deepEqual(ids("all", "unread"), ["o/app#1", "n:9"]);
  assert.deepEqual(TABS.map(t => t.label), ["收件箱", "待你回", "草稿待确认", "待动手", "Agent 处理中", "等对方", "待你看", "搁置", "记录", "我发的 PR"]);
  assert.equal(TABS[1].empty, "没有等你回的");
});

test("a repository scope is that repository, case aside; no other scope names are recognised", () => {
  const rows = mergeThreads([message("1", { repository: "team/kb", url: "https://github.com/team/kb/issues/1" }), message("2")]);
  assert.deepEqual(rows.filter(t => inScope(t, "O/App")).map(t => t.key), ["o/app#2"]);
  assert.deepEqual(rows.filter(t => inScope(t, "team/kb")).map(t => t.key), ["team/kb#1"]);
  assert.equal(rows.filter(t => inScope(t, "")).length, 2);
  assert.equal(rows.filter(t => inScope(t, "team")).length, 0);
});

test("each row says in one line who spoke last and where the Agent is", () => {
  const now = new Date(2026, 10, 18, 15, 0).getTime();
  const at = (d, h, m = 0) => new Date(2026, 10, d, h, m).toISOString();
  const line = (r, messages = []) => statusLine(mergeThreads(messages, { prs: prs([r]) })[0], now);
  assert.equal(line(row("o/orders#1", { said: [{ who: "alice", what: "评论", at: at(18, 10, 41), new: true }] })), "alice 10:41 评论 · 你还没回");
  assert.equal(line(row("o/orders#7", { state: "wait", their_at: "", mine_at: at(18, 9) })), "你开的 PR · 还没人回");
  assert.equal(line(row("o/orders#7", { state: "wait", their_at: at(16, 9), mine_at: at(17, 9, 4) })), "你开的 PR · 你 11-17 09:04 回过");
  assert.equal(line(row("o/orders#4", { state: "wait", mine_at: at(18, 10, 41), their_at: "2026-11-01T00:00:00Z", stage: "已处理",
    run: { result: { kind: "pushed", commit: "abc1234" } } })), "你开的 PR · 你 10:41 回过 · Agent 已推送");
  assert.equal(line(row("o/orders#21", { state: "wait", their_at: at(16, 10), mine_at: at(15, 9), stage: "已处理",
    said: [{ who: "boss", what: "评论", at: at(16, 10) }], run: { result: { kind: "no_action", reason: "可以收" } } })), "你开的 PR · boss 11-16 10:00 评论 · Agent：不用动");
  assert.equal(line(row("o/orders#2", { state: "agent", stage: "处理中" })), "Agent 正在处理");
  assert.equal(line(row("o/orders#2", { state: "reply", stage: "已交给 Agent", stalled: "lost" })), "交给后 15 分钟没动静，派发可能丢了");
  assert.equal(line(row("o/orders#2", { stage: "等你决定", waiting: [{ kind: "ask" }] })), "Agent 有问题等你决定");
  // issue：等对方并进来的几类，区别写在那一句里；Agent 等你决定的写它问了什么
  const one = (i) => statusLine(unify([], { issues: [i] })[0], now);
  assert.equal(one(issue("o/app#80", "pr", {}, { line: "PR #80 开着，等评审" })), "PR #80 开着，等评审");
  assert.equal(one(issue("o/app#81", "close", {}, { line: "你 11-18 10:00 写了请验收关闭" })), "你 11-18 10:00 写了请验收关闭");
  assert.equal(one(issue("o/app#163", "flow", { stage: "等你决定", waiting: [{ id: 1, kind: "ask", tier: "ask", title: "【问你】用哪个方案" }] })), "Agent 问你：用哪个方案");
  assert.equal(one(issue("o/app#164", "flow", { stage: "等你看" })), "Agent 交回结果，等你看");
  assert.equal(one(issue("o/app#165", "flow", { stage: "处理中" })), "Agent 流程中那一句");
  assert.equal(statusLine(mergeThreads([message("1")])[0], now), "");  // 没有处理情况的通知不写
});

test("left column: filter counts follow the scope, repository counts follow the filter, a picked empty repository stays", () => {
  const rows = mergeThreads([message("1"), message("7", { unread: false, repository: "z/lib", url: "https://github.com/z/lib/issues/7" })],
    { prs: prs([row("o/orders#2"), row("o/orders#3", { state: "wait" }), row("o/app#4")]) });
  const all = sideCounts(rows, { tab: "reply", scope: "" });
  assert.deepEqual(all.tabs, { all: 5, reply: 2, draft: 0, todo: 0, agent: 0, wait: 0, look: 0, hold: 0, record: 0, mine: 3 });
  assert.deepEqual(all.repos.map(r => [r.repo, r.count]).sort(), [["o/app", 1], ["o/orders", 1]]);
  const repo = sideCounts(rows, { tab: "mine", scope: "O/App" });
  assert.deepEqual(repo.tabs, { all: 2, reply: 1, draft: 0, todo: 0, agent: 0, wait: 0, look: 0, hold: 0, record: 0, mine: 1 });
  assert.deepEqual(repo.repos.map(r => [r.repo, r.count]), [["o/app", 1], ["o/orders", 2]]);  // 同一时刻按名字排
  assert.equal(sideCounts(rows, { tab: "all", scope: "", read: "unread" }).tabs.all, 1);  // 收件箱看未读：只算未读的通知
});

test("repositories are counted, newest first; search covers title, repo and number; sorting by time", () => {
  const rows = mergeThreads([message("1", { updated_at: "2026-11-10T00:00:00Z" }),
    message("2", { repository: "z/new", url: "https://github.com/z/new/issues/2", updated_at: "2026-11-18T00:00:00Z" }), message("3")]);
  assert.deepEqual(repoCounts(rows).map(r => [r.repo, r.count]), [["o/app", 2], ["z/new", 1]]);
  assert.ok(searchText(rows[1]).includes("#2") && searchText(rows[1]).includes("z/new"));
  assert.deepEqual(sortThreads(rows, "oldest").map(t => t.key), ["o/app#1", "z/new#2", "o/app#3"]);
  assert.deepEqual(sortThreads(rows, "newest").map(t => t.key), ["o/app#3", "z/new#2", "o/app#1"]);
  const late = mergeThreads([message("1", { updated_at: "2026-11-10T00:00:00Z" })], { prs: prs([row("o/app#1", { their_at: "2026-11-17T00:00:00Z" })]) });
  assert.equal(late[0].updated_at, "2026-11-17T00:00:00Z");  // 行的时间取最近一次有人说话
});

test("times read relative; the one-line clock writes today's time alone", () => {
  const now = new Date(2026, 10, 18, 15, 0).getTime();
  const at = (d, h = 10) => new Date(2026, 10, d, h).toISOString();
  assert.equal(ago(new Date(now - 30000).toISOString(), now), "刚刚");
  assert.equal(ago(new Date(now - 5 * 60000).toISOString(), now), "5 分钟前");
  assert.equal(ago(at(18, 11), now), "4 小时前");
  assert.equal(ago(at(17, 23), now), "昨天");
  assert.equal(ago(at(14), now), "4 天前");
  assert.equal(ago(at(2), now), "11月2日");
  assert.equal(clock(at(18, 9), now), "09:00");
  assert.equal(clock(at(17, 15), now), "11-17 15:00");
  assert.equal(clock("", now), "");
});

test("one row per owner/repo#N: a notification, a PR row and an issue of the same subject merge, the issue's class wins", () => {
  const p = prs([row("o/orders#2", { new: 3 }), row("o/orders#8")]);
  const rows = unify([message("2", { repository: "o/orders", url: "https://github.com/o/orders/issues/2", unread: true, updated_at: "2026-11-10T00:00:00Z" })], {
    p, issues: [issue("O/Orders#2", "wait", { updated_at: "2026-11-17T00:00:00Z" }), issue("o/app#5", "todo", { labels: ["bug"] })] });
  assert.deepEqual(rows.map(t => [t.key, t.kind, t.category]), [["o/orders#2", "issue", "wait"], ["o/orders#8", "pr", "reply"], ["o/app#5", "issue", "todo"]]);
  const [merged, , alone] = rows;
  assert.equal(merged.unread, true);  // 未读点跟通知
  assert.equal(merged.newCount, 3);  // +N 跟处理情况那一行
  assert.equal(merged.status, "reply");
  assert.equal(merged.updated_at, "2026-11-17T00:00:00.000Z".replace(".000", ""));
  assert.equal(statusLine(merged), "等对方回那一句");  // issue 用归类给的那一句
  assert.equal(alone.unread, false);
  assert.ok(searchText(alone).includes("bug"));  // 标签也能搜
});

test("filters: PR 评审中、待关闭、等对方回 merge into 等对方; Agent questions join 待你回; the rest of the flow is Agent 处理中", () => {
  assert.equal(issueBucket(issue("o/app#1", "pr")), "wait");
  assert.equal(issueBucket(issue("o/app#1", "close")), "wait");
  assert.equal(issueBucket(issue("o/app#1", "flow", { stage: "等你决定" })), "reply");
  assert.equal(issueBucket(issue("o/app#1", "flow", { stage: "等你看" })), "reply");
  assert.equal(issueBucket(issue("o/app#1", "flow", { stage: "已交给 Agent", stalled: "lost" })), "reply");
  assert.equal(issueBucket(issue("o/app#1", "flow", { stage: "处理中" })), "agent");
  assert.equal(issueBucket(issue("o/app#1", "todo", {}, { bucket: "look" })), "look");  // 后端给了就用后端的
  const p = prs([row("o/orders#1"), row("o/orders#3", { state: "agent", stage: "处理中" }), row("o/orders#4", { state: "done", open: false }),
    row("up/x#7", { state: "wait" }), row("up/x#8")]);
  const issues = ["reply", "todo", "todo", "wait", "pr", "close", "look", "flow"].map((key, n) => issue(`o/app#${n + 10}`, key))
    .concat([issue("o/app#30", "hold"), issue("o/app#31", "record")]);
  const rows = unify([message("99", { type: "CheckSuite", url: "https://github.com/o/app", number: null })], { p, issues });
  const side = sideCounts(rows, { tab: "reply", scope: "" });
  assert.deepEqual(side.tabs, { all: 16, reply: 3, draft: 0, todo: 2, agent: 2, wait: 3, look: 1, hold: 1, record: 1, mine: 4 });
  // 搁置、记录各成一个筛选，不在待动手和等对方里，也不算进角标
  assert.deepEqual(rows.filter(t => inTab(t, "hold")).map(t => t.key), ["o/app#30"]);
  assert.deepEqual(rows.filter(t => inTab(t, "record")).map(t => t.key), ["o/app#31"]);
  assert.deepEqual(rows.filter(t => inTab(t, "agent")).map(t => t.key), ["o/orders#3", "o/app#17"]);
  assert.deepEqual(rows.filter(t => inTab(t, "wait")).map(t => t.key), ["o/app#13", "o/app#14", "o/app#15"]);
  // 我发的 PR：你开着的 PR 都在这里（只看不交），在等对方的不再算进等对方，对方回了的同时在待你回
  assert.deepEqual(rows.filter(t => inTab(t, "mine")).map(t => t.key), ["o/orders#1", "o/orders#3", "up/x#7", "up/x#8"]);
  assert.equal(rows.find(t => t.key === "up/x#7").category, null);
  assert.equal(rows.find(t => t.key === "up/x#8").category, "reply");
  assert.equal(rows.filter(t => inTab(t, "all")).length, 16);  // 已合并的和 CI 只在收件箱里
  assert.equal(replyCount(rows), 3);  // 角标是待你回：o/orders#1、up/x#8、o/app#10；只在我发的 PR 里的不算
});

test("the rows the old messages page missed now land in 待你回, and a PR row follows the issue's class when both exist", () => {
  // office#2：别的仓库的待你回；#163：原「待你处理」里 Agent 等你决定
  const p = prs([row("o/channel#71", { state: "wait" }), row("o/channel#72", { state: "wait" })]);
  const issues = [
    issue("o/channel#70", "reply", { author: "boss" }, { button: "帮我回复", action: "feedback", bucket: "reply" }),
    issue("o/office#2", "reply", {}, { button: "帮我回复", action: "feedback", bucket: "reply" }),  // 所有仓库都出
    issue("o/app#163", "flow", { stage: "等你决定", waiting: [{ id: 9, kind: "ask", title: "【问你】选哪个" }] }, { bucket: "reply" }),
    issue("o/channel#71", "todo", { author: ME }, { bucket: "todo" }),  // PR 那份说等对方，issue 说待动手
    issue("o/channel#72", "flow", { stage: "处理中" }, { bucket: "agent" }),  // PR 那份说等对方，issue 说 Agent 流程中
  ];
  const rows = unify([], { p, issues });
  const by = Object.fromEntries(rows.map(t => [t.key, t]));
  assert.equal(rows.length, 5);  // 同一条只一行
  assert.deepEqual(rows.filter(t => inTab(t, "reply")).map(t => t.key).sort(), ["o/app#163", "o/channel#70", "o/office#2"]);
  assert.equal(by["o/channel#71"].category, "todo");
  assert.equal(by["o/channel#72"].category, "agent");
  assert.equal(rows.filter(t => inTab(t, "wait")).length, 0);
  assert.equal(replyCount(rows), 3);
  assert.equal(handVia(by["o/channel#70"]), "issue");  // 待你回：走 issue 留言
  assert.equal(handVia(by["o/office#2"]), "issue");
  assert.equal(handVia(by["o/app#163"]), "issue");  // 点开能回答，也能再交一轮
});

test("handing off: only 待动手 and 待你回 rows tick; 等对方、我发的 PR、通知、待你看 never do", () => {
  const p = prs([row("o/orders#9"), row("o/orders#5"), row("up/x#1"), row("up/x#2", { stage: "处理中", state: "reply" }), row("up/x#3", { stage: "已交给 Agent", stalled: "lost" }),
    row("up/x#4", { state: "wait", author: ME })]);
  const issues = [issue("o/app#1", "todo"), issue("o/app#3", "look"), issue("o/orders#5", "wait"), issue("o/app#6", "pr"),
    issue("o/app#7", "flow", { stage: "等你决定" }), issue("o/app#8", "flow", { stage: "处理中" }), issue("o/app#11", "flow", { stage: "已交给 Agent", stalled: "lost" }),
    issue("o/app#20", "hold"), issue("o/app#21", "record")];
  const rows = unify([message("6", { type: "PullRequest", url: "https://github.com/o/app/pull/60", number: 60 })], { p, issues });
  const via = Object.fromEntries(rows.map(t => [t.key, handVia(t)]));
  assert.deepEqual(via, {
    "o/app#60": null,  // 只有通知
    "o/orders#9": "prs",  // 你开的 PR 的待你回、没有 issue 那一行
    "o/orders#5": null,  // issue 的类别优先：等对方
    "up/x#1": "prs", "up/x#2": null, "up/x#3": "prs", "up/x#4": null,  // 在处理的不再交；派发可能丢了的可以再交；我发的 PR 在等对方的不交
    "o/app#1": "issue", "o/app#3": null, "o/app#6": null,  // 待你看只在详情里「仍交给 Agent」
    "o/app#7": "issue", "o/app#8": null, "o/app#11": "issue",
    "o/app#20": null, "o/app#21": null,  // 搁置、记录不出复选框
  });
});

test("handing over more than 10 at once asks first, in the page, with the count", () => {
  assert.equal(CONFIRM_OVER, 10);
  assert.equal(needsConfirm(10), false);
  assert.equal(needsConfirm(11), true);
  assert.equal(confirmText(26), "一次交给 Agent 26 条。中台最多同时跑 10 个，其余 16 条排队，按顺序派出。");
  assert.equal(confirmText(12, 8), "一次交给 Agent 12 条。中台最多同时跑 8 个，其余 4 条排队，按顺序派出。");
});

test("a list row does not repeat its category, which the tag or the picked filter already shows", () => {
  const now = new Date(2026, 10, 18, 15, 0).getTime();
  const at = (d, h, m = 0) => new Date(2026, 10, d, h, m).toISOString();
  const prLine = (r, tab = "all") => rowLine(mergeThreads([], { prs: prs([r]) })[0], tab, now);
  const one = (i, tab = "all") => rowLine(unify([], { issues: [i] })[0], tab, now);
  assert.equal(prLine(row("o/orders#1", { said: [{ who: "alice", what: "评论", at: at(18, 10, 41), new: true }] })), "alice 10:41 评论");
  assert.equal(one(issue("o/app#9", "reply", {}, { line: "boss 11-18 09:00 开的，你还没回" })), "boss 11-18 09:00 开的");
  assert.equal(prLine(row("o/orders#2", { state: "agent", stage: "处理中" })), "");
  assert.equal(prLine(row("o/orders#2", { state: "agent", stage: "处理中", mode: "reply" })), "只回复");
  assert.equal(one(issue("o/app#10", "flow", {}, { line: "处理中 · 11-18 16:58 交给", bucket: "agent" })), "11-18 16:58 交给");
  assert.equal(one(issue("o/app#11", "wait", {}, { line: "等对方：标签 question" })), "标签 question");
  assert.equal(one(issue("o/app#12", "record", {}, { line: "标签 roadmap，是记录" })), "标签 roadmap");
  const mine = row("o/orders#7", { state: "wait", their_at: "", mine_at: at(18, 9) });
  assert.equal(prLine(mine), "你开的 PR · 还没人回");  // 收件箱里没有类别标记，留着
  assert.equal(prLine(mine, "mine"), "还没人回");
  assert.equal(one(issue("o/app#13", "pr", {}, { line: "PR #80 开着，等评审" })), "PR #80 开着，等评审");
});

test("done issues and the project link follow the scope", () => {
  assert.equal(doneInScope({ repo: "o/app" }, "O/App"), true);
  assert.equal(doneInScope({ repo: "o/lib" }, "O/App"), false);
  assert.equal(doneInScope({ repo: "o/app" }, ""), true);
  const projects = [{ id: "app", issue_repository: "o/app", repository: "o/app-code" }, { id: "kb", issue_repository: "", repository: "" }];
  assert.equal(projectOfRepo("O/App", projects).id, "app");
  assert.equal(projectOfRepo("o/app-code", projects).id, "app");
  assert.equal(projectOfRepo("t/kb", projects, [{ repository: "t/kb", project_id: "kb" }]).id, "kb");
  assert.equal(projectOfRepo("z/none", projects), null);
});

test("the inbox address keeps the filter and scope; old addresses land on the matching filter", () => {
  assert.deepEqual(parseInboxHash("#/inbox"), { tab: "reply", scope: "", project: "" });
  assert.deepEqual(parseInboxHash("#/inbox?f=todo&s=o/app"), { tab: "todo", scope: "o/app", project: "" });
  assert.deepEqual(parseInboxHash("#/inbox?f=todo&s=team"), { tab: "todo", scope: "", project: "" });  // 不是仓库的范围不认
  assert.equal(parseInboxHash("#/inbox?f=done&s=o/app").tab, "done");
  assert.equal(parseInboxHash("#/inbox?f=mine").tab, "mine");
  assert.deepEqual(parseInboxHash("#/inbox?f=projects&s=bogus"), { tab: "reply", scope: "", project: "" });  // 「按项目」已去掉
  assert.equal(parseInboxHash("#/runs"), null);
  assert.equal(inboxHash({ tab: "reply", scope: "o/orders" }), "#/inbox?s=o/orders");
  assert.equal(inboxHash({ tab: "all", scope: "o/orders" }), "#/inbox?f=all&s=o/orders");
  assert.equal(inboxHash(), "#/inbox");
  // 原待你处理、消息页、PR 待处理
  assert.equal(legacyInboxHash("#/todo"), "#/inbox");
  assert.equal(legacyInboxHash("#/prs"), "#/inbox");
  assert.equal(legacyInboxHash("#/messages"), "#/inbox");
  assert.equal(legacyInboxHash("#/messages?t=wait&s=o/orders"), "#/inbox?f=wait&s=o/orders");
  assert.equal(legacyInboxHash("#/messages?t=all&s=o/orders"), "#/inbox?f=all&s=o/orders");
  assert.equal(legacyInboxHash("#/messages?r=o/orders"), "#/inbox?s=o/orders");
  // 原 Issue 列表和按项目
  assert.equal(legacyInboxHash("#/"), "#/inbox");
  assert.equal(legacyInboxHash(""), "#/inbox");
  assert.equal(legacyInboxHash("#/issues"), "#/inbox?f=all");
  assert.equal(legacyInboxHash("#/?v=issue&p=codewhale"), "#/inbox?f=all&p=codewhale");
  assert.equal(legacyInboxHash(`#/?v=project&g=${encodeURIComponent("个人工具")}`), "#/inbox?f=all");
  assert.equal(legacyInboxHash(`#/?g=${encodeURIComponent("开源协作")}&s=x`), "#/inbox?f=all");
  for (const hash of ["#/inbox", "#/p/codewhale", "#/i/o/r/3", "#/q/5", "#/brief"]) assert.equal(legacyInboxHash(hash), null, hash);
});

test("items the Agent already settled stay out of hand-offs: 待你定 under 待你回, Agent 看过 under 待你看, settled PRs untickable", () => {
  const settled = { kind: "decide", run: 1969, rule: "处理 issue", flow: "issue", line: "这一版收不收费", since: "2026-11-18T09:30:28Z", finished_at: "2026-11-18T09:31:03Z" };
  const decide = issue("o/app#108", "decide", {}, { name: "待你定", line: "Agent 要你定：这一版收不收费", button: "仍交给 Agent", drawer_only: true, action: "issue", settled });
  const seen = issue("o/app#163", "seen", {}, { name: "Agent 看过", button: "仍交给 Agent", drawer_only: true, action: "issue", settled: { ...settled, kind: "nothing" } });
  assert.equal(issueBucket(decide), "reply");  // 旧后端没给 bucket 时照 next_step.bucket 推
  assert.equal(issueBucket(seen), "look");
  const [d, n] = unify([], { issues: [decide, seen] });
  assert.equal(handVia(d), null);  // 列表上不能勾、不能批量交
  assert.equal(handVia(n), null);
  assert.equal(replyHint(d), null);  // 不出「只需回复 / 需要处理」的建议
  assert.equal(statusLine(d), "Agent 要你定：这一版收不收费");
  const p = prs([row("o/orders#7", { stage: "等你决定", run: { id: 2083, state: "done", summary: "", error: "", finished_at: "", created_at: "" },
    settled: { ...settled, rule: "处理 PR 反馈", flow: "pr", line: "非维护者要求改动，要你决定" } }), row("o/orders#8", { stage: "等你决定" })]);
  const [pr, other] = unify([], { p });
  assert.equal(pr.category, "reply");
  assert.equal(handVia(pr), null);
  assert.equal(handVia(other), "prs");  // 没结论（或之后有新动静）的照常能交
  assert.equal(statusLine(pr), "Agent 要你定：非维护者要求改动，要你决定");
});

test("someone else's newer words: the issue row carries +N in reply and agent, and a seen-but-unanswered row stays in reply", () => {
  const heard = issue("o/app#1", "reply", {}, { bucket: "reply", new: 2 });
  const busy = issue("o/app#2", "flow", {}, { bucket: "agent", new: 1 });
  const waiting = issue("o/app#3", "pr", {}, { bucket: "wait", new: 1 });
  const rows = Object.fromEntries(unify([message("1", { new: 5 })], { issues: [heard, busy, waiting] }).map(t => [t.key, t]));
  assert.equal(rows["o/app#1"].newCount, 2);  // issue 那一行的条数优先于通知的
  assert.equal(rows["o/app#2"].category, "agent");
  assert.equal(rows["o/app#2"].newCount, 1);
  assert.equal(rows["o/app#3"].newCount, null);  // 等对方的行不标
  assert.equal(issueBucket(issue("o/app#4", "seen", {}, { new: 1 })), "reply");  // 旧后端没给 bucket 时同样推
  assert.equal(issueBucket(issue("o/app#4", "seen")), "look");
});

test("a PR the Agent saw and said nothing to do, with their word unanswered, says both", () => {
  const settled = { kind: "nothing", run: 2201, rule: "处理 PR 反馈", flow: "pr", line: "对方说先定方向", since: "", finished_at: "" };
  const said = [{ who: "maint", what: "评论", at: "2026-11-18T01:21:45Z", text: "", url: "", path: "", new: true, maintainer: true, role: "" }];
  const [t] = unify([], { p: prs([row("a/b#7", { stage: "已处理", new: 1, said, settled })]) });
  assert.equal(t.category, "reply");
  assert.equal(handVia(t), null);  // 不再派
  assert.match(statusLine(t, Date.parse("2026-11-18T12:00:00Z")), /^maint .+ 评论 · Agent 看过，不用动：对方说先定方向$/);
  const [quiet] = unify([], { p: prs([row("a/b#8", { stage: "已处理", said: [{ ...said[0], new: false }], settled: { ...settled, kind: "decide" } })]) });
  assert.equal(statusLine(quiet), "Agent 要你定：对方说先定方向");
});

test("详情顶上的项目链接：项目名就是仓库名时不重复写", async () => {
  const { projectLinkText } = await import("../src/InboxParts.ts");
  assert.equal(projectLinkText("career-ops", "career-ops-hq/career-ops"), "项目详情 →");
  assert.equal(projectLinkText("工作台", "luochen211/workbench"), "工作台 · 项目详情 →");
  assert.equal(projectLinkText(null, "a/b"), "项目详情 →");
});
