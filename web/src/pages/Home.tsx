/**
 * 工作台 — 对齐 UIdesign 评审稿 01 屏。
 *
 * spec（页面层级树 二、首页 / 工作台）要求：
 *   待办事项卡片（按角色：专家=待审核题目 / HR=进行中考试与未考人数 / 管理者=统计报告）
 *   快捷入口（专家=上传制度 / HR=新建考试 / 管理者=部门统计）
 *   最近动态（最近上传的制度 / 最近发布的考试 / 最近完成的统计）
 *
 * 全部数据来自真实接口，不保留任何占位数字（自测清单 2.1 红线）。
 */

import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, Button, Skeleton, Tag } from "antd";
import {
  ArrowRightOutlined,
  AuditOutlined,
  BarChartOutlined,
  BellOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  FormOutlined,
  PlusOutlined,
  ThunderboltOutlined,
  UploadOutlined,
} from "@ant-design/icons";

import { useAuthStore } from "../store/auth";
import {
  useAuditLogs,
  useChapterStats,
  useComplianceOverview,
  useDeptStats,
  useDocuments,
  useExams,
  useInbox,
  usePendingQuestions,
  useQuestions,
} from "../api/hooks";
import {
  AUDIT_ACTION_COLOR,
  AUDIT_ACTION_LABEL,
  DOC_STATUS_LABEL,
  DOC_STATUS_COLOR,
  EXAM_STATUS_LABEL,
  EXAM_STATUS_COLOR,
  ROLE_LABEL,
  type UserRole,
} from "../api/types";
import {
  completionRate,
  fmtDateTime,
  fmtFromNow,
  fmtPercent,
  fmtScore,
  greeting,
  todayLabel,
} from "../utils/format";
import {
  EmptyHint,
  KpiCard,
  KpiGrid,
  Panel,
  ProgressRows,
  RankBars,
} from "../components/ui";

/** 最近动态条目（由制度 / 考试 / 审计日志统一映射而来）。 */
interface ActivityItem {
  id: string;
  title: string;
  sub: string;
  time: string;
  tone?: "cyan" | "amber" | "green";
  onClick?: () => void;
}

