/**
 * 考试选择器 — 统计看板 / 部门统计 / 合规全景 / 题目质量分析共用。
 *
 * 这些页面都以「某一场考试」为分析主体（spec M6），故统一在这里处理：
 * 空态引导、按状态分组、显示已考/应考进度。
 */

import { Select } from "antd";

import { useExamOptions } from "../api/hooks";
import { EXAM_STATUS_LABEL, type Exam } from "../api/types";
import { EmptyHint } from "./ui";

export function useExamChoices() {
  const { data, isLoading } = useExamOptions();
  const exams: Exam[] = data?.items ?? [];
  return { exams, isLoading };
}

/** 把考试列表按状态排序：进行中 → 已截止 → 待发布 → 草稿 → 已终止。 */
export function sortExams(exams: Exam[]): Exam[] {
  const order: Record<string, number> = {
    in_progress: 0,
    finished: 1,
    pending: 2,
    draft: 3,
    terminated: 4,
  };
  return [...exams].sort((a, b) => {
    const d = (order[a.status] ?? 9) - (order[b.status] ?? 9);
    if (d !== 0) return d;
    return (b.created_at ?? "").localeCompare(a.created_at ?? "");
  });
}

export function ExamSelector({
  value,
  onChange,
  exams,
  loading,
  style,
}: {
  value?: string;
  onChange: (id: string) => void;
  exams: Exam[];
  loading?: boolean;
  style?: React.CSSProperties;
}) {
  const sorted = sortExams(exams);
  return (
    <Select
      showSearch
      loading={loading}
      value={value}
      onChange={onChange}
      placeholder="选择考试"
      style={{ minWidth: 300, ...style }}
      optionFilterProp="label"
      options={sorted.map((e) => ({
        value: e.id,
        label: `${e.name}（${EXAM_STATUS_LABEL[e.status] ?? e.status} · ${e.completed_count}/${e.candidate_count}）`,
      }))}
    />
  );
}

/** 页面无任何考试时的统一引导。 */
export function NoExamHint() {
  return (
    <EmptyHint text="暂无考试数据：请先在「考试管理」中创建并发布一场考试，统计看板将自动出现数据。" />
  );
}
