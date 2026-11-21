// 「每 60 秒刷新」：默认开启；手动关掉记在 localStorage，下次打开保持关闭。
// 刷新不打断操作：issue 详情或弹窗开着、正在输入时这一轮跳过；页面在后台时暂停，切回来立即刷新一次。
// GitHub 请求由后端缓存挡住（issue 5 分钟、项目详情 2 分钟），60 秒一次的前端刷新只读本机缓存。
export const AUTO_KEY = "paihuo:auto-refresh";
export const AUTO_EVERY = 60000;
type Store = Pick<Storage, "getItem" | "setItem"> | null | undefined;

export function browserStorage(): Store {
  try { return globalThis.localStorage; } catch { return null; }
}

/** 从没设置过、或读不到存储的，一律按开启处理。 */
export function loadAuto(store: Store = browserStorage()): boolean {
  try { return store?.getItem(AUTO_KEY) !== "off"; } catch { return true; }
}

export function saveAuto(on: boolean, store: Store = browserStorage()): void {
  try { store?.setItem(AUTO_KEY, on ? "on" : "off"); } catch { /* 隐私模式等存不了，只影响下次的默认值 */ }
}

/** 焦点在输入框、文本框或下拉框里算正在输入（复选框、按钮不算）。 */
export function isTyping(el: { tagName?: string; type?: string; isContentEditable?: boolean } | null | undefined): boolean {
  if (!el?.tagName) return false;
  const name = el.tagName.toUpperCase();
  if (name === "TEXTAREA" || name === "SELECT" || el.isContentEditable) return true;
  return name === "INPUT" && !["checkbox", "radio", "button", "submit", "reset"].includes((el.type || "text").toLowerCase());
}

/** 这一轮要不要刷新：页面在后台、弹窗开着、正在输入时都跳过。 */
export function shouldAutoRefresh({ hidden, modalOpen, typing }: { hidden: boolean; modalOpen: boolean; typing: boolean }): boolean {
  return !hidden && !modalOpen && !typing;
}

interface Doc {
  hidden: boolean; activeElement: Parameters<typeof isTyping>[0];
  addEventListener(type: "visibilitychange", fn: () => void): void; removeEventListener(type: "visibilitychange", fn: () => void): void;
}
interface Timers { setInterval(fn: () => void, ms: number): unknown; clearInterval(id: unknown): void }

/** 开始每 60 秒刷新，返回停止函数。modalOpen 每一轮现读（弹窗开着就跳过）；页面切回前台时立即刷新一次。 */
export function startAutoRefresh({ run, modalOpen, doc, timers = globalThis as unknown as Timers, every = AUTO_EVERY }: {
  run: () => void; modalOpen: () => boolean; doc: Doc; timers?: Timers; every?: number;
}): () => void {
  const tick = () => {
    if (shouldAutoRefresh({ hidden: doc.hidden, modalOpen: modalOpen(), typing: isTyping(doc.activeElement) })) run();
  };
  const id = timers.setInterval(tick, every);
  const back = () => { if (!doc.hidden) tick(); };
  doc.addEventListener("visibilitychange", back);
  return () => { timers.clearInterval(id); doc.removeEventListener("visibilitychange", back); };
}