export default function HomePage() {
  const navigate = useNavigate();
  const { user } = useAuthStore();
  const role: UserRole | undefined = user?.role;

  // ── 各角色需要的数据（未启用的查询不会发请求）──────────────
  const isExpert = role === "expert";
  const isHr = role === "hr_admin";
  const isDeptMgr = role === "dept_manager";
  const isCompliance = role === "compliance";
  const isIt = role === "it_admin";

  const pending = usePendingQuestions(isExpert ? { page: 1, page_size: 5 } : {}, isExpert);
  const questions = useQuestions({ page: 1, page_size: 1 }, isExpert);
  const documents = useDocuments({ page: 1, page_size: 5 });
  const exams = useExams({ page: 1, page_size: 100 }, isHr);
  const inbox = useInbox(Boolean(user));
  const audit = useAuditLogs({ page: 1, page_size: 6 }, isIt || isCompliance);

  // 部门管理者：取最近一场考试的本部门表现（优先进行中，其次已截止）
  const latestExamId = useMemo(() => {
    const items = exams.data?.items ?? [];
    const pick =
      items.find((e) => e.status === "in_progress") ??
      items.find((e) => e.status === "finished") ??
      items[0];
    return pick?.id;
  }, [exams.data?.items]);
  const deptStats = useDeptStats(latestExamId, isDeptMgr && Boolean(latestExamId));
  // 部门管理者的薄弱章节：by-chapter 对 dept_manager 开放
  const chapterStats = useChapterStats(latestExamId, isDeptMgr && Boolean(latestExamId));
  // 合规角色：平均通过率（period 留空 = 全部时间范围）
  const compliance = useComplianceOverview(undefined, isCompliance);

  // ── 派生数据 ─────────────────────────────────────────────
  const docItems = documents.data?.items ?? [];
  const examItems = exams.data?.items ?? [];

  const inProgressExams = useMemo(
    () => examItems.filter((e) => e.status === "in_progress" || e.status === "pending"),
    [examItems],
  );
  const finishedExams = useMemo(
    () => examItems.filter((e) => e.status === "finished"),
    [examItems],
  );

  /** 制度覆盖率 = 已生成题目的制度数 / 制度总数 */
  const coverage = useMemo(() => {
    const total = documents.data?.total ?? 0;
    if (!total) return 0;
    const withQuestions = docItems.filter((d) => d.question_count > 0).length;
    return Math.round((withQuestions / Math.min(total, docItems.length || total)) * 1000) / 10;
  }, [documents.data?.total, docItems]);

  const totalCandidates = inProgressExams.reduce((s, e) => s + (e.candidate_count ?? 0), 0);
  const totalCompleted = inProgressExams.reduce((s, e) => s + (e.completed_count ?? 0), 0);
  const notStarted = Math.max(totalCandidates - totalCompleted, 0);

  // ── KPI 卡片（按角色）────────────────────────────────────
  const kpis = useMemo(() => {
    const cards: React.ReactNode[] = [];
    const push = (node: React.ReactNode) => cards.push(node);

    if (isExpert) {
      push(
        <KpiCard
          key="pending"
          label="待审核题目"
          value={pending.data?.total ?? 0}
          icon={<AuditOutlined />}
          tone="amber"
          trend={pending.data?.total ? "需逐题核对制度原文" : "暂无待办"}
          trendTone={pending.data?.total ? "warn" : "up"}
          onClick={() => navigate("/review")}
          loading={pending.isLoading}
        />,
      );
      push(
        <KpiCard
          key="bank"
          label="已入库题目"
          value={questions.data?.total ?? 0}
          icon={<DatabaseOutlined />}
          tone="green"
          trend="题库总量"
          onClick={() => navigate("/questions")}
          loading={questions.isLoading}
        />,
      );
    }

    if (isHr) {
      push(
        <KpiCard
          key="running"
          label="进行中考试"
          value={inProgressExams.length}
          icon={<FormOutlined />}
          tone="amber"
          trend={notStarted ? `${notStarted} 人未完成` : "全部已完成"}
          trendTone={notStarted ? "warn" : "up"}
          onClick={() => navigate("/exams")}
          loading={exams.isLoading}
        />,
      );
      push(
        <KpiCard
          key="finished"
          label="已截止考试"
          value={finishedExams.length}
          icon={<BarChartOutlined />}
          tone="green"
          trend="可查看统计看板"
          onClick={() => navigate("/statistics")}
          loading={exams.isLoading}
        />,
      );
    }

    if (isDeptMgr) {
      const row = deptStats.data?.departments?.[0];
      push(
        <KpiCard
          key="dept-done"
          label="本部门已完成"
          value={row ? `${row.completed_count}/${row.candidate_count}` : "—"}
          icon={<FormOutlined />}
          tone="blue"
          trend={row ? `完成率 ${completionRate(row.completed_count, row.candidate_count)}%` : "暂无考试数据"}
          onClick={() => navigate("/statistics/dept")}
          loading={deptStats.isLoading}
        />,
      );
      push(
        <KpiCard
          key="dept-avg"
          label="本部门平均分"
          value={row ? fmtScore(row.average_score) : "—"}
          icon={<BarChartOutlined />}
          tone={row && row.average_score < 60 ? "red" : "green"}
          trend={row && row.average_score < 60 ? "低于及格线，建议关注" : "满分 100"}
          trendTone={row && row.average_score < 60 ? "danger" : "up"}
          onClick={() => navigate("/statistics/dept")}
          loading={deptStats.isLoading}
        />,
      );
    }

    if (isCompliance) {
      const rows = compliance.data?.items ?? [];
      const avgPass = rows.length
        ? Math.round((rows.reduce((s, r) => s + (r.pass_rate ?? 0), 0) / rows.length) * 10) / 10
        : 0;
      push(
        <KpiCard
          key="comp-pass"
          label="制度平均通过率"
          value={rows.length ? fmtPercent(avgPass) : "—"}
          icon={<BarChartOutlined />}
          tone={rows.length && avgPass < 80 ? "amber" : "green"}
          trend={
            compliance.data
              ? `${compliance.data.fully_covered} 项已全覆盖 · ${compliance.data.pending} 项待完成`
              : "合规留痕数据来源"
          }
          trendTone={rows.length && avgPass < 80 ? "warn" : "up"}
          onClick={() => navigate("/statistics/compliance")}
          loading={compliance.isLoading}
        />,
      );
    }

    if (isIt) {
      push(
        <KpiCard
          key="it-logs"
          label="近 7 日操作日志"
          value={audit.data?.total ?? 0}
          icon={<ThunderboltOutlined />}
          tone="purple"
          trend="登录 / 上传 / 审核 / 组卷"
          onClick={() => navigate("/system")}
          loading={audit.isLoading}
        />,
      );
    }

    push(
      <KpiCard
        key="docs"
        label="制度文档"
        value={documents.data?.total ?? 0}
        icon={<FileTextOutlined />}
        tone="blue"
        trend={coverage ? `覆盖率 ${fmtPercent(coverage)}` : "可上传 Word / PDF"}
        onClick={() => navigate("/documents")}
        loading={documents.isLoading}
      />,
    );

    push(
      <KpiCard
        key="inbox"
        label="待办与提醒"
        value={inbox.data?.total ?? 0}
        icon={<BellOutlined />}
        tone={inbox.data?.unread ? "amber" : "cyan"}
        trend={inbox.data?.unread ? `${inbox.data.unread} 项需处理` : "暂无紧急事项"}
        trendTone={inbox.data?.unread ? "warn" : "up"}
        onClick={() => navigate(routeForRole(role))}
        loading={inbox.isLoading}
      />,
    );

    return cards;
  }, [
    isExpert,
    isHr,
    isDeptMgr,
    isCompliance,
    isIt,
    role,
    pending.data?.total,
    pending.isLoading,
    questions.data?.total,
    questions.isLoading,
    exams.isLoading,
    inProgressExams.length,
    finishedExams.length,
    notStarted,
    deptStats.data?.departments,
    deptStats.isLoading,
    compliance.data?.items,
    compliance.data?.fully_covered,
    compliance.data?.pending,
    compliance.isLoading,
    examItems.length,
    audit.data?.total,
    audit.isLoading,
    documents.data?.total,
    documents.isLoading,
    coverage,
    inbox.data?.total,
    inbox.data?.unread,
    inbox.isLoading,
    navigate,
  ]);

  // ── 待办事项 ─────────────────────────────────────────────
  const tasks = useMemo(() => {
    const list: { key: string; title: string; sub: string; action: string; link: string; tone?: string }[] = [];

    if (isExpert && (pending.data?.total ?? 0) > 0) {
      list.push({
        key: "review",
        title: `${pending.data?.total} 道 AI 生成题目待审核`,
        sub: "逐题核对制度原文后通过或弃用",
        action: "去审核",
        link: "/review",
      });
    }
    if (isExpert) {
      const noQuestions = docItems.filter((d) => d.question_count === 0);
      if (noQuestions.length) {
        list.push({
          key: "gen",
          title: `《${noQuestions[0].name}》尚未生成题目`,
          sub: "制度已解析，可触发 AI 出题",
          action: "去出题",
          link: `/documents/${noQuestions[0].id}`,
        });
      }
    }
    if (isHr) {
      for (const e of inProgressExams.slice(0, 2)) {
        const left = Math.max((e.candidate_count ?? 0) - (e.completed_count ?? 0), 0);
        list.push({
          key: `exam-${e.id}`,
          title: left ? `${e.name} · ${left} 人未完成` : `${e.name} 待发布或已完成`,
          sub: `应考 ${e.candidate_count} 人 · 已完成 ${e.completed_count} 人`,
          action: "查看进度",
          link: `/exams/${e.id}`,
          tone: left ? "amber" : undefined,
        });
      }
    }
    if (isDeptMgr) {
      list.push({
        key: "dept",
        title: "本部门统计待查看",
        sub: "完成率、平均分与薄弱章节",
        action: "查看统计",
        link: "/statistics/dept",
      });
    }
    if (isCompliance) {
      list.push({
        key: "compliance",
        title: "制度传达合规留痕报告",
        sub: "覆盖率与通过率一览，可导出 PDF",
        action: "查看报告",
        link: "/statistics/compliance",
      });
    }
    if (isIt) {
      list.push({
        key: "system",
        title: "组织架构与权限待核对",
        sub: "HR 同步状态、角色权限矩阵、审计日志",
        action: "去系统管理",
        link: "/system",
      });
    }
    return list;
  }, [isExpert, isHr, isDeptMgr, isCompliance, isIt, pending.data?.total, docItems, inProgressExams]);

  // ── 最近动态 ─────────────────────────────────────────────
  const activities = useMemo<ActivityItem[]>(() => {
    if (audit.data?.items?.length) {
      return audit.data.items.map((log) => ({
        id: log.id,
        title: `${log.user_name ?? "未知用户"} · ${AUDIT_ACTION_LABEL[log.action] ?? log.action}`,
        sub: log.detail ? String(log.detail).slice(0, 60) : (log.target_type ?? ""),
        time: fmtFromNow(log.created_at),
        tone: "cyan" as const,
      }));
    }

    const items: ActivityItem[] = [];
    for (const d of docItems.slice(0, 3)) {
      items.push({
        id: `doc-${d.id}`,
        title: `上传制度《${d.name}》`,
        sub: `版本 ${d.version} · ${DOC_STATUS_LABEL[d.status] ?? d.status}`,
        time: fmtFromNow(d.created_at),
        onClick: () => navigate(`/documents/${d.id}`),
      });
    }
    for (const e of examItems.slice(0, 3)) {
      items.push({
        id: `exam-${e.id}`,
        title:
          e.status === "in_progress"
            ? `发布考试「${e.name}」`
            : `考试「${e.name}」更新为${EXAM_STATUS_LABEL[e.status] ?? e.status}`,
        sub: `覆盖 ${e.candidate_count} 名考生 · 已完成 ${e.completed_count} 人`,
        time: fmtFromNow(e.published_at ?? e.updated_at ?? e.created_at),
        tone: e.status === "in_progress" ? ("amber" as const) : undefined,
        onClick: () => navigate(`/exams/${e.id}`),
      });
    }
    for (const item of (inbox.data?.items ?? []).slice(0, 3)) {
      if (item.type !== "notification") continue;
      items.push({
        id: `inbox-${item.title}`,
        title: item.title,
        sub: item.detail?.slice(0, 60) ?? "",
        time: item.created_at ? fmtFromNow(item.created_at) : "",
        tone: "green" as const,
        onClick: item.link ? () => navigate(item.link!) : undefined,
      });
    }
    return items.sort((a, b) => (b.time ?? "").localeCompare(a.time ?? "")).slice(0, 6);
  }, [audit.data?.items, docItems, examItems, inbox.data?.items, navigate]);

  // ── 快捷入口（按角色）────────────────────────────────────
  const quickActions = useMemo(() => {
    if (isExpert)
      return [
        { label: "上传制度", icon: <UploadOutlined />, link: "/documents/upload", primary: true },
        { label: "题目审核", icon: <AuditOutlined />, link: "/review" },
        { label: "题库管理", icon: <DatabaseOutlined />, link: "/questions" },
      ];
    if (isHr)
      return [
        { label: "新建考试", icon: <PlusOutlined />, link: "/exams/create", primary: true },
        { label: "考试管理", icon: <FormOutlined />, link: "/exams" },
        { label: "统计看板", icon: <BarChartOutlined />, link: "/statistics" },
      ];
    if (isDeptMgr)
      return [
        { label: "部门统计", icon: <BarChartOutlined />, link: "/statistics/dept", primary: true },
      ];
    if (isCompliance)
      return [
        {
          label: "合规全景",
          icon: <BarChartOutlined />,
          link: "/statistics/compliance",
          primary: true,
        },
      ];
    if (isIt)
      return [{ label: "系统管理", icon: <ThunderboltOutlined />, link: "/system", primary: true }];
    return [{ label: "个人中心", icon: <FileTextOutlined />, link: "/profile", primary: true }];
  }, [isExpert, isHr, isDeptMgr, isCompliance, isIt]);

  const hasError =
    documents.isError || pending.isError || exams.isError || inbox.isError || audit.isError;

  return (
    <div className="stack">
      {/* 问候区（评审稿 01 屏顶栏） */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-end",
          gap: 16,
          flexWrap: "wrap",
        }}
      >
        <div>
          <div className="eyebrow">{todayLabel()}</div>
          <div style={{ fontSize: 24, fontWeight: 800, letterSpacing: "-0.4px" }}>
            {greeting()}，{user?.name ?? "同事"}
          </div>
          <div className="muted" style={{ marginTop: 6 }}>
            当前角色：{role ? ROLE_LABEL[role] : "—"}
            {user?.department_name ? ` · ${user.department_name}` : ""}
          </div>
        </div>
        <div className="app-actions">
          {quickActions.map((a) => (
            <Button
              key={a.label}
              type={a.primary ? "primary" : "default"}
              icon={a.icon}
              onClick={() => navigate(a.link)}
            >
              {a.label}
            </Button>
          ))}
        </div>
      </div>

      {hasError && (
        <Alert
          type="warning"
          showIcon
          message="部分数据加载失败"
          description="可能是后端服务不可用，或当前角色无该模块的数据权限。请检查服务状态后重试。"
        />
      )}

      <KpiGrid columns={4}>{kpis}</KpiGrid>

      <div className="grid-2">
        {/* 待办事项 */}
        <Panel
          title="待办事项"
          extra={
            quickActions[1] ? (
              <span
                style={{ cursor: "pointer", color: "var(--blue)", fontWeight: 700 }}
                onClick={() => navigate(quickActions[1].link)}
              >
                全部查看 →
              </span>
            ) : null
          }
        >
          {documents.isLoading && !tasks.length ? (
            <Skeleton active paragraph={{ rows: 3 }} />
          ) : tasks.length === 0 ? (
            <EmptyHint text="暂无待办：当前角色没有待处理的审核、考试或报告任务。" />
          ) : (
            tasks.map((t) => (
              <div className="task" key={t.key} onClick={() => navigate(t.link)}>
                <span
                  className="task-icon"
                  style={
                    t.tone === "amber"
                      ? { background: "var(--amber-soft)", color: "var(--amber-deep)" }
                      : undefined
                  }
                >
                  ◇
                </span>
                <div style={{ minWidth: 0 }}>
                  <b>{t.title}</b>
                  <small>{t.sub}</small>
                </div>
                <span
                  className="task-action"
                  style={t.tone === "amber" ? { color: "var(--amber-deep)" } : undefined}
                >
                  {t.action}
                </span>
              </div>
            ))
          )}
        </Panel>

        {/* 考试完成进度 + 最近动态 */}
        <div className="stack">
          <Panel
            title={isDeptMgr ? "本部门薄弱章节" : "考试完成进度"}
            extra={isHr ? "进行中" : undefined}
          >
            {isDeptMgr ? (
              (chapterStats.data?.chapters?.length ?? 0) > 0 ? (
                <RankBars
                  colorFor={(d) =>
                    d.value < 60 ? "var(--red)" : d.value < 80 ? "var(--amber)" : "var(--cyan)"
                  }
                  data={[...(chapterStats.data?.chapters ?? [])]
                    .map((c) => ({
                      label: `${c.chapter_no ?? "—"}${c.section_no ? ` ${c.section_no}` : ""}`,
                      value: Math.round(c.correct_rate),
                      hint: `作答 ${c.correct_answers}/${c.total_answers}，正确率 ${fmtPercent(c.correct_rate)}`,
                    }))
                    .sort((a, b) => a.value - b.value)}
                />
              ) : (
                <EmptyHint text="暂无本部门章节数据（可能尚无提交答卷）" />
              )
            ) : inProgressExams.length === 0 ? (
              <EmptyHint text="暂无进行中的考试" />
            ) : (
              <ProgressRows
                rows={inProgressExams.slice(0, 4).map((e, i) => ({
                  label: e.name.slice(0, 6),
                  percent: completionRate(e.completed_count ?? 0, e.candidate_count ?? 0),
                  color: i % 3 === 1 ? "var(--cyan)" : i % 3 === 2 ? "var(--amber)" : undefined,
                  hint: `${e.name}：已完成 ${e.completed_count}/${e.candidate_count}`,
                }))}
              />
            )}
          </Panel>

          <Panel title="最近动态" extra="按时间倒序">
            {documents.isLoading && !activities.length ? (
              <Skeleton active paragraph={{ rows: 3 }} />
            ) : activities.length === 0 ? (
              <EmptyHint text="暂无动态：上传制度或发布考试后会在这里出现。" />
            ) : (
              activities.map((a) => (
                <div
                  className="activity"
                  key={a.id}
                  onClick={a.onClick}
                  style={a.onClick ? { cursor: "pointer" } : undefined}
                >
                  <i
                    className="dot"
                    style={
                      a.tone === "amber"
                        ? { background: "var(--amber)", boxShadow: "0 0 0 4px var(--amber-soft)" }
                        : a.tone === "green"
                          ? { background: "var(--green)", boxShadow: "0 0 0 4px var(--green-soft)" }
                          : undefined
                    }
                  />
                  <div style={{ minWidth: 0 }}>
                    <b>{a.title}</b>
                    <small>{a.sub}</small>
                  </div>
                  <time>{a.time}</time>
                </div>
              ))
            )}
          </Panel>
        </div>
      </div>

      {/* 制度概览（专家 / HR 都关心传达覆盖） */}
      <Panel
        title="制度传达进度"
        extra={
          <span
            style={{ cursor: "pointer", color: "var(--blue)", fontWeight: 700 }}
            onClick={() => navigate("/documents")}
          >
            制度管理 →
          </span>
        }
      >
        {documents.isLoading ? (
          <Skeleton active paragraph={{ rows: 2 }} />
        ) : docItems.length === 0 ? (
          <EmptyHint
            text="还没有制度文档。上传制度后，系统会自动解析章节条款并支持 AI 出题。"
            action={
              isExpert ? (
                <Button type="primary" icon={<UploadOutlined />} onClick={() => navigate("/documents/upload")}>
                  上传制度
                </Button>
              ) : undefined
            }
          />
        ) : (
          <div style={{ display: "grid", gap: 10 }}>
            {docItems.map((d) => (
              <div
                key={d.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  border: "1px solid #eef1f6",
                  borderRadius: 11,
                  padding: "12px 14px",
                  cursor: "pointer",
                }}
                onClick={() => navigate(`/documents/${d.id}`)}
              >
                <div style={{ minWidth: 0, flex: 1 }}>
                  <b style={{ fontSize: 12 }}>{d.name}</b>
                  <small style={{ display: "block", color: "#8190a3", fontSize: 10, marginTop: 3 }}>
                    版本 {d.version} · 上传于 {fmtDateTime(d.created_at)} · 题量 {d.question_count}
                  </small>
                </div>
                <Tag color={DOC_STATUS_COLOR[d.status]}>{DOC_STATUS_LABEL[d.status] ?? d.status}</Tag>
                <ArrowRightOutlined style={{ color: "var(--muted-2)", fontSize: 11 }} />
              </div>
            ))}
            {isHr && examItems.length > 0 && (
              <div className="muted" style={{ marginTop: 4 }}>
                最近考试状态：
                {examItems.slice(0, 3).map((e) => (
                  <Tag key={e.id} color={EXAM_STATUS_COLOR[e.status]} style={{ marginLeft: 6 }}>
                    {e.name.slice(0, 10)} · {EXAM_STATUS_LABEL[e.status] ?? e.status}
                  </Tag>
                ))}
              </div>
            )}
          </div>
        )}
      </Panel>
    </div>
  );
}

/** 无待办时「待办与提醒」卡片的落地页（按角色）。 */
function routeForRole(role: UserRole | undefined): string {
  switch (role) {
    case "expert":
      return "/review";
    case "hr_admin":
      return "/exams";
    case "dept_manager":
      return "/statistics/dept";
    case "compliance":
      return "/statistics/compliance";
    case "it_admin":
      return "/system";
    default:
      return "/profile";
  }
}
