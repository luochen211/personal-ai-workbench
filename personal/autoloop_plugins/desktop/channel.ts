// 本机桌面通道（覆盖 agent-autoloop 自带的 desktop）：Agent 的提问或结果来了交给派活仓库的 personal/notify_open.py，
// 弹一个点了就打开工作台的提醒（#/q/<编号>，页面打开它所属的 issue，能看全文、回答）。
// 中台协议把提问和通知统称 Card（nextCard、delivered），这里只是照协议的名字调用。由 python3 -m personal.agent_watch install
// 装到 ~/.agent-autoloop/plugins/desktop/channel.ts，改了要重启中台才生效：launchctl kickstart -k gui/$(id -u)/agent-autoloop
// （不要用 agent-autoloop service install 重启：它会按默认值重写 plist，丢掉手工加进 PATH 的目录）。
// 派活仓库按 ~/.agent-autoloop/issues/paihuo-root 找；找不到时退回普通通知，不写命令行提示。
import { execFile, spawn, spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

type Item = { id: number; version: number; ref: string | null; kind: "ask" | "notice"; title: string };
type Context = {
  hub: {
    nextCard(wait: number): Promise<{ card: Item | null }>;  // 中台协议的字段名
    delivered(id: number, version: number, ref: string): Promise<unknown>;
    failed(id: number, version: number, error: string): Promise<unknown>;
  };
  log: (line: string) => void;
  signal: AbortSignal;
};

const HOME = process.env.AGENT_AUTOLOOP_HOME ?? join(process.env.HOME ?? "", ".agent-autoloop");

export function paihuoRoot(home: string = HOME): string | null {
  try {
    const root = (process.env.PAIHUO_ROOT ?? readFileSync(join(home, "issues", "paihuo-root"), "utf8")).trim();
    return existsSync(join(root, "personal", "notify_open.py")) ? root : null;
  } catch {
    return null;
  }
}

export function heading(item: Item): string {
  return item.kind === "ask" ? "Agent 问你一个问题" : "Agent 有结果等你看";
}

/** 标题是 Agent 写的，只当参数传，不拼进脚本。 */
export function openCommand(item: Item, root: string): [string, string[]] {
  // 写成 --text=… 一个参数：标题以 - 开头也不会被当成选项
  return ["python3", ["-m", "personal.notify_open", "notify", `--title=${heading(item)}`, `--text=${item.title}`, `--question=${item.id}`]];
}

function plain(item: Item) {
  const script = ["on run argv", "display notification (item 2 of argv) with title (item 1 of argv)", "end run"];
  return new Promise<void>((resolve, reject) => {
    execFile("osascript", [...script.flatMap((line) => ["-e", line]), "--", heading(item), item.title], { timeout: 30_000 },
      (error) => (error ? reject(error) : resolve()));
  });
}

function hasDesktop() {
  return process.platform === "darwin" &&
    spawnSync("launchctl", ["print", `gui/${process.getuid!()}`], { stdio: "ignore" }).status === 0;
}

export default async function desktop({ hub, log, signal }: Context) {
  const available = hasDesktop();
  while (!signal.aborted) {
    let item: Item | null;
    try {
      ({ card: item } = await hub.nextCard(50));
    } catch (error) {
      log(`连不上中台：${error}`);
      await new Promise((resolve) => setTimeout(resolve, 10_000));
      continue;
    }
    if (!item) continue;
    if (item.ref) {
      await hub.delivered(item.id, item.version, item.ref);
      continue;
    }
    if (!available) {
      await hub.delivered(item.id, item.version, `skipped-${item.id}`);
      continue;
    }
    try {
      const root = paihuoRoot();
      if (root) {
        // 对话框会等人点，不能挡住后面的提醒：脱离中台进程单独跑
        const [program, args] = openCommand(item, root);
        spawn(program, args, { cwd: root, detached: true, stdio: "ignore" }).unref();
      } else {
        await plain(item);
      }
      await hub.delivered(item.id, item.version, `shown-${item.id}`);
    } catch (error) {
      log(`第 ${item.id} 条提醒的桌面通知没发出：${error}`);
      await hub.failed(item.id, item.version, String(error)).catch(() => {});
    }
  }
}
