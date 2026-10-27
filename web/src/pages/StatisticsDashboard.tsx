/**
 * 统计看板 — HR 全景（对齐 UIdesign 评审稿 04 屏）。
 *
 * spec（页面层级树：统计看板 / HR 全景看板页）要求：
 *   顶部考试选择器；概览卡片（应考 / 已考 / 未考、平均分、通过率、最高最低分）；
 *   图表区（章节正确率排行、部门平均分排行）；
 *   明细表（按部门可展开人员、按个人、按题目）；
 *   导出 Excel 与合规报告 PDF。
 *
 * 数据来自 GET /statistics/exams/{id}/dashboard —— 一次请求拿齐概览与四张明细，
 * 既减少往返，也保证同一时刻各表口径一致；数据范围由后端按角色收口
 * （部门管理者只会拿到本部门，返回体里的 scope 会标注）。
 *
 * 导出走后端 openpyxl / reportlab 接口，内容与看板一致。
 */

import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Alert, Button, Empty, Skeleton, Table, Tabs, Tag, Tooltip, message } from "antd";
import { DownloadOutlined, FilePdfOutlined, ReloadOutlined } from "@ant-design/icons";

import { useExamDashboard } from "../api/hooks";
import { useAuthStore } from "../store/auth";
import { exportExamExcel, exportExamPdf } from "../api/export";
import { ExamSelector, NoExamHint, sortExams, useExamChoices } from "../components/ExamSelector";
import {
  BarChart,
  KpiCard,
  KpiGrid,
  PageHeader,
  Panel,
  RankBars,
} from "../components/ui";
import {
  EXAM_STATUS_LABEL,
  QUESTION_TYPE_COLOR,
  QUESTION_TYPE_LABEL,
  type ChapterStatRow,
  type DepartmentStatRow,
  type PersonStatRow,
  type QuestionStatRow,
} from "../api/types";
import {
  completionRate,
  errMsg,
  fmtDateTime,
  fmtDuration,
  fmtPercent,
  fmtScore,
} from "../utils/format";

/** 正确率 → 颜色（评审稿阈值：<60 红、<80 琥珀、其余青） */
function rateColor(rate: number): string {
  if (rate < 60) return "var(--red)";
  if (rate < 80) return "var(--amber)";
  return "var(--cyan)";
}

/** 分数 → 颜色 */
function scoreColor(score: number): string {
  if (score < 60) return "var(--red-deep)";
  if (score < 80) return "var(--amber-deep)";
  return "var(--green-deep)";
}

