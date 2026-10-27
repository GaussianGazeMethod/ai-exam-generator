/**
 * 个人中心 — 所有角色可见。
 *
 * spec 页面层级树（个人中心）要求：
 *   基本信息（姓名、部门、角色，只读）
 *   我的考试记录（员工查看自己的历史成绩）
 *   退出登录
 *
 * 说明：除 /auth/me 外没有「修改资料」接口（原型阶段账号由 HR/SSO 同步），
 * 因此本页字段全部只读，不做假的「保存成功」反馈。
 */

import { useNavigate } from "react-router-dom";
import { Alert, Button, Descriptions, Skeleton, Table, Tag } from "antd";
import { LogoutOutlined, ReloadOutlined } from "@ant-design/icons";

import { useMe, useMyExamRecords, useRoles } from "../api/hooks";
import { ROLE_LABEL } from "../api/types";
import { useAuthStore } from "../store/auth";
import { fmtDateTime, fmtDuration, fmtScore } from "../utils/format";
import { EmptyHint, KpiCard, KpiGrid, PageHeader, Panel } from "../components/ui";

export default function Profile() {
  const navigate = useNavigate();
  const { user, logout } = useAuthStore();
  const me = useMe();
  const records = useMyExamRecords({ page: 1, page_size: 50 }, Boolean(user));
  // 权限矩阵接口仅 it_admin 可读，其他角色不请求（避免无意义的 403）
  const roles = useRoles(user?.role === "it_admin");

  const profile = me.data ?? user;
  const items = records.data?.items ?? [];

  const submitted = items.filter((r) => r.status === "submitted");
  const passed = submitted.filter((r) => r.is_passed);
  const scores = submitted
    .map((r) => r.total_score)
    .filter((s): s is number => typeof s === "number");
  const avgScore = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;

  const myPermissions = roles.data?.find((r) => r.role === profile?.role)?.permissions ?? [];

  return (
    <div className="stack">
      <PageHeader
        eyebrow="个人中心"
        title={profile?.name ?? "我的账号"}
        description="账号信息由公司组织架构 / SSO 同步，页面内为只读展示。"
        extra={
          <Button
            icon={<LogoutOutlined />}
            onClick={() => {
              logout();
              navigate("/login", { replace: true });
            }}
          >
            退出登录
          </Button>
        }
      />

      {me.isError && <Alert type="warning" showIcon message="获取账号信息失败，展示本地缓存信息" />}

      <div className="grid-2 even">
        <Panel
          title="基本信息"
          extra={
            <Button
              size="small"
              type="text"
              icon={<ReloadOutlined />}
              onClick={() => me.refetch()}
            />
          }
        >
          {me.isLoading && !profile ? (
            <Skeleton active paragraph={{ rows: 4 }} />
          ) : (
            <Descriptions column={1} size="small" labelStyle={{ width: 96, color: "var(--ink-3)" }}>
              <Descriptions.Item label="姓名">{profile?.name ?? "—"}</Descriptions.Item>
              <Descriptions.Item label="角色">
                {profile ? <Tag color="blue">{ROLE_LABEL[profile.role]}</Tag> : "—"}
              </Descriptions.Item>
              <Descriptions.Item label="部门">
                {profile?.department_name ?? "未分配部门"}
              </Descriptions.Item>
              <Descriptions.Item label="邮箱">{profile?.email ?? "—"}</Descriptions.Item>
              <Descriptions.Item label="状态">
                {profile?.is_active ? <Tag color="green">在职</Tag> : <Tag color="red">已停用</Tag>}
              </Descriptions.Item>
              <Descriptions.Item label="最近登录">
                {fmtDateTime(profile?.last_login_at)}
              </Descriptions.Item>
              <Descriptions.Item label="创建时间">
                {fmtDateTime(profile?.created_at)}
              </Descriptions.Item>
            </Descriptions>
          )}
        </Panel>

        <Panel title="我的权限范围" extra={roles.isError ? "该接口仅 IT 管理员可读" : undefined}>
          {myPermissions.length ? (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {myPermissions.map((p) => (
                <Tag key={p} color="geekblue" style={{ marginInlineEnd: 0 }}>
                  {p}
                </Tag>
              ))}
            </div>
          ) : (
            <EmptyHint
              text={
                roles.isLoading
                  ? "加载中…"
                  : "当前角色无权限查看矩阵（/system/roles 仅 IT 管理员可读），可在系统管理中核对。"
              }
            />
          )}
        </Panel>
      </div>

      <KpiGrid columns={4}>
        <KpiCard label="参加考试" value={submitted.length} tone="blue" />
        <KpiCard label="通过次数" value={passed.length} tone="green" />
        <KpiCard
          label="未通过"
          value={submitted.length - passed.length}
          tone={submitted.length - passed.length ? "red" : "cyan"}
        />
        <KpiCard
          label="平均分"
          value={avgScore === null ? "—" : fmtScore(avgScore)}
          tone="purple"
        />
      </KpiGrid>

      <Panel
        title="我的考试记录"
        extra={
          <Button
            size="small"
            type="text"
            icon={<ReloadOutlined />}
            onClick={() => records.refetch()}
          />
        }
      >
        <Table
          rowKey="answer_id"
          size="small"
          loading={records.isLoading}
          dataSource={items}
          locale={{
            emptyText: (
              <EmptyHint text="暂无考试记录。完成一次考试后成绩会出现在这里；可前往「我的考试」查看待考任务。" />
            ),
          }}
          pagination={false}
          columns={[
            { title: "考试名称", dataIndex: "exam_name", ellipsis: true },
            {
              title: "状态",
              dataIndex: "status",
              width: 90,
              render: (s: string) => (s === "submitted" ? <Tag color="green">已提交</Tag> : <Tag>答题中</Tag>),
            },
            {
              title: "得分",
              dataIndex: "total_score",
              width: 100,
              sorter: (a, b) => (a.total_score ?? 0) - (b.total_score ?? 0),
              render: (v: number | null, row) =>
                v === null ? (
                  "—"
                ) : (
                  <span
                    style={{
                      fontWeight: 700,
                      color: row.is_passed ? "var(--green-deep)" : "var(--red-deep)",
                    }}
                  >
                    {fmtScore(v)}
                  </span>
                ),
            },
            {
              title: "结果",
              dataIndex: "is_passed",
              width: 80,
              render: (v: boolean | null) =>
                v === null ? "—" : v ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag>,
            },
            {
              title: "答对 / 答错",
              width: 110,
              render: (_: unknown, row) => `${row.correct_count ?? "—"} / ${row.wrong_count ?? "—"}`,
            },
            {
              title: "用时",
              dataIndex: "duration_seconds",
              width: 100,
              render: (v: number | null) => fmtDuration(v),
            },
            {
              title: "提交时间",
              dataIndex: "submitted_at",
              width: 160,
              render: (v: string | null) => fmtDateTime(v),
            },
          ]}
        />
      </Panel>
    </div>
  );
}
