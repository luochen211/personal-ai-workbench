// 收件箱的「微信」范围（后端 personal/plugins/wechat/inbox.py，可选）：微信的行并进收件箱的行，新鲜度那一句，「已处理」送给后端的一条。
// 只有私聊待回、群里 @我、待收转账落「待你回」（计入导航角标），其余落「待你看」，暂时不处理的落「搁置」，类别由后端给。
// 微信只读：不交给 Agent、不写回复草稿、不发消息。不写 JSX，node --test 可以直接加载这个文件。
import { clock } from "./InboxParts.ts";  // 带扩展名：node --test 直接加载
import type { Thread } from "./InboxParts";
import type { WechatReport, WechatRow } from "./api";

/** 后端的行换成收件箱的行：没有仓库和编号，title 是会话名，类别是后端给的 bucket。 */
export const wechatThread = (row: WechatRow): Thread => ({
  key: row.key, repository: "", number: null, kind: "wechat", title: row.chat, url: "", unread: false, updated_at: row.at,
  done: false, classification: "workspace", project_id: null, project_name: null, status: null, newCount: null, mine: false,
  category: row.bucket, wechat: row,
});

/** 没启用（没有 wechat.json）或还没读到是空的。 */
export const wechatThreads = (report: WechatReport | null | undefined): Thread[] =>
  report?.enabled ? (report.rows ?? []).map(wechatThread) : [];

/** 「微信数据更新于 HH:MM」：今天只写时刻，别的天带月日；还没有数据时写清楚。 */
export function freshness(report: WechatReport | null | undefined, now = Date.now()) {
  if (!report?.enabled) return "";
  if (report.updated_at) return `微信数据更新于 ${clock(report.updated_at, now)}`;
  return report.running ? "正在读取微信数据…" : "还没有微信数据";
}

/** 「已处理」送给后端的一条：这一行最新一条消息的时间，这个会话有更新的消息才回来。 */
export const doneItem = (row: Pick<WechatRow, "key" | "ts">) => ({ key: row.key, ts: row.ts });

/** 行前小标签：私聊待回等信号；同一个群几条合成一行时写条数。 */
export const signalText = (row: Pick<WechatRow, "label" | "count" | "kind">) =>
  row.count > 1 && row.kind !== "private" ? `${row.label} ×${row.count}` : row.label;
