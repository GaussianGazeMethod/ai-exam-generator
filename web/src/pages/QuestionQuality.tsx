/**
 * 题目质量分析页【专家 / HR 管理员】
 * — spec 页面层级树「题目质量分析页【专家】」：
 *   考试选择器；高错误率题目列表（错误率 > 阈值，可配置），每题含题目内容、题型、
 *   错误率、使用次数；点击 → 跳转题目详情页（查看是否需要修改表述）。
 *
 * 数据源：GET /statistics/question-quality
 *   - 传 exam_id → 单场考试的题目质量
 *   - 不传   → 跨全部已发布考试汇总（同一题在多场考试中的作答合并），
 *              这是「题目本身是否有问题」更可靠的判据（单场样本可能太小）
 *   阈值 error_threshold 由后端参与 needs_review 判定，保证「服务端标记」
 *   与页面阈值口径一致（不再前端本地过滤）。
 *
 * 口径提醒（页面上必须显式标注，否则会误导）：
 *   - error_rate 是**错误率**，correct_rate 是**正确率**，两者互补；
 *   - total_answers = 0 表示该题没有任何作答数据（正确率 0 不代表题目差），
 *     页面把「无人作答」与「题目质量差」用不同提示区分开。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Alert,
  Button,
  Empty,
  InputNumber,
  Progress,
  Skeleton,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { DownloadOutlined, ReloadOutlined } from "@ant-design/icons";

import { useQuestionQuality } from "../api/hooks";
import { useExamChoices } from "../components/ExamSelector";
import {
  QUESTION_TYPE_COLOR,
  QUESTION_TYPE_LABEL,
  type QuestionStatRow,
  type QuestionType,
} from "../api/types";
import { EmptyHint, KpiCard, KpiGrid, PageHeader, Panel, RankBars } from "../components/ui";
import { downloadCsv, errMsg, fmtPercent } from "../utils/format";

const DEFAULT_THRESHOLD = 30;
/** 与 app.css 设计令牌同值（--red / --amber / --cyan），页面不新造色值 */
const COLOR_RED = "#e15d65";
const COLOR_AMBER = "#e8a63d";
const COLOR_CYAN = "#13b8a6";

/** ALL_EXAMS 表示「跨全部考试汇总」 */
const ALL_EXAMS = "__all__";

function parseThreshold(raw: string | null): number {
  const n = Number(raw);
  if (raw === null || raw === "" || Number.isNaN(n)) return DEFAULT_THRESHOLD;
  return Math.min(Math.max(n, 0), 100);
}

function contentText(content: string): string {
  return (content ?? "").trim() || "（题干为空）";
}

