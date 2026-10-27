/**
 * 合规全景页【合规 / 高层】— 路由 /statistics/compliance，仅 compliance 角色可见。
 *
 * spec（页面层级树与核心服务模块.md 155-159）：时间范围筛选（季度 / 年度）、
 * 制度传达验证一览表（名称 / 考试场次 / 覆盖率 / 通过率 / 状态标识）、
 * 一键导出合规报告 PDF。
 *
 * 数据来源 GET /statistics/compliance?period=2026Q3|2026：
 *  - 时间范围（本季度 / 本年度 / 全部）作为 period 参数真实下推到后端过滤，
 *    不做本地假筛；「全部」不传 period。
 *  - 后端按「制度」聚合（同一制度的多场考试合并计数），覆盖率 = 已考/应考，
 *    通过率 = 通过/已考，status 为 complete / partial / pending。
 *  - items 无 exam_id（只有 document_id），故「操作」列不编造考试 ID，
 *    统一跳 /exams 查看明细。
 */

import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import dayjs from "dayjs";
import { Alert, Button, Progress, Segmented, Skeleton, Table, Tag } from "antd";
import { FileExcelOutlined, PrinterOutlined, ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";

import { useComplianceOverview } from "../api/hooks";
import { COMPLIANCE_STATUS_COLOR, type ComplianceRow } from "../api/types";
import { EmptyHint, KpiCard, KpiGrid, PageHeader, Panel, RankBars } from "../components/ui";
import { useAuthStore } from "../store/auth";
import { downloadCsv, errMsg, fmtPercent, printAsPdf, todayLabel } from "../utils/format";

// ── 时间范围（真实下推为后端 period 参数） ───────────────────────────────

type RangeKey = "quarter" | "year" | "all";

const RANGE_OPTIONS: { label: string; value: RangeKey }[] = [
  { label: "本季度", value: "quarter" },
  { label: "本年度", value: "year" },
  { label: "全部", value: "all" },
];

const ROUND_CN = ["一", "二", "三", "四"];

/** 当前季度序号（1-4）。 */
function quarterOf(now: dayjs.Dayjs): number {
  return Math.floor(now.month() / 3) + 1;
}

/** period 参数：后端 _period_range 接受 2026Q3 / 2026，全部时不传。 */
function periodArg(key: RangeKey): string | undefined {
  const now = dayjs();
  if (key === "all") return undefined;
  if (key === "year") return String(now.year());
  return `${now.year()}Q${quarterOf(now)}`;
}

/** 报表口径文案，例如「2026 年第三季度（2026-07-01 起截至 2026-09-21）」。 */
function rangeCaption(key: RangeKey): string {
  const now = dayjs();
  if (key === "all") return "全部历史（不限定时间范围）";
  if (key === "year") {
    return `${now.year()} 年度（${now.startOf("year").format("YYYY-MM-DD")} 起截至 ${now.format("YYYY-MM-DD")}）`;
  }
  const q = quarterOf(now) - 1;
  const from = now.month(q * 3).startOf("month");
  return `${now.year()} 年第${ROUND_CN[q]}季度（${from.format("YYYY-MM-DD")} 起截至 ${now.format("YYYY-MM-DD")}）`;
}

/** 通过率阈值：≥80 绿 / 60-80 琥珀 / <60 红。 */
const PASS_GREEN = 80;
const PASS_AMBER = 60;

function passColor(rate: number): string {
  if (rate >= PASS_GREEN) return "var(--green)";
  if (rate >= PASS_AMBER) return "var(--amber)";
  return "var(--red)";
}

/**
 * 接口运行时补充字段。ComplianceRow 已声明 status_label / exam_names 等字段，
 * 此处保留一个「更宽」的别名，便于对未声明字段做安全兜底读取。
 */
type ComplianceRowRuntime = ComplianceRow & {
  status_label?: string;
  exam_names?: string[];
};

/** 状态文案：优先用后端 status_label（如「3人未考」），无则按 status 兜底。 */
function rowStatusText(row: ComplianceRowRuntime): string {
  if (row.status_label) return row.status_label;
  const s = String(row.status ?? "");
  const fallback: Record<string, string> = {
    complete: "已完成",
    partial: "进行中",
    pending: "未开始",
  };
  return fallback[s.toLowerCase()] ?? (s || "—");
}

/** 覆盖率进度条着色与通过率同一套阈值语义。 */
function coverageColor(rate: number): string {
  if (rate >= PASS_GREEN) return "var(--blue)";
  if (rate >= PASS_AMBER) return "var(--amber)";
  return "var(--red)";
}

/** 风险项：传达未完成（status ≠ complete），或通过率低于 80%。 */
function isRisk(row: ComplianceRowRuntime): boolean {
  const s = String(row.status ?? "").toLowerCase();
  const unfinished = s ? s !== "complete" : false;
  return unfinished || row.pass_rate < PASS_GREEN;
}

/** 请求失败的统一兜底：错误提示 + 重试按钮（不展示任何占位数据）。 */
export function ComplianceErrorPanel({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <Panel title="制度传达验证一览">
      <Alert
        type="error"
        showIcon
        message="合规数据加载失败"
        description={errMsg(error, "无法获取合规全景数据，请稍后重试。")}
        action={
          <Button size="small" icon={<ReloadOutlined />} onClick={onRetry}>
            重试
          </Button>
        }
      />
    </Panel>
  );
}

export default function ComplianceOverview() {
  const navigate = useNavigate();
  const { user } = useAuthStore();

  const [range, setRange] = useState<RangeKey>("quarter");
  // 时间范围真实下推为 period 参数（后端 _period_range 按考试发布时间过滤），「全部」不传参
  const period = useMemo(() => periodArg(range), [range]);
  const { data, isLoading, isError, error, refetch } = useComplianceOverview(period);

  const items: ComplianceRowRuntime[] = useMemo(() => data?.items ?? [], [data]);

  const caption = useMemo(() => rangeCaption(range), [range]);
  const generatedAt = todayLabel();
  const author = user?.name ?? "当前登录用户";

  const summary = useMemo(() => {
    const n = items.length;
    const avg = (pick: (r: ComplianceRowRuntime) => number) =>
      n ? items.reduce((s, r) => s + (pick(r) ?? 0), 0) / n : 0;
    return {
      total: n,
      avgCoverage: avg((r) => r.coverage_rate),
      avgPass: avg((r) => r.pass_rate),
      riskCount: items.filter(isRisk).length,
      weakBelow: items.filter((r) => r.pass_rate < PASS_GREEN).length,
      examTotal: items.reduce((s, r) => s + (r.exam_count ?? 0), 0),
    };
  }, [items]);

  const rankData = useMemo(
    () =>
      [...items]
        .sort((a, b) => a.pass_rate - b.pass_rate)
        .slice(0, 5)
        .map((r) => ({
          label: r.document_name,
          value: Math.round(r.pass_rate * 10) / 10,
          hint: `通过率 ${fmtPercent(r.pass_rate)}｜覆盖率 ${fmtPercent(r.coverage_rate)}｜状态 ${rowStatusText(r)}`,
        })),
    [items],
  );

  const handleExportCsv = () => {
    downloadCsv(
      `制度传达合规报告-${caption}-${dayjs().format("YYYYMMDD")}`,
      ["制度/考试名称", "考试场次", "覆盖率(%)", "通过率(%)", "状态标识", "考试明细"],
      items.map((r) => [
        r.document_name,
        r.exam_count,
        r.coverage_rate,
        r.pass_rate,
        rowStatusText(r),
        (r.exam_names ?? []).join(" / "),
      ]),
    );
  };

  const columns: ColumnsType<ComplianceRowRuntime> = [
    {
      title: "制度 / 考试名称",
      dataIndex: "document_name",
      key: "document_name",
      ellipsis: true,
      render: (name: string, row) => (
        <div>
          <div style={{ fontWeight: 600 }}>{name}</div>
          {row.exam_names?.length ? (
            <div className="muted" style={{ marginTop: 2 }} title={row.exam_names.join(" / ")}>
              考试明细：{row.exam_names.join(" / ")}
            </div>
          ) : null}
        </div>
      ),
    },
    {
      title: "考试场次",
      dataIndex: "exam_count",
      key: "exam_count",
      width: 96,
      align: "right",
      sorter: (a, b) => a.exam_count - b.exam_count,
    },
    {
      title: "覆盖率",
      dataIndex: "coverage_rate",
      key: "coverage_rate",
      width: 190,
      sorter: (a, b) => a.coverage_rate - b.coverage_rate,
      render: (v: number) => (
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Progress
            percent={v}
            showInfo={false}
            size="small"
            strokeColor={coverageColor(v)}
            style={{ margin: 0, width: 96 }}
          />
          <span style={{ fontSize: 11, color: "var(--ink-2)" }}>{fmtPercent(v)}</span>
        </div>
      ),
    },
    {
      title: "通过率",
      dataIndex: "pass_rate",
      key: "pass_rate",
      width: 110,
      sorter: (a, b) => a.pass_rate - b.pass_rate,
      render: (v: number) => (
        <span style={{ fontWeight: 700, color: passColor(v) }}>{fmtPercent(v)}</span>
      ),
    },
    {
      title: "状态标识",
      dataIndex: "status",
      key: "status",
      width: 130,
      render: (_s: string, row) => (
        <Tag color={COMPLIANCE_STATUS_COLOR[String(row.status ?? "").toLowerCase()] ?? "default"}>
          {rowStatusText(row)}
        </Tag>
      ),
    },
    {
      title: "操作",
      key: "action",
      width: 150,
      render: (_s: unknown, row) => {
        // 后端自本轮起在 items 内返回 exams:[{exam_id,name}]，可以真实下钻到考试统计；
        // 老数据（无 exams 字段）时不编造跳转，退化为提示。
        const first = row.exams?.[0];
        if (!first) {
          return <span className="muted">无考试可下钻</span>;
        }
        return (
          <Button
            type="link"
            size="small"
            style={{ padding: 0 }}
            onClick={() => navigate(`/statistics?examId=${first.exam_id}`)}
          >
            查看统计{row.exams && row.exams.length > 1 ? `（${row.exams.length} 场）` : ""}
          </Button>
        );
      },
    },
  ];

  return (
    <div className="stack">
      <PageHeader
        eyebrow="统计看板 / 合规全景"
        title="制度传达合规全景"
        description="以制度为单位验证传达覆盖率与通过率。"
        extra={
          <>
            <Segmented
              value={range}
              onChange={(v) => setRange(v as RangeKey)}
              options={RANGE_OPTIONS}
            />
            <Button icon={<FileExcelOutlined />} disabled={!items.length} onClick={handleExportCsv}>
              导出 Excel
            </Button>
            <Button type="primary" icon={<PrinterOutlined />} onClick={printAsPdf}>
              导出合规报告 PDF
            </Button>
          </>
        }
      />

      {/* 报表头部：屏幕与打印共用，printAsPdf() 打出的 PDF 由此构成完整报告封面 */}
      <div className="panel" style={{ marginBottom: 0 }}>
        <div className="eyebrow">制度传达合规报告</div>
        <div style={{ fontSize: 18, fontWeight: 800, marginTop: 2 }}>
          制度传达合规全景（{caption}）
        </div>
        <div className="muted" style={{ marginTop: 10, lineHeight: 1.8 }}>
          报表口径：{caption}｜生成时间：{generatedAt}｜生成人：{author}
          <br />
          统计范围：合规接口按「制度」聚合的全部已发布 / 已截止 / 已终止考试，按上方报表口径下推
          period 参数过滤。
        </div>
      </div>

      <KpiGrid columns={4}>
        <KpiCard
          label="覆盖制度数"
          value={isLoading ? "…" : summary.total}
          suffix="项"
          tone="blue"
          icon="▤"
          loading={isLoading}
          trend={`累计考试 ${summary.examTotal} 场`}
          trendTone="neutral"
        />
        <KpiCard
          label="平均覆盖率"
          value={isLoading ? "…" : fmtPercent(summary.avgCoverage)}
          tone="cyan"
          icon="◎"
          loading={isLoading}
          trend="以制度为单位取算术平均"
          trendTone="neutral"
        />
        <KpiCard
          label="平均通过率"
          value={isLoading ? "…" : fmtPercent(summary.avgPass)}
          tone="green"
          icon="✓"
          loading={isLoading}
          trend={`阈值 ${PASS_GREEN}%：≥${PASS_GREEN}% 达标 / ${PASS_AMBER}-${PASS_GREEN}% 关注 / <${PASS_AMBER}% 不合格`}
          trendTone="neutral"
        />
        <KpiCard
          label="风险项数量"
          value={isLoading ? "…" : summary.riskCount}
          suffix="项"
          tone={summary.riskCount > 0 ? "amber" : "green"}
          icon="!"
          loading={isLoading}
          trend={`通过率 < ${PASS_GREEN}% 共 ${summary.weakBelow} 项`}
          trendTone={summary.riskCount > 0 ? "warn" : "neutral"}
        />
      </KpiGrid>

      {isError ? (
        <ComplianceErrorPanel error={error} onRetry={() => void refetch()} />
      ) : (
        <Panel
          title="制度传达验证一览"
          extra={
            items.length ? `共 ${summary.total} 项制度 / 考试｜覆盖率与通过率可排序` : undefined
          }
        >
          {isLoading ? (
            <Skeleton active paragraph={{ rows: 6 }} />
          ) : items.length === 0 ? (
            <EmptyHint text="暂无已发布/已截止的考试数据，请先在考试管理中发布考试" />
          ) : (
            <Table<ComplianceRowRuntime>
              rowKey="document_name"
              size="small"
              columns={columns}
              dataSource={items}
              pagination={{ pageSize: 10, showSizeChanger: false, size: "small" }}
            />
          )}
          <div className="muted" style={{ marginTop: 12 }}>
            口径说明：上方时间范围以 period 参数下推后端（按考试发布时间过滤），切换即重新取数；
            覆盖率 = 已考 / 应考，通过率 = 通过 / 已考； 风险项按「传达未完成（状态标识非已完成）」或
            「通过率 &lt; {PASS_GREEN}%」判定。数据来源：GET /statistics/compliance。
          </div>
        </Panel>
      )}

      <Panel title="薄弱制度排行" extra="通过率最低的 5 项">
        {isLoading ? <Skeleton active paragraph={{ rows: 5 }} /> : <RankBars data={rankData} colorFor={(d) => passColor(d.value)} />}
      </Panel>
    </div>
  );
}
