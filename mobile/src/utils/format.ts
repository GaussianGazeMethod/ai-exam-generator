/** 移动端展示格式化（与 Web 端保持一致的写法）。 */

import dayjs from "dayjs";

export function fmtDateTime(v?: string | null): string {
  if (!v) return "—";
  const d = dayjs(v);
  return d.isValid() ? d.format("YYYY-MM-DD HH:mm") : "—";
}

/** 秒 → mm:ss（倒计时用，始终补零） */
export function fmtCountdown(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

/** 秒 → “12 分 34 秒” */
export function fmtDuration(seconds?: number | null): string {
  if (!seconds || seconds <= 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? `${m} 分 ${s} 秒` : `${s} 秒`;
}