export default function StatisticsDashboard() {
  const navigate = useNavigate();
  // 角色视图隔离（spec M6）：合规/高层关注制度传达与题目质量，
  // 不看「按个人」的成绩明细，避免把个人成绩暴露给非 HR 角色。
  const role = useAuthStore((st) => st.user?.role);
  const isCompliance = role === "compliance";
  const [searchParams, setSearchParams] = useSearchParams();
  const { exams, isLoading: examsLoading } = useExamChoices();
  const sorted = useMemo(() => sortExams(exams), [exams]);

  const urlExamId = searchParams.get("examId") ?? undefined;
  const examId = urlExamId ?? sorted[0]?.id;

  const [exporting, setExporting] = useState<"excel" | "pdf" | null>(null);

  useEffect(() => {
    if (!urlExamId && sorted[0]?.id) {
      const next = new URLSearchParams(searchParams);
      next.set("examId", sorted[0].id);
      setSearchParams(next, { replace: true });
    }
  }, [urlExamId, sorted, searchParams, setSearchParams]);

  const dashboard = useExamDashboard(examId);

  const ov = dashboard.data?.overview;
  const chapterRows: ChapterStatRow[] = dashboard.data?.chapter_stats ?? [];
  const deptRows: DepartmentStatRow[] = dashboard.data?.dept_stats ?? [];
  const personRows: PersonStatRow[] = dashboard.data?.person_stats ?? [];
  const questionRows: QuestionStatRow[] = dashboard.data?.question_stats ?? [];
  const scope = dashboard.data?.scope;

  /** 部门 → 人员（按部门表的展开行用） */
  const personsByDept = useMemo(() => {
    const map = new Map<string, PersonStatRow[]>();
    for (const p of personRows) {
      const key = p.dept_name || "未分配部门";
      const arr = map.get(key) ?? [];
      arr.push(p);
      map.set(key, arr);
    }
    return map;
  }, [personRows]);

  const hasSubmissions = (ov?.completed_count ?? 0) > 0;

  const doExport = async (kind: "excel" | "pdf") => {
    if (!examId) return;
    setExporting(kind);
    try {
      const name = kind === "excel" ? await exportExamExcel(examId) : await exportExamPdf(examId);
      message.success(`已导出：${name}`);
    } catch (err) {
      message.error(`导出失败：${errMsg(err)}`);
    } finally {
      setExporting(null);
    }
  };

  if (!examsLoading && sorted.length === 0) {
    return (
      <div>
        <PageHeader eyebrow="统计看板 / HR 全景" title="考试统计" />
        <Panel>
          <NoExamHint />
        </Panel>
      </div>
    );
  }

  return (
    <div className="stack">
      <PageHeader
        eyebrow="统计看板 / HR 全景"
        title={ov?.exam_name ?? "考试统计"}
        description={
          ov
            ? `考试状态：${
                ov.exam_status ? (EXAM_STATUS_LABEL[ov.exam_status] ?? ov.exam_status) : "—"
              } · 数据范围：${scope === "department" ? "本部门（已按权限收口）" : "全公司"} · 生成时间 ${
                dashboard.data?.generated_at ? fmtDateTime(dashboard.data.generated_at) : "—"
              }`
            : undefined
        }
        extra={
          <>
            <ExamSelector
              exams={sorted}
              loading={examsLoading}
              value={examId}
              onChange={(id) => {
                const next = new URLSearchParams(searchParams);
                next.set("examId", id);
                setSearchParams(next);
              }}
            />
            <Button
              icon={<ReloadOutlined />}
              loading={dashboard.isFetching}
              onClick={() => dashboard.refetch()}
            >
              刷新
            </Button>
            <Button
              icon={<DownloadOutlined />}
              loading={exporting === "excel"}
              disabled={!ov}
              onClick={() => doExport("excel")}
            >
              导出 Excel
            </Button>
            <Button
              type="primary"
              icon={<FilePdfOutlined />}
              loading={exporting === "pdf"}
              disabled={!ov}
              onClick={() => doExport("pdf")}
            >
              导出合规报告 PDF
            </Button>
          </>
        }
      />

      {dashboard.isError && (
        <Alert
          type="error"
          showIcon
          message="统计看板加载失败"
          description={errMsg(dashboard.error, "请确认考试数据存在，且当前角色有该考试的数据权限。")}
          action={
            <Button size="small" onClick={() => dashboard.refetch()}>
              重试
            </Button>
          }
        />
      )}

      {dashboard.isLoading ? (
        <Panel>
          <Skeleton active paragraph={{ rows: 3 }} />
        </Panel>
      ) : (
        <KpiGrid columns={6}>
          <KpiCard
            label="应考人数"
            value={ov?.candidate_count ?? 0}
            tone="blue"
            trend={scope === "department" ? "本部门口径" : "全公司口径"}
          />
          <KpiCard
            label="已完成人数"
            value={ov?.completed_count ?? 0}
            tone="green"
            trend={
              ov
                ? `完成率 ${fmtPercent(completionRate(ov.completed_count, ov.candidate_count))}`
                : undefined
            }
          />
          <KpiCard
            label="未考人数"
            value={ov?.not_started_count ?? 0}
            tone={ov?.not_started_count ? "amber" : "cyan"}
            trend={ov?.not_started_count ? "需发送提醒" : "已全部完成"}
            trendTone={ov?.not_started_count ? "warn" : "up"}
          />
          <KpiCard
            label="平均分"
            value={ov ? fmtScore(ov.average_score) : "—"}
            tone="purple"
            trend={
              ov?.max_score
                ? `满分 ${fmtScore(ov.max_score, 0)} · 平均得分率 ${
                    ov.average_score_percent !== undefined
                      ? fmtPercent(ov.average_score_percent)
                      : "—"
                  }`
                : "满分 100"
            }
          />
          <KpiCard
            label="通过率"
            value={ov ? fmtPercent(ov.pass_rate) : "—"}
            tone={ov && ov.pass_rate < 80 ? "amber" : "green"}
            trend={ov?.pass_score ? `得分率 ≥ ${ov.pass_score}%` : "得分率 ≥ 60%"}
            trendTone={ov && ov.pass_rate < 80 ? "warn" : "up"}
          />
          <KpiCard
            label="最高 / 最低分"
            value={ov ? `${fmtScore(ov.highest_score, 0)} / ${fmtScore(ov.lowest_score, 0)}` : "—"}
            tone={ov && ov.lowest_score < 60 ? "red" : "cyan"}
            trend={ov && ov.failed_count ? `${ov.failed_count} 人未通过` : "无未通过答卷"}
            trendTone={ov && ov.failed_count ? "danger" : "up"}
          />
        </KpiGrid>
      )}

      {!dashboard.isLoading && !hasSubmissions && (
        <Alert
          type="info"
          showIcon
          message="本场考试尚无人提交答卷"
          description="章节 / 部门 / 个人 / 题目明细都依赖考生作答数据；考生完成并提交后，这里会自动出现统计结果。"
        />
      )}

      <div className="grid-2 even">
        <Panel title="章节正确率排行" extra="按正确率升序，红色为最薄弱">
          {dashboard.isLoading ? (
            <Skeleton active paragraph={{ rows: 4 }} />
          ) : (
            <RankBars
              colorFor={(d) => rateColor(d.value)}
              data={[...chapterRows]
                .map((c) => ({
                  label: `${c.chapter_no ?? "—"}${c.section_no ? ` ${c.section_no}` : ""}`,
                  value: Math.round(c.correct_rate),
                  hint: `作答 ${c.correct_answers}/${c.total_answers}，正确率 ${fmtPercent(c.correct_rate)}`,
                }))
                .sort((a, b) => a.value - b.value)}
            />
          )}
        </Panel>

        <Panel title="部门平均分" extra="满分 100">
          {dashboard.isLoading ? (
            <Skeleton active paragraph={{ rows: 4 }} />
          ) : (
            <BarChart
              data={[...deptRows]
                .sort((a, b) => b.average_score - a.average_score)
                .map((d) => ({
                  label: d.dept_name.slice(0, 4),
                  value: Math.round(d.average_score),
                  hint: `${d.dept_name}：平均 ${fmtScore(d.average_score)}，已考 ${d.completed_count}/${d.candidate_count}，通过率 ${fmtPercent(d.pass_rate)}`,
                }))}
              colorFor={(d) =>
                d.value < 60
                  ? "linear-gradient(180deg,#ee9198,#e15d65)"
                  : d.value < 80
                    ? "linear-gradient(180deg,#f2c26a,#e8a63d)"
                    : "linear-gradient(180deg,#6ea1ff,#2f6bff)"
              }
            />
          )}
        </Panel>
      </div>

      <Panel
        title="明细数据"
        extra={
          isCompliance
            ? "合规视角：仅展示题目维度（个人 / 部门成绩明细属 HR 与部门管理者职权）"
            : "按部门可展开人员；个人名次按得分降序"
        }
      >
        <Tabs
          defaultActiveKey={isCompliance ? "question" : "dept"}
          items={[
            {
              key: "dept",
              label: `按部门（${deptRows.length}）`,
              children: (
                <Table
                  rowKey={(row) => row.dept_id ?? row.dept_name}
                  size="small"
                  loading={dashboard.isLoading}
                  dataSource={deptRows}
                  pagination={false}
                  locale={{ emptyText: <Empty description="暂无可统计的部门数据" /> }}
                  expandable={{
                    rowExpandable: (row) => (personsByDept.get(row.dept_name)?.length ?? 0) > 0,
                    expandedRowRender: (row) => (
                      <Table
                        rowKey="user_id"
                        size="small"
                        pagination={false}
                        dataSource={personsByDept.get(row.dept_name) ?? []}
                        columns={[
                          { title: "姓名", dataIndex: "name", width: 140 },
                          {
                            title: "得分",
                            dataIndex: "score",
                            width: 100,
                            render: (v: number) => (
                              <span style={{ fontWeight: 700, color: scoreColor(v) }}>
                                {fmtScore(v)}
                              </span>
                            ),
                          },
                          {
                            title: "答对 / 答错",
                            width: 120,
                            render: (_: unknown, p: PersonStatRow) =>
                              `${p.correct_count} / ${p.wrong_count}`,
                          },
                          {
                            title: "用时",
                            dataIndex: "duration_seconds",
                            width: 110,
                            render: (v: number) => fmtDuration(v),
                          },
                          {
                            title: "提交时间",
                            dataIndex: "submitted_at",
                            width: 170,
                            render: (v: string) => fmtDateTime(v),
                          },
                        ]}
                      />
                    ),
                  }}
                  columns={[
                    { title: "部门", dataIndex: "dept_name" },
                    { title: "应考", dataIndex: "candidate_count", width: 76 },
                    { title: "已考", dataIndex: "completed_count", width: 76 },
                    {
                      title: "未考",
                      dataIndex: "not_started_count",
                      width: 76,
                      render: (v: number) => (
                        <span style={{ color: v > 0 ? "var(--amber-deep)" : undefined }}>{v}</span>
                      ),
                    },
                    {
                      title: "完成率",
                      width: 110,
                      sorter: (a, b) =>
                        completionRate(a.completed_count, a.candidate_count) -
                        completionRate(b.completed_count, b.candidate_count),
                      render: (_: unknown, row: DepartmentStatRow) => {
                        const rate = completionRate(row.completed_count, row.candidate_count);
                        return (
                          <span style={{ color: rate < 80 ? "var(--amber-deep)" : undefined }}>
                            {fmtPercent(rate)}
                          </span>
                        );
                      },
                    },
                    {
                      title: "平均分",
                      dataIndex: "average_score",
                      width: 96,
                      sorter: (a, b) => a.average_score - b.average_score,
                      render: (v: number) => (
                        <span style={{ fontWeight: 700, color: scoreColor(v) }}>{fmtScore(v)}</span>
                      ),
                    },
                    {
                      title: "通过率",
                      dataIndex: "pass_rate",
                      width: 96,
                      sorter: (a, b) => a.pass_rate - b.pass_rate,
                      render: (v: number) => fmtPercent(v),
                    },
                    {
                      title: "状态",
                      width: 100,
                      render: (_: unknown, row: DepartmentStatRow) => {
                        const rate = completionRate(row.completed_count, row.candidate_count);
                        if (row.average_score >= 80 && rate >= 80) return <Tag color="green">良好</Tag>;
                        if (row.average_score < 60 || rate < 60) return <Tag color="red">需关注</Tag>;
                        return <Tag color="gold">一般</Tag>;
                      },
                    },
                  ]}
                />
              ),
            },
            {
              key: "person",
              label: `按个人（${personRows.length}）`,
              children: (
                <Table
                  rowKey="user_id"
                  size="small"
                  loading={dashboard.isLoading}
                  dataSource={personRows}
                  pagination={{ pageSize: 10, showSizeChanger: false }}
                  locale={{ emptyText: <Empty description="暂无个人答卷数据" /> }}
                  columns={[
                    { title: "名次", dataIndex: "rank", width: 70 },
                    { title: "姓名", dataIndex: "name", width: 120 },
                    { title: "部门", dataIndex: "dept_name", width: 150 },
                    {
                      title: "得分",
                      dataIndex: "score",
                      width: 150,
                      sorter: (a, b) => a.score - b.score,
                      render: (v: number, row: PersonStatRow) => (
                        <span style={{ fontWeight: 700, color: row.is_passed ? "var(--green-deep)" : "var(--red-deep)" }}>
                          {fmtScore(v)}
                          {row.score_percent !== undefined && (
                            <span className="muted" style={{ marginLeft: 6, fontWeight: 400 }}>
                              （{fmtPercent(row.score_percent)}）
                            </span>
                          )}
                          {!row.is_passed && (
                            <Tag color="red" style={{ marginLeft: 6 }}>
                              未通过
                            </Tag>
                          )}
                        </span>
                      ),
                    },
                    {
                      title: "答对 / 答错",
                      width: 110,
                      render: (_: unknown, row: PersonStatRow) =>
                        `${row.correct_count} / ${row.wrong_count}`,
                    },
                    {
                      title: "用时",
                      dataIndex: "duration_seconds",
                      width: 110,
                      sorter: (a, b) => a.duration_seconds - b.duration_seconds,
                      render: (v: number) => fmtDuration(v),
                    },
                    {
                      title: "提交时间",
                      dataIndex: "submitted_at",
                      width: 170,
                      sorter: (a, b) => (a.submitted_at ?? "").localeCompare(b.submitted_at ?? ""),
                      render: (v: string) => fmtDateTime(v),
                    },
                  ]}
                />
              ),
            },
            {
              key: "question",
              label: `按题目（${questionRows.length}）`,
              children: (
                <Table
                  rowKey="question_id"
                  size="small"
                  loading={dashboard.isLoading}
                  dataSource={questionRows}
                  pagination={{ pageSize: 10, showSizeChanger: false }}
                  locale={{ emptyText: <Empty description="暂无题目作答数据" /> }}
                  columns={[
                    { title: "题号", dataIndex: "order", width: 64 },
                    {
                      title: "题型",
                      dataIndex: "question_type",
                      width: 96,
                      render: (t: keyof typeof QUESTION_TYPE_LABEL) => (
                        <Tag color={QUESTION_TYPE_COLOR[t]}>{QUESTION_TYPE_LABEL[t] ?? t}</Tag>
                      ),
                    },
                    {
                      title: "题目内容",
                      dataIndex: "content",
                      ellipsis: { showTitle: false },
                      render: (v: string) => (
                        <Tooltip title={v}>
                          <span>{v}</span>
                        </Tooltip>
                      ),
                    },
                    {
                      title: "来源",
                      width: 160,
                      render: (_: unknown, row: QuestionStatRow) =>
                        `${row.chapter_no ?? "—"} ${row.clause_no ?? ""}`.trim(),
                    },
                    { title: "作答次数", dataIndex: "total_answers", width: 96 },
                    {
                      title: "正确率",
                      dataIndex: "correct_rate",
                      width: 110,
                      sorter: (a, b) => a.correct_rate - b.correct_rate,
                      render: (v: number, row: QuestionStatRow) => (
                        <span
                          style={{
                            fontWeight: 600,
                            color: row.total_answers === 0 ? "var(--muted)" : rateColor(v),
                          }}
                        >
                          {row.total_answers === 0 ? "—" : fmtPercent(v)}
                        </span>
                      ),
                    },
                    {
                      title: "需复查",
                      dataIndex: "needs_review",
                      width: 100,
                      render: (v: boolean) => (v ? <Tag color="orange">建议复查</Tag> : "—"),
                    },
                    {
                      title: "操作",
                      width: 100,
                      render: (_: unknown, row: QuestionStatRow) => (
                        <Button
                          size="small"
                          type="link"
                          onClick={() => navigate(`/questions/${row.question_id}`)}
                        >
                          查看题目
                        </Button>
                      ),
                    },
                  ]}
                />
              ),
            },
          ]
            // 角色视图隔离：合规/高层不看个人与部门成绩明细
            .filter((tab) => !isCompliance || tab.key === "question")}
        />
      </Panel>
    </div>
  );
}
