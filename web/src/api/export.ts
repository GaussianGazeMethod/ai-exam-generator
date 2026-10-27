/**
 * 报表导出（服务端生成）。
 *
 * 背景：统计看板与合规全景的「导出 Excel / 导出合规报告 PDF」原来是前端打印或
 * 拼 CSV；后端已提供真正的导出接口（openpyxl / reportlab 生成），
 * 由服务端产出既保证内容与看板一致，也避免前端为拼报表引入依赖。
 *
 * 这些接口需要 Bearer Token，普通的 <a href> 无法带鉴权头，
 * 故走 axios 取 blob 再触发下载。
 */

import api from "./client";

function filenameFrom(disposition: string | undefined, fallback: string): string {
  if (!disposition) return fallback;
  // 优先 RFC 5987 的 filename*（后端用它对中文名做 URL 编码）
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1]);
    } catch {
      /* 落到 filename */
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  return plain?.[1] ?? fallback;
}

async function downloadBlob(url: string, fallbackName: string): Promise<string> {
  const res = await api.get(url, { responseType: "blob" });
  const name = filenameFrom(
    res.headers["content-disposition"] as string | undefined,
    fallbackName,
  );
  const blobUrl = URL.createObjectURL(res.data as Blob);
  const a = document.createElement("a");
  a.href = blobUrl;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(blobUrl);
  return name;
}

/** 导出考试统计明细 Excel。 */
export function exportExamExcel(examId: string): Promise<string> {
  return downloadBlob(
    `/statistics/exams/${examId}/export/excel`,
    `考试统计_${examId}.xlsx`,
  );
}

/** 导出单场考试的合规报告 PDF。 */
export function exportExamPdf(examId: string): Promise<string> {
  return downloadBlob(`/statistics/exams/${examId}/export/pdf`, `合规报告_${examId}.pdf`);
}

/** 导出合规全景报告 PDF（可带时间范围 period，如 2026Q3 / 2026）。 */
export function exportCompliancePdf(period?: string): Promise<string> {
  return downloadBlob(
    `/statistics/compliance/export/pdf${period ? `?period=${encodeURIComponent(period)}` : ""}`,
    "制度传达合规报告.pdf",
  );
}
