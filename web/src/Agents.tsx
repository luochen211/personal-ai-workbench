import Question from "./Question";
import type { AgentsReport, FeedRow } from "./api";
import { saidLine, stallText } from "./PrParts";
import { FeedResultLine, agentLine, agentsLine, running } from "./AgentParts";

// 收件箱用的两块：列表上方几个 Agent 在跑，行详情里这条 PR 的处理情况。
const when = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";
export const stageTone = (stage: string) => stage === "等你决定" ? "bad" : stage === "等你看" ? "warn" : stage === "已处理" ? "ok" : "";

/** 列表上方：N 个 Agent 在跑 · M 个排队 · 最多同时 K 个，下面列在跑的。 */
export function AgentStatus({ agents }: { agents: AgentsReport }) {
  const list = running(agents);
  return <div className="agent-status" role="status">
    <strong>{agentsLine(agents)}</strong>
    {agents.error ? <span className="meta">{agents.error}</span>
      : list.length ? <ul>{list.map(a => <li key={a.id}>{agentLine(a)}</li>)}</ul>
      : <span className="meta">现在没有 Agent 在跑。</span>}
  </div>;
}

/** 行详情里的「Agent 处理」：阶段、对方说了什么、你最后一次动作、派发和结果、Agent 的提问。 */
export function FeedDetail({ row, answered, title = "Agent 处理" }: { row: FeedRow; answered: (message: string) => void; title?: string }) {
  const stall = stallText(row);
  const issue = row.kind === "issue";
  return <section className="detail-section"><h3>{title}</h3>
    <p className="pr-feedback-meta">
      {!row.open && <span className="tag">已不在开着的列表里</span>}
      {row.draft && <span className="tag">草稿</span>}
      {row.pending && <span className="tag news">{row.new} 条新回复</span>}
      <span className={`tag ${stageTone(row.stage)}`}>{row.stage}</span>
      {stall && <span className="tag warn">派发可能丢了</span>}
    </p>
    {row.said.length > 0 && <ul className="pr-said-list">{row.said.map((s, n) => <li key={n}>
      <span><a href={s.url || row.url} target="_blank" rel="noreferrer">{saidLine(s)}</a></span><time>{when(s.at)}</time>
    </li>)}</ul>}
    {row.mine_at && <p className="meta">你最后一次{issue ? "留言" : "动作"}：{when(row.mine_at)}</p>}
    {stall && <p className="notice-line" role="status">{stall}</p>}
    {row.stage === "已交给 Agent" && !stall && row.handed_at && <p className="meta">{when(row.handed_at)} 交给 Agent，正在派出。</p>}
    {row.run && <FeedResultLine result={row.run.result} />}
    {row.unseen && <p className="notice-line" role="status">回执说已经{issue ? "写了回复" : "推送或写了回复"}，但没找到这一轮的回复草稿，GitHub 上也还没看到你账号在这一轮之后的动作，去看一眼。</p>}
    {row.by_me && <p className="meta">你在那之后自己动过手，对方没有新回复，算处理过了。</p>}
    {row.waiting.filter(w => w.kind === "ask").map(w => <Question key={w.id} item={w} done={answered}
      path={`/api/prs/questions/${w.id}/answer`} />)}
  </section>;
}
