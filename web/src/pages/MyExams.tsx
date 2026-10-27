/**
 * 我的考试（员工端）— spec 移动答题端在 Web 侧的入口。
 *
 * spec 移动端由「扫码 / 链接」进入答题；Web 端员工此前没有任何入口，
 * 本页列出进行中 / 已截止的考试与本人答题状态，并给出二维码 / 链接入口。
 *
 * 数据源：GET /exam-taking/my-exams（仅返回本人答卷状态，不泄露他人成绩）。
 */

import { Alert, Button, Skeleton, Table, Tag } from "antd";
import { MobileOutlined, QrcodeOutlined, ReloadOutlined } from "@ant-design/icons";

import { useMyExams } from "../api/hooks";
import {
  EXAM_STATUS_COLOR,
  EXAM_STATUS_LABEL,
  MY_EXAM_STATUS_COLOR,
  MY_EXAM_STATUS_LABEL,
} from "../api/types";
import { fmtDateTime, fmtScore } from "../utils/format";
import { EmptyHint, KpiCard, KpiGrid, PageHeader, Panel } from "../components/ui";

/** 移动答题端地址（Vite dev: 5174）。生产由网关按 /m 前缀路由。 */
const MOBILE_BASE = `${window.location.protocol}//${window.location.hostname}:5174`;

export default function MyExams() {
  const { data, isLoading, isError, refetch } = useMyExams();

  const items = data?.items ?? [];
  const pending = items.filter((i) => i.my_status !== "submitted" && i.status === "in_progress");
  const done = items.filter((i) => i.my_status === "submitted");
  const scores = done.map((i) => i.my_score).filter((s): s is number => typeof s === "number");
  const avg = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;

  return (
    <div className="stack">
      <PageHeader
        eyebrow="移动答题端"
        title="我的考试"
        description="手机扫码或打开链接即可答题；本页用于查看待考与已完成记录。"
        extra={
          <Button icon={<ReloadOutlined />} onClick={() => refetch()}>
            刷新
          </Button>
        }
      />

      {isError && (
        <Alert type="error" showIcon message="加载考试列表失败" description="请确认后端服务可用后重试。" />
      )}

      <KpiGrid columns={4}>
        <KpiCard label="待完成考试" value={pending.length} tone="amber" loading={isLoading} />
        <KpiCard label="已完成考试" value={done.length} tone="green" loading={isLoading} />
        <KpiCard
          label="通过考试"
          value={done.filter((i) => i.my_is_passed).length}
          tone="cyan"
          loading={isLoading}
        />
        <KpiCard
          label="平均分"
          value={avg === null ? "—" : fmtScore(avg)}
          tone="purple"
          loading={isLoading}
        />
      </KpiGrid>

      <Panel
        title="考试列表"
        extra={
          <span className="muted">
            移动端地址：{MOBILE_BASE}/m/exam/&lt;考试ID&gt;
          </span>
        }
      >
        {isLoading ? (
          <Skeleton active paragraph={{ rows: 4 }} />
        ) : items.length === 0 ? (
          <EmptyHint text="暂无考试：管理员发布考试后，这里会出现待考任务与扫码入口。" />
        ) : (
          <Table
            rowKey="exam_id"
            size="small"
            dataSource={items}
            pagination={false}
            columns={[
              {
                title: "考试名称",
                dataIndex: "name",
                render: (v: string, row) => (
                  <div>
                    <b style={{ fontSize: 12 }}>{v}</b>
                    <div className="muted" style={{ marginTop: 2 }}>
                      {row.duration_minutes} 分钟
                      {row.deadline ? ` · 截止 ${fmtDateTime(row.deadline)}` : ""}
                      {row.allow_retake ? ` · 允许重考 ${row.max_retake_count} 次` : ""}
                    </div>
                  </div>
                ),
              },
              {
                title: "考试状态",
                dataIndex: "status",
                width: 100,
                render: (s: string) => (
                  <Tag color={EXAM_STATUS_COLOR[s as keyof typeof EXAM_STATUS_COLOR]}>
                    {EXAM_STATUS_LABEL[s as keyof typeof EXAM_STATUS_LABEL] ?? s}
                  </Tag>
                ),
              },
              {
                title: "我的状态",
                dataIndex: "my_status",
                width: 100,
                render: (s: keyof typeof MY_EXAM_STATUS_LABEL) => (
                  <Tag color={MY_EXAM_STATUS_COLOR[s]}>{MY_EXAM_STATUS_LABEL[s]}</Tag>
                ),
              },
              {
                title: "我的分数",
                dataIndex: "my_score",
                width: 100,
                render: (v: number | null, row) =>
                  v === null ? (
                    "—"
                  ) : (
                    <span
                      style={{
                        fontWeight: 700,
                        color: row.my_is_passed ? "var(--green-deep)" : "var(--red-deep)",
                      }}
                    >
                      {fmtScore(v)}
                    </span>
                  ),
              },
              {
                title: "提交时间",
                dataIndex: "my_submitted_at",
                width: 160,
                render: (v: string | null) => fmtDateTime(v),
              },
              {
                title: "操作",
                width: 200,
                render: (_: unknown, row) => {
                  const canTake = row.status === "in_progress" && row.my_status !== "submitted";
                  return (
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                      {canTake && (
                        <Button
                          type="primary"
                          size="small"
                          icon={<MobileOutlined />}
                          onClick={() => window.open(`${MOBILE_BASE}/m/exam/${row.exam_id}`, "_blank")}
                        >
                          开始答题
                        </Button>
                      )}
                      {row.my_answer_id && row.my_status === "submitted" && (
                        <Button size="small" icon={<QrcodeOutlined />} onClick={() => window.location.assign("/profile")}>
                          查看成绩
                        </Button>
                      )}
                      {!canTake && !row.my_answer_id && <span className="muted">已结束</span>}
                    </div>
                  );
                },
              },
            ]}
          />
        )}
      </Panel>

      <Panel title="答题入口说明" extra="扫码或手机浏览器打开">
        <div className="muted" style={{ lineHeight: 1.9, fontSize: 12 }}>
          1. 手机与电脑处于同一网络时，可直接访问
          <code style={{ margin: "0 4px" }}>{MOBILE_BASE}/m/exam/&lt;考试ID&gt;</code>
          开始答题（移动端 dev server 运行在 5174 端口）。
          <br />
          2. 考试发布后，「考试管理 → 考试详情」会生成二维码与链接，扫一扫即可进入移动答题页。
          <br />
          3. 答题过程中支持倒计时自动提交；提交后可在本页与「个人中心」查看成绩与错题回顾。
        </div>
      </Panel>
    </div>
  );
}
