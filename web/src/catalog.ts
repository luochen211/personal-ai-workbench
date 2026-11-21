// 原 Issue 列表（主页 #/）的网址：现在只用来把旧网址换成收件箱的（见 InboxParts.legacyInboxHash）。
// #/?v=<issue|project>&g=<一级分组>&s=<二级分类>&t=<标签>&p=<项目>；更早的项目总览 #/?g=&s=&t=（没有 v）、Issue 列表 #/issues 也认。
export interface Filter { group: string; sub: string; tag: string }
export type Mode = "issue" | "project";
export interface Home extends Filter { mode: Mode; project: string }

/** 是主页的网址就读出视图和筛选，否则是 null。 */
export function parseHome(hash: string): Home | null {
  const found = hash.match(/^(?:#?\/?|#\/issues)(?:\?(.*))?$/);
  if (!found) return null;
  const params = new URLSearchParams(found[1] ?? "");
  const filter = { group: params.get("g") ?? "", sub: params.get("s") ?? "", tag: params.get("t") ?? "" };
  const legacy = !hash.startsWith("#/issues") && !params.has("v") && !!(filter.group || filter.sub || filter.tag);
  const mode: Mode = params.get("v") === "project" || legacy ? "project" : "issue";
  return { mode, ...filter, project: params.get("p") ?? "" };
}
