/**
 * 公共展示组件 — 对齐 UIdesign 评审稿的 KPI 卡片 / 条形图 / 排行条 / 空态。
 *
 * 这些组件只负责样式与排版，不含任何数据请求逻辑，
 * 页面只需把真实接口数据传进来即可，避免各页各写一套 markup 导致视觉漂移。
 */

import { ReactNode } from "react";
import { Skeleton } from "antd";

// ── 页面头部 ──────────────────────────────────────────────────────────

export function PageHeader({
  eyebrow,
  title,
  extra,
  description,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  extra?: ReactNode;
  description?: ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-end",
        gap: 16,
        marginBottom: 18,
        flexWrap: "wrap",
      }}
    >
      <div style={{ minWidth: 0 }}>
        {eyebrow ? <div className="eyebrow">{eyebrow}</div> : null}
        <div className="title">{title}</div>
        {description ? (
          <div className="muted" style={{ marginTop: 6 }}>
            {description}
          </div>
        ) : null}
      </div>
      {extra ? <div className="app-actions no-print">{extra}</div> : null}
    </div>
  );
}

// ── KPI 卡片 ─────────────────────────────────────────────────────────

const TONE = {
  blue: { bg: "var(--blue-soft)", fg: "var(--blue)" },
  cyan: { bg: "var(--cyan-soft)", fg: "#17a58b" },
  green: { bg: "var(--green-soft)", fg: "var(--green-deep)" },
  amber: { bg: "var(--amber-soft)", fg: "var(--amber-deep)" },
  red: { bg: "var(--red-soft)", fg: "var(--red-deep)" },
  purple: { bg: "var(--purple-soft)", fg: "var(--purple)" },
} as const;

export type KpiTone = keyof typeof TONE;

export function KpiCard({
  label,
  value,
  suffix,
  icon,
  tone = "blue",
  trend,
  trendTone = "up",
  onClick,
  loading,
}: {
  label: ReactNode;
  value: ReactNode;
  suffix?: ReactNode;
  icon?: ReactNode;
  tone?: KpiTone;
  /** 副标题行（趋势 / 说明） */
  trend?: ReactNode;
  trendTone?: "up" | "warn" | "danger" | "neutral";
  onClick?: () => void;
  loading?: boolean;
}) {
  const t = TONE[tone];
  const trendClass =
    trendTone === "warn" ? "trend warn" : trendTone === "danger" ? "trend danger" : "trend";
  return (
    <div
      className="kpi"
      onClick={onClick}
      style={onClick ? { cursor: "pointer" } : undefined}
      role={onClick ? "button" : undefined}
    >
      <div className="kpi-top">
        <span>{label}</span>
        {icon ? (
          <span className="kpi-icon" style={{ background: t.bg, color: t.fg }}>
            {icon}
          </span>
        ) : null}
      </div>
      {loading ? (
        <Skeleton.Input active size="small" style={{ width: 90, margin: "14px 0 8px" }} />
      ) : (
        <div className="kpi-num">
          {value}
          {suffix ? <span>{suffix}</span> : null}
        </div>
      )}
      {trend ? <div className={trendClass}>{trend}</div> : null}
    </div>
  );
}

/** 等宽 KPI 栅格（评审稿为 4 列 / 5 列）。 */
export function KpiGrid({ columns = 4, children }: { columns?: number; children: ReactNode }) {
  return (
    <div className="kpi-grid" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
      {children}
    </div>
  );
}

// ── 条形图 ───────────────────────────────────────────────────────────

export interface BarDatum {
  label: string;
  /** 0-100 的百分比（分数则先换算） */
  value: number;
  hint?: string;
}

/** 竖向柱状图（评审稿「部门平均分」）。 */
export function BarChart({
  data,
  max = 100,
  height = 170,
  colorFor,
  onBarClick,
}: {
  data: BarDatum[];
  max?: number;
  height?: number;
  colorFor?: (d: BarDatum, i: number) => string | undefined;
  onBarClick?: (d: BarDatum) => void;
}) {
  if (!data.length) return <EmptyHint text="暂无数据" />;
  return (
    <div className="bar-chart" style={{ height }}>
      {data.map((d, i) => {
        const pct = Math.max(Math.min((d.value / max) * 100, 100), d.value > 0 ? 3 : 0);
        return (
          <div
            className="bar-col"
            key={`${d.label}-${i}`}
            title={d.hint ?? `${d.label}：${d.value}`}
            onClick={onBarClick ? () => onBarClick(d) : undefined}
            style={onBarClick ? { cursor: "pointer" } : undefined}
          >
            <small style={{ fontWeight: 700, color: "var(--ink-2)" }}>{d.value}</small>
            <i style={{ height: `${pct}%`, background: colorFor?.(d, i) }} />
            <small>{d.label}</small>
          </div>
        );
      })}
    </div>
  );
}

/** 横向排行条（评审稿「章节正确率排行」）。 */
export function RankBars({
  data,
  colorFor,
  onRowClick,
}: {
  data: BarDatum[];
  colorFor?: (d: BarDatum, i: number) => string | undefined;
  onRowClick?: (d: BarDatum) => void;
}) {
  if (!data.length) return <EmptyHint text="暂无数据" />;
  return (
    <div>
      {data.map((d, i) => (
        <div
          className="rank"
          key={`${d.label}-${i}`}
          title={d.hint}
          onClick={onRowClick ? () => onRowClick(d) : undefined}
          style={onRowClick ? { cursor: "pointer" } : undefined}
        >
          <span className="rank-name">{d.label}</span>
          <div className="rank-bar">
            <i
              style={{
                width: `${Math.max(Math.min(d.value, 100), d.value > 0 ? 3 : 0)}%`,
                background: colorFor?.(d, i),
              }}
            />
          </div>
          <strong>{d.value}%</strong>
        </div>
      ))}
    </div>
  );
}

/** 进度条行（评审稿「考试完成进度」）。 */
export function ProgressRows({
  rows,
}: {
  rows: { label: string; percent: number; color?: string; hint?: string }[];
}) {
  if (!rows.length) return <EmptyHint text="暂无进行中的考试" />;
  return (
    <div>
      {rows.map((r, i) => (
        <div className="bar-row" key={`${r.label}-${i}`} title={r.hint}>
          <label>{r.label}</label>
          <div className="bar">
            <i style={{ width: `${Math.min(r.percent, 100)}%`, background: r.color }} />
          </div>
          <strong>{r.percent}%</strong>
        </div>
      ))}
    </div>
  );
}

// ── 状态占位 ─────────────────────────────────────────────────────────

export function EmptyHint({ text, action }: { text: ReactNode; action?: ReactNode }) {
  return (
    <div className="app-empty">
      <div style={{ fontSize: 22, opacity: 0.35, marginBottom: 8 }}>◌</div>
      <div>{text}</div>
      {action ? <div style={{ marginTop: 12 }}>{action}</div> : null}
    </div>
  );
}

// ── 面板 ─────────────────────────────────────────────────────────────

export function Panel({
  title,
  extra,
  children,
  style,
}: {
  title?: ReactNode;
  extra?: ReactNode;
  children: ReactNode;
  style?: React.CSSProperties;
}) {
  return (
    <div className="panel" style={style}>
      {title || extra ? (
        <div className="panel-head">
          <h3>{title}</h3>
          {extra ? <span className="muted">{extra}</span> : null}
        </div>
      ) : null}
      {children}
    </div>
  );
}
