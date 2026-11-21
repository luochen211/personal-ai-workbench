import { useCallback, useEffect, useState } from "react";

type Theme = "light" | "dark";
const KEY = "paihuo-theme";

function systemDark(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;
}

function saved(): Theme | null {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

/** 明暗主题：有保存的就用保存的，否则跟系统；切换时写 localStorage（读写失败就当没有）。 */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => saved() ?? (systemDark() ? "dark" : "light"));
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);
  const toggle = useCallback(() => {
    setTheme((t) => {
      const next: Theme = t === "dark" ? "light" : "dark";
      try {
        localStorage.setItem(KEY, next);
      } catch {
        /* 私密窗口等情况下存不了，忽略 */
      }
      return next;
    });
  }, []);
  return [theme, toggle];
}
