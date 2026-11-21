import { useEffect, useRef } from "react";

// 页面里的二次确认（不用浏览器原生 confirm）：一句说明，「取消」和确认两个按钮，extra 是可选的第三个按钮；Esc 等于取消
export default function Confirm({ title, text, ok, confirm, close, extra }: {
  title: string; text: string; ok: string; confirm: () => void; close: () => void; extra?: { label: string; run: () => void };
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return <dialog ref={ref} className="confirm" aria-label={title} onCancel={event => { event.preventDefault(); close(); }}>
    <h2>{title}</h2>
    <p>{text}</p>
    <div className="confirm-actions"><button autoFocus onClick={close}>取消</button>{extra && <button onClick={extra.run}>{extra.label}</button>}<button className="primary" onClick={confirm}>{ok}</button></div>
  </dialog>;
}
