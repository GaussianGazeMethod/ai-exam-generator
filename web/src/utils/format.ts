/**
 * 轻量展示工具 — 日期格式化 / 相册式相对时间 / 导出。
 *
 * 约定：页面不自行拼日期字符串，统一走这里，避免同一页面出现
 * “2025/07/14” 与 “2025-07-14 14:26” 两种写法。
 */

import dayjs from "dayjs";
import "dayjs/locale/zh-cn";
import relativeTime from "dayjs/plugin/relativeTime";
import weekday from "dayjs/plugin/weekday";

dayjs.extend(relativeTime);
dayjs.extend(weekday);
dayjs.locale("zh-cn");

/** YYYY-MM-DD HH:mm */
export function fmtDateTime(v?: string | null): string {
  if (!v) return "—";
  const d = dayjs(v);
  return d.isValid() ? d.format("YYYY-MM-DD HH:mm") : "—";
}

/** YYYY-MM-DD */
export function fmtDate(v?: string | null): string {
  if (!v) return "—";
  const d = dayjs(v);
  return d.isValid() ? d.format("YYYY-MM-DD") : "—";
}

/** 秒 → “12:34” / “1 小时 02 分” */
export function fmtDuration(seconds?: number | null): string {
  if (!seconds || seconds <= 0) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return `${m}:${String(s).padStart(2, "0")}`;
  return `${Math.floor(m / 60)} 小时 ${String(m % 60).padStart(2, "0")} 分`;
}

/** 相对时间：3 分钟前 / 昨天 / 5 天前 */
export function fmtFromNow(v?: string | null): string {
  if (!v) return "—";
  const d = dayjs(v);
  return d.isValid() ? d.fromNow() : "—";
}

/** “周一，2025 年 7 月 14 日” */
export function todayLabel(): string {
  const d = dayjs();
  const wd = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][d.day()];
  return `${wd}，${d.year()} 年 ${d.month() + 1} 月 ${d.date()} 日`;
}

/** 按时段问候：早上好 / 下午好 / 晚上好 */
export function greeting(): string {
  const h = dayjs().hour();
  if (h < 6) return "夜深了";
  if (h < 12) return "早上好";
  if (h < 14) return "中午好";
  if (h < 18) return "下午好";
  return "晚上好";
}

/** 百分比展示：82.3% */
export function fmtPercent(v?: number | null, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v.toFixed(digits)}%`;
}

/** 分数展示：82.6 / — */
export function fmtScore(v?: number | null, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toFixed(digits);
}

/** 完成率（已考 / 应考） */
export function completionRate(done: number, total: number): number {
  if (!total || total <= 0) return 0;
  return Math.round((done / total) * 1000) / 10;
}

/** CSV 转义：含逗号/引号/换行时加引号 */
function csvCell(value: unknown): string {
  const s = value === null || value === undefined ? "" : String(value);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/**
 * 导出 CSV（Excel 可直接打开）。
 *
 * 加 UTF-8 BOM，否则 Excel 打开中文会乱码。
 */
export function downloadCsv(filename: string, headers: string[], rows: unknown[][]): void {
  const lines = [headers.map(csvCell).join(","), ...rows.map((r) => r.map(csvCell).join(","))];
  const blob = new Blob([`\uFEFF${lines.join("\r\n")}`], {
    type: "text/csv;charset=utf-8;",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename.endsWith(".csv") ? filename : `${filename}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/**
 * 导出 PDF：走浏览器打印（“另存为 PDF”）。
 *
 * 说明：不引入 jsPDF/html2canvas，避免为一份报告增加 1MB+ 依赖；
 * 打印样式在 app.css 的 @media print 中隐藏侧栏与顶栏。
 */
export function printAsPdf(): void {
  window.print();
}

/** 复制文本到剪贴板（带降级方案，http 环境也能用）。 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* 降级 */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

/** 统一从异常中取可读信息（后端 detail 优先）。 */
export function errMsg(err: unknown, fallback = "操作失败"): string {
  const e = err as { response?: { data?: { detail?: unknown } }; message?: string };
  const detail = e?.response?.data?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] as { msg?: string };
    if (first?.msg) return first.msg;
  }
  return e?.message || fallback;
}