export default function QuestionQuality() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const threshold = parseThreshold(searchParams.get("threshold"));
  // 默认跨全部考试汇总（题目本身的错误率才可判断），可切换到单场考试
  const examParam = searchParams.get("examId") ?? ALL_EXAMS;
  const singleExam = examParam !== ALL_EXAMS ? examParam : undefined;

  const [exporting, setExporting] = useState(false);

  // 考试下拉用真实名称（后端的 question-quality 只回 exam_ids，无法直接展示）
  const { exams, isLoading: examsLoading } = useExamChoices();
  const examNameOf = useCallback(
    (id: string) => exams.find((e) => e.id === id)?.name ?? `考试 ${id.slice(0, 8)}…`,
    [exams],
  );

  const patchParams = useCallback(
    (patch: Record<string, string>) => {
      const next = new URLSearchParams(searchParams);
      Object.entries(patch).forEach(([k, v]) => (v ? next.set(k, v) : next.delete(k)));
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const {
    data,
    isLoading,
    isFetching,
    isError,
    error,
    refetch,
  } = useQuestionQuality({ exam_id: singleExam, error_threshold: threshold, limit: 200 });

  const questions: QuestionStatRow[] = data?.questions ?? [];
  const rows = useMemo(
    () => [...questions].sort((a, b) => b.error_rate - a.error_rate),
    [questions],
  );

  // 阈值以下但也存在作答数据的题目：用于「接近阈值的题目」提示
  const answeredRows = rows.filter((r) => r.total_answers > 0);
  const noAnswerRows = rows.length > 0 && answeredRows.length === 0;

  const avgCorrect = answeredRows.length
    ? Math.round(
        (answeredRows.reduce((s, r) => s + r.correct_rate, 0) / answeredRows.length) * 10,
      ) / 10
    : null;

  const worst = answeredRows[0];

  const onExport = () => {
    setExporting(true);
    try {
      downloadCsv(
        singleExam ? "题目质量分析-单场考试.csv" : "题目质量分析-全部考试.csv",
        ["题号", "题型", "错误率(%)", "正确率(%)", "作答人数", "使用次数", "需复查", "题目内容"],
        rows.map((r) => [
          r.order,
          QUESTION_TYPE_LABEL[r.question_type] ?? r.question_type,
          r.error_rate.toFixed(1),
          r.correct_rate.toFixed(1),
          r.total_answers,
          r.usage_count,
          r.needs_review ? "是" : "否",
          r.content,
        ]),
      );
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="stack">
      <PageHeader
        eyebrow="统计看板 / 题目质量分析"
        title="题目质量分析"
        description={
          <span>
            错误率 = 100 − 正确率；当前口径：
            {singleExam ? "单场考试" : "跨全部已发布考试汇总（同一题作答合并）"}，阈值 {threshold}%，
            共扫描 {data?.total_scanned ?? 0} 题。
          </span>
        }
        extra={
          <>
            <Space size={6}>
              <span className="muted">分析范围</span>
              <select
                value={examParam}
                onChange={(e) => patchParams({ examId: e.target.value })}
                disabled={examsLoading}
                style={{
                  height: 34,
                  borderRadius: 10,
                  border: "1px solid var(--line)",
                  padding: "0 8px",
                  minWidth: 220,
                  fontSize: 13,
                  background: "#fff",
                }}
              >
                <option value={ALL_EXAMS}>全部考试（汇总）</option>
                {(data?.exam_ids ?? []).map((id) => (
                  <option key={id} value={id}>
                    {examNameOf(id)}
                  </option>
                ))}
              </select>
            </Space>
            <Space size={6}>
              <span className="muted">错误率阈值</span>
              <InputNumber
                min={0}
                max={100}
                step={1}
                value={threshold}
                formatter={(v) => `${v ?? ""}%`}
                parser={(v) => Number((v ?? "").replace("%", ""))}
                onChange={(v) =>
                  patchParams({
                    threshold: String(
                      Math.min(
                        Math.max(
                          typeof v === "number" && !Number.isNaN(v) ? v : DEFAULT_THRESHOLD,
                          0,
                        ),
                        100,
                      ),
                    ),
                  })
                }
                style={{ width: 92 }}
              />
            </Space>
            <Button icon={<ReloadOutlined />} onClick={() => refetch()} loading={isFetching}>
              刷新
            </Button>
            <Button
              icon={<DownloadOutlined />}
              onClick={onExport}
              loading={exporting}
              disabled={!rows.length}
            >
              导出 CSV
            </Button>
          </>
        }
      />

      {isError && (
        <Alert
          type="error"
          showIcon
          message="题目质量数据加载失败"
          description={errMsg(error, "请确认后端服务与考试数据是否正常")}
          action={
            <Button size="small" onClick={() => refetch()}>
              重试
            </Button>
          }
        />
      )}

      {isLoading ? (
        <>
          <KpiGrid columns={4}>
            {[0, 1, 2, 3].map((i) => (
              <div className="kpi" key={i}>
                <Skeleton active paragraph={{ rows: 1 }} title={{ width: "46%" }} />
              </div>
            ))}
          </KpiGrid>
          <Panel title="高错误率题目">
            <Skeleton active paragraph={{ rows: 4 }} />
          </Panel>
        </>
      ) : (
        <>
          <KpiGrid columns={4}>
            <KpiCard
              label="纳入分析题目"
              value={data?.total_scanned ?? 0}
              suffix="题"
              tone="blue"
              trend={singleExam ? "本场考试题目数" : "全部已发布考试的去重题目"}
            />
            <KpiCard
              label={`高错误率题目（≥ ${threshold}%）`}
              value={data?.needs_review_count ?? 0}
              suffix="题"
              tone={(data?.needs_review_count ?? 0) > 0 ? "red" : "green"}
              trend="后端按同一阈值标记，与列表口径一致"
              trendTone={(data?.needs_review_count ?? 0) > 0 ? "danger" : "up"}
            />
            <KpiCard
              label="平均正确率"
              value={avgCorrect === null ? "—" : fmtPercent(avgCorrect)}
              tone="cyan"
              trend={
                answeredRows.length
                  ? `按有作答的 ${answeredRows.length} 题等权平均`
                  : "尚无可用的作答数据"
              }
            />
            <KpiCard
              label="错误率最高题目"
              value={worst ? fmtPercent(worst.error_rate) : "—"}
              tone="amber"
              trend={worst ? contentText(worst.content).slice(0, 22) : "暂无有作答数据的题目"}
              trendTone="warn"
              onClick={worst ? () => navigate(`/questions/${worst.question_id}`) : undefined}
            />
          </KpiGrid>

          {noAnswerRows && (
            <Alert
              type="warning"
              showIcon
              message="当前没有任何作答数据"
              description="所有题目的作答人数均为 0，因此错误率无法判定。这属于「没有做题数据」，不代表题目质量好或坏；请等待考生提交答卷后再分析。"
            />
          )}

          <Panel
            title="高错误率题目"
            extra={`错误率 ≥ ${threshold}%　共 ${rows.length} 题`}
          >
            <Table<QuestionStatRow>
              rowKey="question_id"
              size="small"
              loading={isFetching}
              dataSource={rows}
              pagination={{ pageSize: 10, showTotal: (t) => `共 ${t} 题` }}
              locale={{
                emptyText: (
                  <EmptyHint
                    text={
                      data?.total_scanned
                        ? `没有错误率达到 ${threshold}% 的题目：可下调阈值再观察（已扫描 ${data.total_scanned} 题）。`
                        : "当前范围内没有题目数据：请先在考试管理中完成组卷并发布考试。"
                    }
                  />
                ),
              }}
              columns={[
                { title: "题号", dataIndex: "order", width: 64, align: "center" },
                {
                  title: "题目内容",
                  dataIndex: "content",
                  ellipsis: true,
                  render: (text: string, record: QuestionStatRow) => (
                    <Tooltip title={contentText(text)} placement="topLeft">
                      <Typography.Text
                        style={{ cursor: "pointer" }}
                        onClick={() => navigate(`/questions/${record.question_id}`)}
                      >
                        {contentText(text)}
                      </Typography.Text>
                    </Tooltip>
                  ),
                },
                {
                  title: "题型",
                  dataIndex: "question_type",
                  width: 92,
                  render: (t: QuestionType) => (
                    <Tag color={QUESTION_TYPE_COLOR[t]}>{QUESTION_TYPE_LABEL[t] ?? t}</Tag>
                  ),
                },
                {
                  title: "来源",
                  width: 150,
                  render: (_: unknown, r: QuestionStatRow) =>
                    `${r.chapter_no ?? "—"} ${r.clause_no ?? ""}`.trim(),
                },
                {
                  title: (
                    <Tooltip title="错误率 = 100 − 正确率">
                      <span>错误率</span>
                    </Tooltip>
                  ),
                  dataIndex: "error_rate",
                  width: 180,
                  defaultSortOrder: "descend",
                  sorter: (a, b) => a.error_rate - b.error_rate,
                  render: (v: number, r: QuestionStatRow) => {
                    if (r.total_answers === 0) {
                      return <span className="muted">无作答数据</span>;
                    }
                    const over = v >= threshold;
                    const mid = v >= threshold * 0.6;
                    return (
                      <div>
                        <Typography.Text strong={over} type={over ? "danger" : undefined}>
                          {fmtPercent(v)}
                        </Typography.Text>
                        <Progress
                          percent={Math.min(Math.max(v, 0), 100)}
                          showInfo={false}
                          size="small"
                          strokeColor={over ? COLOR_RED : mid ? COLOR_AMBER : COLOR_CYAN}
                          style={{ marginBottom: 0 }}
                        />
                      </div>
                    );
                  },
                },
                {
                  title: "正确率",
                  dataIndex: "correct_rate",
                  width: 96,
                  sorter: (a, b) => a.correct_rate - b.correct_rate,
                  render: (v: number, r: QuestionStatRow) =>
                    r.total_answers === 0 ? "—" : fmtPercent(v),
                },
                {
                  title: "作答人数",
                  dataIndex: "total_answers",
                  width: 96,
                  sorter: (a, b) => a.total_answers - b.total_answers,
                },
                {
                  title: "使用次数",
                  dataIndex: "usage_count",
                  width: 96,
                  align: "right",
                  sorter: (a, b) => a.usage_count - b.usage_count,
                },
                {
                  title: "需复查",
                  dataIndex: "needs_review",
                  width: 96,
                  render: (v: boolean) => (v ? <Tag color="orange">建议复查</Tag> : "—"),
                },
                {
                  title: "操作",
                  width: 108,
                  render: (_: unknown, record: QuestionStatRow) => (
                    <Button size="small" onClick={() => navigate(`/questions/${record.question_id}`)}>
                      查看题目
                    </Button>
                  ),
                },
              ]}
            />
          </Panel>

          <Panel
            title="错误率 TOP 5"
            extra={`≥ 阈值红色，≥ ${Math.round(threshold * 0.6)}% 琥珀，其余青色`}
          >
            <RankBars
              data={answeredRows.slice(0, 5).map((r) => ({
                label:
                  contentText(r.content).length > 12
                    ? `${contentText(r.content).slice(0, 12)}…`
                    : contentText(r.content),
                value: Math.round(r.error_rate * 10) / 10,
                hint: `错误率 ${fmtPercent(r.error_rate)}｜作答 ${r.total_answers} 次｜${contentText(r.content)}`,
              }))}
              colorFor={(d) =>
                d.value >= threshold
                  ? COLOR_RED
                  : d.value >= threshold * 0.6
                    ? COLOR_AMBER
                    : COLOR_CYAN
              }
            />
            {!answeredRows.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无作答数据" />}
          </Panel>
        </>
      )}
    </div>
  );
}
