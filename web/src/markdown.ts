// GitHub 风格的 Markdown 显示：issue / PR 正文、评论、GitHub 消息和 Agent 结论都用它。
// 正文是外部输入：不启用原始 HTML，再用 rehype-sanitize 的默认白名单（和 GitHub 的一致）过一遍，
// 链接只留 http(s)/mailto，新标签页打开。#123、owner/repo#123、@用户名 转成 GitHub 链接（代码和已有链接里不转）。
// 不写 JSX，node --test 可以直接加载这个文件做渲染测试。
import { createElement } from "react";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";

interface Node { type: string; value?: string; url?: string; children?: Node[] }

const REFERENCE = /(?<![\w/@.-])(?:([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)#(\d+)|#(\d+)|@([A-Za-z0-9](?:[A-Za-z0-9-]{0,38})))(?![\w-])/g;
const SKIP = new Set(["link", "linkReference", "inlineCode", "code", "definition"]);

export function referenceUrl(match: RegExpExecArray, repo?: string): string | null {
  if (match[1]) return `https://github.com/${match[1]}/issues/${match[2]}`;
  if (match[3]) return repo ? `https://github.com/${repo}/issues/${match[3]}` : null;
  return `https://github.com/${match[4]}`;
}

function split(value: string, repo?: string): Node[] {
  const out: Node[] = [];
  let last = 0;
  for (const match of value.matchAll(REFERENCE)) {
    const url = referenceUrl(match as RegExpExecArray, repo);
    if (!url) continue;
    const start = match.index ?? 0;
    if (start > last) out.push({ type: "text", value: value.slice(last, start) });
    out.push({ type: "link", url, children: [{ type: "text", value: match[0] }] });
    last = start + match[0].length;
  }
  if (last < value.length) out.push({ type: "text", value: value.slice(last) });
  return out;
}

function walk(node: Node, repo?: string) {
  if (!node.children || SKIP.has(node.type)) return;
  node.children = node.children.flatMap(child => {
    if (child.type === "text" && child.value) return split(child.value, repo);
    walk(child, repo);
    return [child];
  });
}

export function githubReferences(options: { repo?: string } = {}) {
  return (tree: Node) => walk(tree, options.repo);
}

const external = (props: { href?: string; children?: ReactNode }) =>
  createElement("a", { href: props.href, target: "_blank", rel: "noopener noreferrer" }, props.children);
const picture = (props: { src?: string; alt?: string }) =>
  createElement("img", { src: props.src, alt: props.alt ?? "", loading: "lazy", referrerPolicy: "no-referrer" });

/** 渲染一段 GitHub Markdown。repo 用来把 #123 链到同一仓库。 */
export default function Markdown({ text, repo }: { text: string; repo?: string }) {
  return createElement("div", { className: "markdown" },
    createElement(ReactMarkdown, {
      remarkPlugins: [remarkGfm, [githubReferences, { repo }]],
      rehypePlugins: [rehypeSanitize],
      components: { a: external, img: picture },
    }, text));
}
