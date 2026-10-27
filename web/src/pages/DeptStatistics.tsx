/**
 * 部门统计页【部门管理者】— 路由 /statistics/dept（仅 dept_manager 可见）
 *
 * 对应 spec「页面层级树与核心服务模块.md」第 149-153 行：
 *   考试选择器 / 概览卡片（部门应考·已考·平均分）/ 薄弱章节高亮 / 人员明细列表
 *
 * 数据全部来自 S6 统计接口（web/src/api/hooks.ts），页面不产出任何本地假数据：
 *   overview   → 考试全景（通过率等「考试级」指标，后端对 dept_manager 开放）
 *   by-dept    → 后端已按 dept_manager 的所属部门过滤数据范围，正常只有本部门一行
 *   by-chapter → 章节 / 条款级正确率（后端为每题一行）
 *   by-person  → 已提交答卷的人员明细
 *
 * 空态约定：考试列表为空 → NoExamHint；本部门统计行为空 → EmptyHint「本部门暂无已提交答卷」。
 */

import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Alert, Button, Empty, Input, Skeleton, Table } from "antd";
import {
  CheckCircleOutlined,
  DownloadOutlined,
  FieldTimeOutlined,
  ReloadOutlined,
  RiseOutlined,
  TeamOutlined,
  TrophyOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";

import {
  useChapterStats,
  useDeptStats,
  useExamOptions,
  useExamOverview,
  usePersonStats,
} from "../api/hooks";
import { EXAM_STATUS_LABEL, type ChapterStatRow, type PersonStatRow } from "../api/types";
import { ExamSelector, NoExamHint, sortExams, useExamChoices } from "../components/ExamSelector";
import {
  EmptyHint,
  KpiCard,
  KpiGrid,
  PageHeader,
  Panel,
  ProgressRows,
  RankBars,
  type BarDatum,
} from "../components/ui";
import {
  completionRate,
  downloadCsv,
  errMsg,
  fmtDateTime,
  fmtDuration,
  fmtPercent,
  fmtScore,
} from "../utils/format";

const PASS_SCORE = 60;

/** 薄弱程度配色：全部取自 app.css 的设计令牌（= 评审稿 <60 红 / <80 琥珀 / 其余青）。 */
function rateColor(rate: number): string {
  if (rate < PASS_SCORE) return "var(--red)";
  if (rate < 80) return "var(--amber)";
  return "var(--cyan)";
}

/**
 * 章节条目标签。
 *
 * 后端 /by-chapter 的行结构是：chapter_no / section_no / total_answers /
 * correct_answers / correct_rate（同一章下按节拆分），没有 title 字段，
 * 故用「章 节」拼接，保证同名章节也能区分。
 */
function chapterLabel(row: ChapterStatRow): string {
  const parts = [row.chapter_no, row.section_no].filter(
    (v): v is string => typeof v === "string" && v.trim() !== "",
  );
  return parts.length ? parts.join(" · ") : "未标注章节";
}

function chapterHint(row: ChapterStatRow, label: string): string {
  return `${label}：正确率 ${fmtPercent(row.correct_rate)}（作答 ${row.correct_answers}/${row.total_answers}）`;
}

/** 未取到数值时统一显示「—」，避免出现 0 与「无数据」混淆。 */
function dash(v: number | undefined, render: (n: number) => string = String): string {
  if (v === undefined || v === null || Number.isNaN(v)) return "—";
  return render(v);
}

/** 统一的三态渲染：加载中（Skeleton）/ 失败（Alert + 重试）/ 正常。 */
function AsyncSection({
  isLoading,
  isError,
  error,
  onRetry,
  rows = 4,
  children,
}: {
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  onRetry: () => void;
  rows?: number;
  children: React.ReactNode;
}) {
  if (isLoading) {
    return (
      <div className="app-skeleton">
        <Skeleton active title={false} paragraph={{ rows }} />
      </div>
    );
  }
  if (isError) {
    return (
      <Alert
        type="error"
        showIcon
        message="数据加载失败"
        description={errMsg(error, "统计接口请求失败，请稍后重试")}
        action={
          <Button size="small" icon={<ReloadOutlined />} onClick={onRetry}>
            重试
          </Button>
        }
      />
    );
  }
  return <>{children}</>;
}

export default function DeptStatistics() {
  const [searchParams, setSearchParams] = useSearchParams();
  const urlExamId = searchParams.get("examId") ?? "";

  // 考试下拉：useExamChoices 提供列表，同一 query key 的 useExamOptions 提供 isError/refetch
  //（React Query 按 queryKey 去重，不会重复发请求）。
  const { exams, isLoading: examsLoading } = useExamChoices();
  const examOptions = useExamOptions();

  const orderedExams = useMemo(() => sortExams(exams), [exams]);
  const firstExamId = orderedExams[0]?.id ?? "";

  // URL 是选中项的唯一来源；URL 缺失或考试已不存在时回落到列表首项并写回 URL
  const selected = orderedExams.find((e) => e.id === urlExamId);
  const examId = selected?.id ?? "";
  const fallbackExamId = examId || (examsLoading ? "" : firstExamId);

  useEffect(() => {
    if (!fallbackExamId || fallbackExamId === urlExamId) return;
    setSearchParams({ examId: fallbackExamId }, { replace: true });
  }, [fallbackExamId, urlExamId, setSearchParams]);

  const handleExamChange = (id: string) => setSearchParams({ examId: id });

  // ── S6 统计接口（examId 为空时 hook 内部 enabled=false，不会发请求）──
  const {
    data: overview,
    isLoading: overviewLoading,
    isError: overviewFailed,
    error: overviewError,
    refetch: refetchOverview,
  } = useExamOverview(examId);

  const {
    data: deptData,
    isLoading: deptLoading,
    isError: deptFailed,
    error: deptError,
    refetch: refetchDept,
  } = useDeptStats(examId);

  const {
    data: chapterData,
    isLoading: chapterLoading,
    isError: chapterFailed,
    error: chapterError,
    refetch: refetchChapter,
  } = useChapterStats(examId);

  const {
    data: personData,
    isLoading: personLoading,
    isError: personFailed,
    error: personError,
    refetch: refetchPerson,
  } = usePersonStats(examId);

  const [keyword, setKeyword] = useState("");

  // 后端已按部门管理者范围过滤，正常只有本部门一行
  const deptRow = deptData?.departments?.[0];
  const hasDeptRow = Boolean(deptRow);

  // 部门行缺失时（当前账号未绑定部门 / 本部门无答卷）退化为「考试整体口径」，并在卡片上注明
  const totalCount = deptRow ? deptRow.candidate_count : overview?.candidate_count;
  const doneCount = deptRow ? deptRow.completed_count : overview?.completed_count;
  const notDoneCount =
    deptRow && deptRow.candidate_count !== undefined && deptRow.completed_count !== undefined
      ? Math.max(0, deptRow.candidate_count - deptRow.completed_count)
      : overview?.not_started_count;
  const avgScore = deptRow ? deptRow.average_score : overview?.average_score;
  // 通过率优先取本部门口径，缺失时退回考试整体口径
  const passRate = deptRow ? deptRow.pass_rate : overview?.pass_rate;

  const doneRate = completionRate(doneCount ?? 0, totalCount ?? 0);
  const progressColor =
    doneRate >= 100 ? "var(--cyan)" : doneRate >= 60 ? "var(--blue)" : "var(--amber)";

  const chapterRows = useMemo(
    () => [...(chapterData?.chapters ?? [])].sort((a, b) => a.correct_rate - b.correct_rate),
    [chapterData],
  );
  const chapterBars: BarDatum[] = chapterRows.map((row) => {
    const label = chapterLabel(row);
    return { label, value: row.correct_rate, hint: chapterHint(row, label) };
  });

  const persons = useMemo(() => personData?.persons ?? [], [personData]);
  const filteredPersons = useMemo(() => {
    const kw = keyword.trim();
    if (!kw) return persons;
    return persons.filter((p) => (p.name ?? "").includes(kw));
  }, [persons, keyword]);

  const examTitle = selected?.name ?? overview?.exam_name ?? "部门统计";
  const description = selected
    ? `${EXAM_STATUS_LABEL[selected.status] ?? selected.status} · 数据范围：仅本部门${
        deptRow?.dept_name ? `（${deptRow.dept_name}）` : ""
      }`
    : undefined;

  const columns: ColumnsType<PersonStatRow> = [
    {
      title: "姓名",
      dataIndex: "name",
      key: "name",
      width: 140,
      render: (v: string) => v || "—",
    },
    {
      title: "部门",
      dataIndex: "dept_name",
      key: "dept_name",
      width: 180,
      render: (v: string) => v || "—",
    },
    {
      title: "得分",
      dataIndex: "score",
      key: "score",
      width: 110,
      sorter: (a, b) => a.score - b.score,
      render: (v: number) => (
        <span
          style={{
            fontWeight: 700,
            color: v >= PASS_SCORE ? "var(--green-deep)" : "var(--red-deep)",
          }}
        >
          {fmtScore(v)}
        </span>
      ),
    },
    {
      title: "用时",
      dataIndex: "duration_seconds",
      key: "duration_seconds",
      width: 130,
      render: (v: number) => fmtDuration(v),
    },
    {
      title: "提交时间",
      dataIndex: "submitted_at",
      key: "submitted_at",
      width: 170,
      render: (v: string) => fmtDateTime(v),
    },
  ];

  const handleExport = () => {
    const safeName = examTitle.replace(/[\\/:*?"<>|\s]+/g, "-");
    downloadCsv(
      `部门人员成绩-${safeName}`,
      ["姓名", "部门", "得分", "用时(秒)", "提交时间"],
      filteredPersons.map((p) => [
        p.name,
        p.dept_name,
        fmtScore(p.score),
        p.duration_seconds,
        fmtDateTime(p.submitted_at),
      ]),
    );
  };

  // ── 考试列表：加载中 / 失败 / 无考试 ────────────────────────────────
  const noExam = !examsLoading && !examOptions.isError && exams.length === 0;

  return (
    <div className="stack">
      <PageHeader
        eyebrow="统计看板 / 部门统计"
        title={examTitle}
        description={description}
        extra={
          <ExamSelector
            value={examId || undefined}
            onChange={handleExamChange}
            exams={exams}
            loading={examsLoading}
          />
        }
      />

      {examOptions.isError ? (
        <Alert
          type="error"
          showIcon
          message="考试列表加载失败"
          description={errMsg(examOptions.error, "无法获取考试列表")}
          action={
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={() => examOptions.refetch()}
            >
              重试
            </Button>
          }
        />
      ) : null}

      {noExam ? (
        <Panel>
          <NoExamHint />
        </Panel>
      ) : null}

      {examId ? (
        <>
          {hasDeptRow ? null : (
            <Alert
              type="warning"
              showIcon
              message="未取到本部门统计行"
              description="统计接口 /statistics/exams/{id}/by-dept 会按部门管理者的所属部门过滤数据：若当前账号未绑定部门、或本部门尚无已提交答卷，将返回空数组。此时概览卡片展示的是该场考试的“整体口径”，人员明细为空属真实数据范围。"
            />
          )}

          {overviewFailed ? (
            <Alert
              type="error"
              showIcon
              message="考试概览加载失败"
              description={errMsg(overviewError, "无法获取考试概览")}
              action={
                <Button size="small" icon={<ReloadOutlined />} onClick={() => refetchOverview()}>
                  重试
                </Button>
              }
            />
          ) : null}

          {deptFailed ? (
            <Alert
              type="error"
              showIcon
              message="部门统计加载失败"
              description={errMsg(deptError, "无法获取部门统计")}
              action={
                <Button size="small" icon={<ReloadOutlined />} onClick={() => refetchDept()}>
                  重试
                </Button>
              }
            />
          ) : null}

          {/* 概览卡片：部门应考 / 已完成 / 未完成 / 平均分 / 通过率 */}
          <KpiGrid columns={5}>
            <KpiCard
              label="部门应考人数"
              value={dash(totalCount)}
              icon={<TeamOutlined />}
              tone="blue"
              loading={overviewLoading || deptLoading}
              trend={hasDeptRow ? deptRow?.dept_name || "本部门" : "考试整体口径"}
              trendTone={hasDeptRow ? "up" : "warn"}
            />
            <KpiCard
              label="已完成"
              value={dash(doneCount)}
              icon={<CheckCircleOutlined />}
              tone="cyan"
              loading={overviewLoading || deptLoading}
              trend={`完成率 ${fmtPercent(completionRate(doneCount ?? 0, totalCount ?? 0))}`}
            />
            <KpiCard
              label="未完成"
              value={dash(notDoneCount)}
              icon={<FieldTimeOutlined />}
              tone="amber"
              trendTone={(notDoneCount ?? 0) > 0 ? "warn" : "up"}
              loading={overviewLoading || deptLoading}
              trend={(notDoneCount ?? 0) > 0 ? "需提醒未考人员" : "本部门已全部完成"}
            />
            <KpiCard
              label="平均分"
              value={dash(avgScore, (n) => fmtScore(n))}
              icon={<TrophyOutlined />}
              tone="purple"
              loading={overviewLoading || deptLoading}
              trend={`${PASS_SCORE} 分为及格线`}
            />
            <KpiCard
              label="通过率"
              value={dash(passRate, (n) => fmtPercent(n))}
              icon={<RiseOutlined />}
              tone="green"
              loading={overviewLoading}
              trend={hasDeptRow ? "本部门口径" : `≥ ${PASS_SCORE} 分视为通过`}
            />
          </KpiGrid>

          <div className="grid-2">
            <Panel title="本部门考试完成情况" extra={hasDeptRow ? "已完成 / 应考" : undefined}>
              <AsyncSection
                isLoading={deptLoading}
                isError={deptFailed}
                error={deptError}
                onRetry={() => refetchDept()}
                rows={2}
              >
                {hasDeptRow ? (
                  <>
                    <ProgressRows
                      rows={[
                        {
                          label: "已完成 / 应考",
                          percent: doneRate,
                          color: progressColor,
                          hint: `已完成 ${doneCount ?? 0} 人 / 应考 ${totalCount ?? 0} 人`,
                        },
                      ]}
                    />
                    <div className="muted" style={{ marginTop: 10 }}>
                      已完成 {doneCount ?? 0} 人 · 应考 {totalCount ?? 0} 人 · 未完成{" "}
                      {notDoneCount ?? 0} 人
                    </div>
                  </>
                ) : (
                  <EmptyHint
                    text="本部门暂无已提交答卷"
                    action={
                      <div className="muted">
                        部门统计接口按部门管理者的所属部门过滤数据范围，未匹配到本部门答卷时返回空数组，
                        无可展示的完成进度。
                      </div>
                    }
                  />
                )}
              </AsyncSection>
            </Panel>

            <Panel title="薄弱章节高亮" extra="按正确率升序，红色为最薄弱">
              <AsyncSection
                isLoading={chapterLoading}
                isError={chapterFailed}
                error={chapterError}
                onRetry={() => refetchChapter()}
                rows={5}
              >
                {chapterBars.length ? (
                  <RankBars data={chapterBars} colorFor={(d) => rateColor(d.value)} />
                ) : (
                  <EmptyHint text="暂无章节作答数据：本场考试尚无已提交的答卷" />
                )}
              </AsyncSection>
            </Panel>
          </div>

          <Panel
            title="本部门人员明细"
            extra={
              <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                <Input.Search
                  placeholder="搜索姓名"
                  allowClear
                  style={{ width: 200 }}
                  value={keyword}
                  onChange={(e) => setKeyword(e.target.value)}
                />
                <Button
                  size="small"
                  icon={<DownloadOutlined />}
                  disabled={!filteredPersons.length}
                  onClick={handleExport}
                >
                  导出 CSV
                </Button>
              </span>
            }
          >
            <AsyncSection
              isLoading={personLoading}
              isError={personFailed}
              error={personError}
              onRetry={() => refetchPerson()}
              rows={6}
            >
              <Table<PersonStatRow>
                rowKey="user_id"
                size="small"
                columns={columns}
                dataSource={filteredPersons}
                pagination={{
                  pageSize: 10,
                  showSizeChanger: false,
                  hideOnSinglePage: true,
                  showTotal: (t) => `共 ${t} 人`,
                }}
                locale={{
                  emptyText: (
                    <Empty
                      image={Empty.PRESENTED_IMAGE_SIMPLE}
                      description={
                        keyword.trim()
                          ? `没有姓名包含「${keyword.trim()}」的人员`
                          : "本部门暂无已提交答卷"
                      }
                    />
                  ),
                }}
              />
            </AsyncSection>
          </Panel>
        </>
      ) : examsLoading ? (
        <Panel>
          <div className="app-skeleton">
            <Skeleton active paragraph={{ rows: 6 }} />
          </div>
        </Panel>
      ) : null}
    </div>
  );
}
