import { useEffect, useRef } from "react";
import type { ReactNode } from "react";

export default function Modal({ title, close, children }: { title: string; close: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return <dialog ref={ref} className="detail" aria-label={title} onCancel={event => { event.preventDefault(); close(); }}>
    <button className="close" onClick={close} aria-label={`关闭${title}`}>关闭 ×</button>{children}
  </dialog>;
}
