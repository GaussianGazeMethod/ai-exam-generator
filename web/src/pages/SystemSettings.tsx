/**
 * 系统管理（S8，IT 管理员视角）— /system
 *
 * 四个页签：SSO 配置 / 组织架构 / 权限管理 / 审计日志。
 *
 * 诚实性约定（本页面的红线）：
 * - SSO 参数、HR 对接地址与密钥、同步频率在原型阶段**没有后端持久化接口**，
 *   这些表单只做本地状态 + antd Form 校验，界面用 Alert type="info" 明确标注「原型阶段未落库」，
 *   保存/测试按钮只给出「未接入后端持久化」的明确反馈，不伪造「已保存成功」。
 * - 「测试连接」真实请求 GET /api/v1/auth/sso/callback（后端固定返回 501「SSO 待配置」），
 *   把真实 HTTP 结果原样反馈，不假装连接成功。
 * - 审计日志的时间范围筛选后端**不支持**，只在前端过滤当前页数据，界面上已注明。
 * - 所有表格数据均来自真实接口，本文件不构造任何假数据。
 */

import { useState } from "react";
import axios from "axios";
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import {
  ApartmentOutlined,
  CheckOutlined,
  CloudSyncOutlined,
  DownloadOutlined,
  EditOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  TeamOutlined,
  UserOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import type { Dayjs } from "dayjs";

import { EmptyHint, KpiCard, KpiGrid, PageHeader, Panel } from "../components/ui";
import {
  useAuditLogs,
  useDepartments,
  useRoles,
  useSystemUsers,
  useTriggerOrgSync,
  useUpdateSystemUser,
} from "../api/hooks";
import {
  AUDIT_ACTION_COLOR,
  AUDIT_ACTION_LABEL,
  ROLE_LABEL,
  type AuditLogItem,
  type RoleConfig,
  type SystemUser,
  type UserRole,
} from "../api/types";
import { downloadCsv, errMsg, fmtDateTime, fmtFromNow } from "../utils/format";

const { Text, Paragraph } = Typography;

// ── 展示辅助 ──────────────────────────────────────────────────────────

/** 角色值 → 中文名（后端可能返回 types.ts 未覆盖的角色，做兜底）。 */
function roleLabel(role?: string | null): string {
  if (!role) return "—";
  return ROLE_LABEL[role as UserRole] ?? role;
}

/** 操作类型 → 中文名（后端 action 为自由字符串，做兜底）。 */
function actionLabel(action?: string | null): string {
  if (!action) return "—";
  return AUDIT_ACTION_LABEL[action] ?? action;
}

const DATA_SCOPE_META: Record<string, { label: string; color: string }> = {
  all: { label: "全公司", color: "blue" },
  department: { label: "本部门", color: "purple" },
  self: { label: "仅本人", color: "default" },
};

/** 数据权限范围标签。 */
function DataScopeTag({ scope }: { scope?: string | null }) {
  if (!scope) return <Tag>未知</Tag>;
  const meta = DATA_SCOPE_META[scope];
  return <Tag color={meta ? meta.color : "default"}>{meta ? meta.label : scope}</Tag>;
}

/** 审计日志详情可能是 JSON 字符串：能解析则格式化，否则原样展示。 */
function prettyDetail(detail?: string | null): string {
  if (!detail) return "—";
  try {
    return JSON.stringify(JSON.parse(detail), null, 0);
  } catch {
    return detail;
  }
}

/** 长文本：截断 + Tooltip。 */
function EllipsisCell({ text, width = 260 }: { text: string; width?: number }) {
  if (!text || text === "—") return <span>—</span>;
  return (
    <Tooltip title={<pre style={{ margin: 0, maxHeight: 320, overflow: "auto" }}>{text}</pre>}>
      <span
        style={{
          display: "inline-block",
          maxWidth: width,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          verticalAlign: "bottom",
        }}
      >
        {text}
      </span>
    </Tooltip>
  );
}

/** 请求失败的统一提示（Alert + 重试）。 */
function ErrorAlert({
  title,
  error,
  onRetry,
}: {
  title: string;
  error: unknown;
  onRetry?: () => void;
}) {
  return (
    <Alert
      type="error"
      showIcon
      style={{ marginBottom: 12 }}
      message={title}
      description={errMsg(error, "请求失败，请稍后重试")}
      action={
        onRetry ? (
          <Button size="small" icon={<ReloadOutlined />} onClick={onRetry}>
            重试
          </Button>
        ) : undefined
      }
    />
  );
}

// ────────────────────────────────────────────────────────────────────
// 页签 1：SSO 配置
// ────────────────────────────────────────────────────────────────────

const SSO_PROTOCOLS = [
  { value: "oidc", label: "OIDC（OpenID Connect）" },
  { value: "ldap", label: "LDAP / AD" },
  { value: "saml", label: "SAML 2.0" },
];

const ENDPOINT_LABEL: Record<string, string> = {
  oidc: "IdP Endpoint（discovery / authorize 地址）",
  ldap: "LDAP 服务器地址",
  saml: "SAML IdP SSO URL",
};

interface SsoFormValues {
  protocol: string;
  client_id: string;
  client_secret: string;
  endpoint: string;
}

interface SsoProbeResult {
  ok: boolean;
  httpStatus: number;
  title: string;
  detail: string;
}

function SsoTab() {
  const [form] = Form.useForm<SsoFormValues>();
  const protocol = Form.useWatch("protocol", form) ?? "oidc";
  const [result, setResult] = useState<SsoProbeResult | null>(null);
  const [testing, setTesting] = useState(false);

  const callbackUrl = `${window.location.origin}/api/v1/auth/sso/callback`;

  /** 真实探测后端 SSO 回调；后端原型阶段固定 501「SSO 待配置」。 */
  async function testConnection() {
    let values: SsoFormValues;
    try {
      values = await form.validateFields(["endpoint"]);
      values = { ...form.getFieldsValue(true), ...values };
    } catch {
      return; // 校验失败，antd 已在表单上提示
    }
    if (!values.endpoint) return;

    setTesting(true);
    try {
      // 用真实请求探测：GET /auth/sso/callback 需要 code 参数，
      // 携带探测用 code 后后端会走到 501「SSO 待配置」分支。
      const res = await axios.get("/api/v1/auth/sso/callback", {
        params: { code: "prototype-connectivity-probe", state: values.protocol },
      });
      setResult({
        ok: true,
        httpStatus: res.status,
        title: `后端返回 HTTP ${res.status}`,
        detail: JSON.stringify(res.data),
      });
    } catch (err) {
      const e = err as {
        response?: { status?: number; data?: { detail?: unknown } };
        message?: string;
      };
      const status = e.response?.status;
      const detail = e.response?.data?.detail;
      const detailText =
        typeof detail === "string"
          ? detail
          : detail === undefined
            ? (e.message ?? "无响应体")
            : JSON.stringify(detail);
      setResult({
        ok: false,
        httpStatus: status ?? 0,
        title: status
          ? `连接测试失败：后端返回 HTTP ${status}`
          : `连接测试失败：${e.message ?? "请求未送达"}`,
        detail:
          status === 501
            ? `${detailText} —— 后端 /api/v1/auth/sso/callback 为原型占位实现，SSO 尚未接入，因此此处不能判定为连接成功。`
            : detailText,
      });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="stack">
      <Alert
        type="info"
        showIcon
        message="原型阶段：SSO 配置未落库"
        description={
          <span>
            本页表单仅保存在浏览器内存中，<b>没有后端持久化接口</b>（后端无 /system/sso 读写端点）。
            刷新页面即丢失；「保存配置」与「测试连接」都不会写入数据库。
          </span>
        }
      />

      <Panel title="SSO 参数配置" extra="仅本地预览，未落库">
        <Form<SsoFormValues>
          form={form}
          layout="vertical"
          initialValues={{ protocol: "oidc" }}
          style={{ maxWidth: 640 }}
        >
          <Form.Item
            name="protocol"
            label="协议"
            rules={[{ required: true, message: "请选择 SSO 协议" }]}
          >
            <Select options={SSO_PROTOCOLS} />
          </Form.Item>

          <Form.Item
            name="endpoint"
            label={ENDPOINT_LABEL[protocol] ?? "Endpoint"}
            rules={[
              { required: true, message: "请填写 Endpoint" },
              {
                validator: (_rule, value: string) => {
                  if (!value) return Promise.resolve();
                  if (/^https:\/\//i.test(value)) return Promise.resolve();
                  if (/^https?:\/\/(localhost|127\.0\.0\.1)([:/]|$)/i.test(value)) {
                    return Promise.resolve();
                  }
                  return Promise.reject(
                    new Error("生产环境 SSO Endpoint 必须使用 https://（仅 localhost 允许 http://）"),
                  );
                },
              },
            ]}
          >
            <Input placeholder="https://idp.example.com/oauth2/authorize" allowClear />
          </Form.Item>

          <Form.Item
            name="client_id"
            label="Client ID"
            rules={[{ required: true, message: "请填写 Client ID" }]}
          >
            <Input placeholder="ai-exam-portal" allowClear />
          </Form.Item>

          <Form.Item
            name="client_secret"
            label="Client Secret"
            rules={[{ required: true, message: "请填写 Client Secret" }]}
          >
            <Input.Password placeholder="••••••••••••" autoComplete="new-password" />
          </Form.Item>

          <Form.Item label="回调地址（由前端 Origin 推导，后端路由 /api/v1/auth/sso/callback）">
            <Input value={callbackUrl} readOnly />
          </Form.Item>

          <Space wrap>
            <Button
              type="primary"
              onClick={() =>
                message.info("原型阶段：SSO 配置未接入后端持久化，仅本地预览（未写入任何存储）")
              }
            >
              保存配置
            </Button>
            <Button loading={testing} onClick={testConnection}>
              测试连接
            </Button>
            <Text type="secondary">后端 SSO 未配置时返回 501「SSO 待配置」</Text>
          </Space>
        </Form>
      </Panel>

      {result ? (
        <Alert
          type={result.ok ? "success" : "error"}
          showIcon
          message={result.title}
          description={
            <div>
              <div style={{ marginBottom: 6 }}>
                {result.ok
                  ? "后端真实返回了成功响应（当前后端不会走到这里）。"
                  : "以下为后端真实返回，未做任何美化或伪造。"}
              </div>
              <pre
                style={{
                  margin: 0,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-all",
                  fontSize: 12,
                }}
              >
                {result.detail}
              </pre>
            </div>
          }
        />
      ) : (
        <Panel title="连接测试结果">
          <EmptyHint text="尚未发起连接测试 —— 点击上方「测试连接」真实请求后端 /api/v1/auth/sso/callback" />
        </Panel>
      )}
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────
// 页签 2：组织架构
// ────────────────────────────────────────────────────────────────────

const SYNC_FREQ_OPTIONS = [
  { value: "daily_0200", label: "每日凌晨 02:00（默认）" },
  { value: "daily_0600", label: "每日凌晨 06:00" },
  { value: "hourly", label: "每小时" },
  { value: "manual", label: "仅手动触发" },
];

function OrgTab() {
  const [hrForm] = Form.useForm();

  const [role, setRole] = useState<string | undefined>();
  const [departmentId, setDepartmentId] = useState<string | undefined>();
  const [keyword, setKeyword] = useState("");
  const [submittedKeyword, setSubmittedKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const departments = useDepartments();
  const users = useSystemUsers({
    page,
    page_size: pageSize,
    role,
    department_id: departmentId,
    keyword: submittedKeyword || undefined,
  });
  const activeUsers = useSystemUsers({ page: 1, page_size: 500 });
  const sync = useTriggerOrgSync();

  const deptList = departments.data ?? [];
  const deptNameById = new Map(deptList.map((d) => [d.id, d.name]));
  const deptOptions = deptList.map((d) => ({ value: d.id, label: d.name }));
  const roleOptions = Object.entries(ROLE_LABEL).map(([value, label]) => ({ value, label }));

  const totalUsers = users.data?.total ?? 0;
  // GET /system/users 没有 is_active 过滤参数：取 <=MAX_PAGE_SIZE(500) 一页在前端统计，
  // 若总人数超过该上限则只能给出近似值（KPI 副标题已注明，不谎报为精确值）。
  const activeSample = activeUsers.data?.items ?? [];
  const totalActive = activeSample.filter((u) => u.is_active).length;
  const activePartial = (activeUsers.data?.total ?? 0) > activeSample.length;
  const listRefreshedAt = users.dataUpdatedAt ? fmtDateTime(new Date(users.dataUpdatedAt).toISOString()) : null;

  const deptColumns: ColumnsType<(typeof deptList)[number]> = [
    { title: "部门名称", dataIndex: "name", key: "name" },
    {
      title: "上级部门",
      dataIndex: "parent_id",
      key: "parent_id",
      render: (pid: string | null) =>
        pid ? (deptNameById.get(pid) ?? <Text type="secondary">未知部门（{pid}）</Text>) : "—",
    },
    {
      title: "外部 ID",
      dataIndex: "external_id",
      key: "external_id",
      render: (v: string | null) => v ?? "—",
    },
  ];

  const userColumns: ColumnsType<SystemUser> = [
    {
      title: "姓名",
      dataIndex: "name",
      key: "name",
      render: (v: string, r) => (
        <Space size={6}>
          <UserOutlined style={{ color: "var(--muted)" }} />
          <span>{v}</span>
          {!r.is_active ? <Tag color="red">已停用</Tag> : null}
        </Space>
      ),
    },
    { title: "邮箱", dataIndex: "email", key: "email", render: (v: string | null) => v ?? "—" },
    {
      title: "角色",
      dataIndex: "role",
      key: "role",
      width: 120,
      render: (v: string) => <Tag color="blue">{roleLabel(v)}</Tag>,
    },
    {
      title: "所属部门",
      dataIndex: "department_name",
      key: "department_name",
      render: (v: string | null, r) =>
        v ?? (r.department_id ? (deptNameById.get(r.department_id) ?? "—") : "—"),
    },
    {
      title: "状态",
      dataIndex: "is_active",
      key: "is_active",
      width: 90,
      render: (v: boolean) => <Tag color={v ? "green" : "default"}>{v ? "已启用" : "已停用"}</Tag>,
    },
    {
      title: "最近登录",
      dataIndex: "last_login_at",
      key: "last_login_at",
      width: 130,
      render: (v: string | null) => (
        <Tooltip title={v ? fmtDateTime(v) : "从未登录"}>{fmtFromNow(v)}</Tooltip>
      ),
    },
  ];

  return (
    <div className="stack">
      <KpiGrid columns={3}>
        <KpiCard
          label="部门数"
          value={deptList.length}
          suffix="个"
          icon={<ApartmentOutlined />}
          tone="blue"
          loading={departments.isLoading}
          trend={<span>来自 GET /system/departments</span>}
        />
        <KpiCard
          label="人员数"
          value={totalUsers}
          suffix="人"
          icon={<TeamOutlined />}
          tone="purple"
          loading={users.isLoading}
          trend={<span>来自 GET /system/users 的 total</span>}
        />
        <KpiCard
          label="已启用人数"
          value={totalActive}
          suffix="人"
          icon={<SafetyCertificateOutlined />}
          tone="green"
          loading={activeUsers.isLoading}
          trend={
            <span>
              {activePartial
                ? `按前 ${activeSample.length} 人统计的近似值（后端无 is_active 过滤参数）`
                : "来自 GET /system/users 的 is_active 统计"}
            </span>
          }
        />
      </KpiGrid>

      <Alert
        type="info"
        showIcon
        message="原型阶段：HR 系统对接配置未落库"
        description="HR API 地址、密钥、同步频率在原型阶段没有后端持久化接口；「手动触发同步」调用的是真实接口 POST /system/org/sync，但后端当前为模拟实现，不会真实拉取 HR 数据。同步记录（上次同步时间 / 结果）后端也未持久化，故本页只能展示列表数据的刷新时间。"
      />

      <Panel
        title="HR 系统对接配置"
        extra="仅本地预览，未落库"
      >
        <Form
          form={hrForm}
          layout="vertical"
          initialValues={{ sync_frequency: "daily_0200" }}
          style={{ maxWidth: 640 }}
        >
          <Form.Item
            name="api_url"
            label="HR 系统 API 地址"
            rules={[
              { required: true, message: "请填写 HR 系统 API 地址" },
              { type: "url", message: "请输入合法的 URL" },
            ]}
          >
            <Input placeholder="https://hr.example.com/api/v1/org/departments" allowClear />
          </Form.Item>
          <Form.Item
            name="api_secret"
            label="API 密钥"
            rules={[{ required: true, message: "请填写 HR API 密钥" }]}
          >
            <Input.Password placeholder="••••••••••••" autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="sync_frequency" label="同步频率（默认每日凌晨 02:00）">
            <Select options={SYNC_FREQ_OPTIONS} />
          </Form.Item>
          <Button
            onClick={() =>
              message.info("原型阶段：HR 对接配置未接入后端持久化，仅本地预览（未写入任何存储）")
            }
          >
            保存对接配置
          </Button>
        </Form>
      </Panel>

      <Panel
        title="同步状态"
        extra={listRefreshedAt ? `人员列表最近刷新：${listRefreshedAt}` : undefined}
      >
        <Descriptions column={2} size="small" style={{ marginBottom: 12 }}>
          <Descriptions.Item label="上次同步时间">—（后端未持久化同步记录）</Descriptions.Item>
          <Descriptions.Item label="上次同步结果">—（后端未持久化同步记录）</Descriptions.Item>
          <Descriptions.Item label="当前部门数">{departments.isLoading ? "加载中…" : `${deptList.length} 个`}</Descriptions.Item>
          <Descriptions.Item label="当前人员数">{users.isLoading ? "加载中…" : `${totalUsers} 人`}</Descriptions.Item>
        </Descriptions>
        {sync.isError ? (
          <ErrorAlert title="触发同步失败" error={sync.error} onRetry={() => sync.mutate()} />
        ) : null}
        <Button
          type="primary"
          icon={<CloudSyncOutlined />}
          loading={sync.isPending}
          onClick={async () => {
            try {
              const res = await sync.mutateAsync();
              message.success(res.message);
            } catch (err) {
              message.error(errMsg(err, "触发同步失败"));
            }
          }}
        >
          手动触发同步
        </Button>
        <Text type="secondary" style={{ marginLeft: 12 }}>
          调用真实接口 POST /system/org/sync，成功后自动刷新部门与人员列表
        </Text>
      </Panel>

      <Panel title="部门列表" extra={`共 ${deptList.length} 个`}>
        {departments.isError ? (
          <ErrorAlert
            title="部门列表加载失败"
            error={departments.error}
            onRetry={() => departments.refetch()}
          />
        ) : null}
        <Table
          rowKey="id"
          size="small"
          loading={departments.isLoading}
          columns={deptColumns}
          dataSource={deptList}
          pagination={false}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="后端返回 0 个部门 —— 请点击「手动触发同步」或通过 HR 系统导入"
              />
            ),
          }}
        />
      </Panel>

      <Panel title="人员列表" extra={`共 ${totalUsers} 人`}>
        {users.isError ? (
          <ErrorAlert title="人员列表加载失败" error={users.error} onRetry={() => users.refetch()} />
        ) : null}
        <Space wrap style={{ marginBottom: 12 }}>
          <Input.Search
            placeholder="按姓名搜索"
            style={{ width: 220 }}
            value={keyword}
            allowClear
            onChange={(e) => setKeyword(e.target.value)}
            onSearch={(v) => {
              setSubmittedKeyword(v.trim());
              setPage(1);
            }}
          />
          <Select
            placeholder="角色"
            style={{ width: 150 }}
            allowClear
            value={role}
            options={roleOptions}
            onChange={(v) => {
              setRole(v);
              setPage(1);
            }}
          />
          <Select
            placeholder="所属部门"
            style={{ width: 200 }}
            allowClear
            value={departmentId}
            options={deptOptions}
            onChange={(v) => {
              setDepartmentId(v);
              setPage(1);
            }}
          />
          <Button
            icon={<ReloadOutlined />}
            loading={users.isFetching}
            onClick={() => users.refetch()}
          >
            刷新
          </Button>
        </Space>
        <Table<SystemUser>
          rowKey="id"
          size="small"
          loading={users.isLoading}
          columns={userColumns}
          dataSource={users.data?.items ?? []}
          pagination={{
            current: page,
            pageSize,
            total: totalUsers,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p, ps) => {
              setPage(p);
              setPageSize(ps);
            },
          }}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="没有符合条件的人员（筛选条件走的是后端参数）"
              />
            ),
          }}
        />
      </Panel>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────
// 页签 3：权限管理
// ────────────────────────────────────────────────────────────────────

function PermissionMatrix({ roles }: { roles: RoleConfig[] }) {
  // 列 = 所有角色 permissions 的并集（去重 + 字典序）
  const permissionUnion = Array.from(
    new Set(roles.flatMap((r) => r.permissions ?? [])),
  ).sort((a, b) => a.localeCompare(b));

  const columns: ColumnsType<RoleConfig> = [
    {
      title: "角色",
      dataIndex: "name",
      key: "name",
      width: 160,
      fixed: "left",
      render: (v: string, r) => (
        <Space direction="vertical" size={0}>
          <b>{v}</b>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {r.role}
          </Text>
        </Space>
      ),
    },
    {
      title: "数据权限",
      dataIndex: "data_scope",
      key: "data_scope",
      width: 110,
      fixed: "left",
      render: (v: string) => <DataScopeTag scope={v} />,
    },
    ...permissionUnion.map((perm) => ({
      title: (
        <Tooltip title={perm}>
          <span style={{ fontSize: 11 }}>{perm}</span>
        </Tooltip>
      ),
      key: perm,
      width: 120,
      align: "center" as const,
      render: (_: unknown, r: RoleConfig) =>
        (r.permissions ?? []).includes(perm) ? (
          <CheckOutlined style={{ color: "var(--green-deep)" }} />
        ) : (
          <span style={{ color: "var(--muted-2)" }}>—</span>
        ),
    })),
  ];

  if (!roles.length) {
    return <EmptyHint text="后端未返回任何角色配置" />;
  }

  return (
    <Table<RoleConfig>
      rowKey="role"
      size="small"
      columns={columns}
      dataSource={roles}
      pagination={false}
      scroll={{ x: 900 }}
    />
  );
}

interface AssignFormValues {
  role: UserRole;
  department_id?: string | null;
}

function UserAssignModal({
  user,
  open,
  onClose,
  roleOptions,
  deptOptions,
}: {
  user: SystemUser | null;
  open: boolean;
  onClose: () => void;
  roleOptions: { value: string; label: string }[];
  deptOptions: { value: string; label: string }[];
}) {
  const update = useUpdateSystemUser();
  const [form] = Form.useForm<AssignFormValues>();

  const handleOk = async () => {
    if (!user) return;
    try {
      const values = await form.validateFields();
      await update.mutateAsync({
        userId: user.id,
        role: values.role,
        department_id: values.department_id ?? null,
      });
      message.success(`已更新 ${user.name} 的角色 / 部门`);
      onClose();
    } catch (err) {
      if ((err as { errorFields?: unknown }).errorFields) return; // 表单校验失败
      message.error(errMsg(err, "更新用户角色失败"));
    }
  };

  return (
    <Modal
      open={open}
      title={user ? `调整角色 / 部门 —— ${user.name}` : "调整角色 / 部门"}
      onCancel={onClose}
      onOk={handleOk}
      confirmLoading={update.isPending}
      destroyOnHidden
      okText="提交"
      cancelText="取消"
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="仅角色与部门会通过 PUT /system/users/{id} 落库"
        description="姓名与启用状态也可由该接口更新，但本弹窗只提交角色与部门两项。"
      />
      <Form
        form={form}
        layout="vertical"
        preserve={false}
        initialValues={{ role: user?.role, department_id: user?.department_id ?? undefined }}
      >
        <Form.Item
          name="role"
          label="角色"
          rules={[{ required: true, message: "请选择角色" }]}
        >
          <Select options={roleOptions} placeholder="请选择角色" />
        </Form.Item>
        <Form.Item name="department_id" label="所属部门">
          <Select options={deptOptions} placeholder="不指定（清空部门）" allowClear />
        </Form.Item>
      </Form>
    </Modal>
  );
}

function PermissionsTab() {
  const rolesQuery = useRoles();
  const departments = useDepartments();

  const roles = rolesQuery.data ?? [];
  const [roleFilter, setRoleFilter] = useState<string | undefined>();
  const [keyword, setKeyword] = useState("");
  const [submittedKeyword, setSubmittedKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);

  const users = useSystemUsers({
    page,
    page_size: pageSize,
    role: roleFilter,
    keyword: submittedKeyword || undefined,
  });

  const [assignTarget, setAssignTarget] = useState<SystemUser | null>(null);

  // 角色下拉的候选项优先来自后端 useRoles()，避免前后端角色清单漂移
  const roleOptions = roles.length
    ? roles.map((r) => ({ value: r.role as string, label: r.name }))
    : Object.entries(ROLE_LABEL).map(([value, label]) => ({ value, label }));

  const deptOptions = (departments.data ?? []).map((d) => ({ value: d.id, label: d.name }));

  const columns: ColumnsType<SystemUser> = [
    {
      title: "姓名",
      dataIndex: "name",
      key: "name",
      render: (v: string, r) => (
        <Space size={6}>
          <span>{v}</span>
          {!r.is_active ? <Tag color="red">已停用</Tag> : null}
        </Space>
      ),
    },
    {
      title: "当前角色",
      dataIndex: "role",
      key: "role",
      width: 140,
      render: (v: string) => <Tag color="blue">{roleLabel(v)}</Tag>,
    },
    {
      title: "所属部门",
      dataIndex: "department_name",
      key: "department_name",
      render: (v: string | null) => v ?? "—",
    },
    {
      title: "操作",
      key: "action",
      width: 150,
      render: (_, record) => (
        <Button
          size="small"
          icon={<EditOutlined />}
          onClick={() => setAssignTarget(record)}
        >
          调整角色 / 部门
        </Button>
      ),
    },
  ];

  const total = users.data?.total ?? 0;

  return (
    <div className="stack">
      <Alert
        type="info"
        showIcon
        message="原型阶段：功能权限矩阵与数据权限范围只读"
        description="角色清单与权限来自真实接口 GET /system/roles；后端未提供角色权限的写入接口，故矩阵与数据权限范围为只读展示。「用户角色分配」是真实落库操作（PUT /system/users/{id}）。"
      />

      <Panel title="角色 × 功能权限矩阵" extra={`${roles.length} 个角色 / ${new Set(roles.flatMap((r) => r.permissions ?? [])).size} 项权限`}>
        {rolesQuery.isError ? (
          <ErrorAlert
            title="角色配置加载失败"
            error={rolesQuery.error}
            onRetry={() => rolesQuery.refetch()}
          />
        ) : null}
        <PermissionMatrix roles={roles} />
      </Panel>

      <Panel title="数据权限范围" extra="只读（后端无写入接口）">
        <Table<RoleConfig>
          rowKey="role"
          size="small"
          loading={rolesQuery.isLoading}
          columns={[
            { title: "角色", dataIndex: "name", key: "name", width: 180 },
            {
              title: "数据权限范围",
              dataIndex: "data_scope",
              key: "data_scope",
              width: 160,
              render: (v: string) => <DataScopeTag scope={v} />,
            },
            {
              title: "说明",
              key: "desc",
              render: (_, r) => {
                const meta = DATA_SCOPE_META[r.data_scope];
                return (
                  <Space wrap size={4}>
                    <Text type="secondary">
                      {r.data_scope === "all"
                        ? "可查看全公司数据"
                        : r.data_scope === "department"
                          ? "仅可查看本部门数据"
                          : r.data_scope === "self"
                            ? "仅可查看本人数据"
                            : "未定义的范围（后端返回原值）"}
                    </Text>
                    {!meta ? <Tag color="orange">{r.data_scope || "空"}</Tag> : null}
                  </Space>
                );
              },
            },
          ]}
          dataSource={roles}
          pagination={false}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无角色配置" /> }}
        />
      </Panel>

      <Panel title="用户角色分配" extra={`共 ${total} 人`}>
        {users.isError ? (
          <ErrorAlert title="人员列表加载失败" error={users.error} onRetry={() => users.refetch()} />
        ) : null}
        <Space wrap style={{ marginBottom: 12 }}>
          <Input.Search
            placeholder="按姓名搜索"
            style={{ width: 220 }}
            value={keyword}
            allowClear
            onChange={(e) => setKeyword(e.target.value)}
            onSearch={(v) => {
              setSubmittedKeyword(v.trim());
              setPage(1);
            }}
          />
          <Select
            placeholder="按角色筛选"
            style={{ width: 170 }}
            allowClear
            value={roleFilter}
            options={roleOptions}
            onChange={(v) => {
              setRoleFilter(v);
              setPage(1);
            }}
          />
          <Button icon={<ReloadOutlined />} loading={users.isFetching} onClick={() => users.refetch()}>
            刷新
          </Button>
        </Space>
        <Table<SystemUser>
          rowKey="id"
          size="small"
          loading={users.isLoading}
          columns={columns}
          dataSource={users.data?.items ?? []}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p, ps) => {
              setPage(p);
              setPageSize(ps);
            },
          }}
          locale={{
            emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有符合条件的人员" />,
          }}
        />
      </Panel>

      <UserAssignModal
        user={assignTarget}
        open={Boolean(assignTarget)}
        onClose={() => setAssignTarget(null)}
        roleOptions={roleOptions}
        deptOptions={deptOptions}
      />
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────
// 页签 4：审计日志
// ────────────────────────────────────────────────────────────────────

function AuditTab() {
  const [action, setAction] = useState<string | undefined>();
  const [userId, setUserId] = useState<string | undefined>();
  const [operatorKeyword, setOperatorKeyword] = useState("");
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const logs = useAuditLogs({
    page,
    page_size: pageSize,
    action,
    user_id: userId,
  });

  // 操作者下拉：从真实人员接口取（后端按 user_id 精确筛选）
  const users = useSystemUsers({ page: 1, page_size: 200 });
  const userOptions = (users.data?.items ?? []).map((u) => ({ value: u.id, label: u.name }));

  const actionOptions = Object.entries(AUDIT_ACTION_LABEL).map(([value, label]) => ({
    value,
    label,
  }));

  const rawItems = logs.data?.items ?? [];

  // 后端不支持时间参数 → 仅前端过滤「当前页」数据（界面上已注明）
  const items = rawItems.filter((it) => {
    if (range && range[0] && range[1]) {
      const t = new Date(it.created_at).getTime();
      if (Number.isNaN(t)) return false;
      if (t < range[0].startOf("day").valueOf() || t > range[1].endOf("day").valueOf()) return false;
    }
    if (operatorKeyword && !(it.user_name ?? "").includes(operatorKeyword)) return false;
    return true;
  });

  const clientFiltered = (Boolean(range && range[0] && range[1])) || Boolean(operatorKeyword);

  const columns: ColumnsType<AuditLogItem> = [
    {
      title: "时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 150,
      render: (v: string) => fmtDateTime(v),
    },
    {
      title: "操作者",
      dataIndex: "user_name",
      key: "user_name",
      width: 130,
      render: (v: string | null, r) =>
        v ? (
          <Tooltip title={`user_id: ${r.user_id ?? "—"}`}>{v}</Tooltip>
        ) : (
          <Text type="secondary">系统 / 匿名</Text>
        ),
    },
    {
      title: "操作类型",
      dataIndex: "action",
      key: "action",
      width: 100,
      render: (v: string) => (
        <Tag color={AUDIT_ACTION_COLOR[v] ?? "default"}>{actionLabel(v)}</Tag>
      ),
    },
    {
      title: "对象",
      key: "target",
      width: 190,
      render: (_, r) => {
        if (!r.target_type && !r.target_id) return "—";
        const full = `${r.target_type ?? "—"} / ${r.target_id ?? "—"}`;
        return <EllipsisCell text={full} width={170} />;
      },
    },
    {
      title: "详情",
      dataIndex: "detail",
      key: "detail",
      render: (v: string | null) => <EllipsisCell text={prettyDetail(v)} />,
    },
    {
      title: "IP",
      dataIndex: "ip_address",
      key: "ip_address",
      width: 130,
      render: (v: string | null) => v ?? "—",
    },
  ];

  const total = logs.data?.total ?? 0;

  function handleExport() {
    if (!items.length) {
      message.info("当前没有可导出的日志（当前筛选结果为空）");
      return;
    }
    downloadCsv(
      `审计日志_${new Date().toISOString().slice(0, 10)}`,
      ["时间", "操作者", "操作类型", "对象类型", "对象 ID", "详情", "IP"],
      items.map((l) => [
        fmtDateTime(l.created_at),
        l.user_name ?? "",
        actionLabel(l.action),
        l.target_type ?? "",
        l.target_id ?? "",
        prettyDetail(l.detail),
        l.ip_address ?? "",
      ]),
    );
    message.success(
      clientFiltered
        ? `已导出当前页筛选后的 ${items.length} 条日志（不含其他分页数据）`
        : `已导出当前页 ${items.length} 条日志（不含其他分页数据）`,
    );
  }

  return (
    <div className="stack">
      <Alert
        type="info"
        showIcon
        message="筛选能力说明（原型阶段）"
        description={
          <span>
            「操作类型」与「操作者」下拉走后端参数（action / user_id），分页也是后端分页；
            <b>时间范围后端不支持</b>，RangePicker 只用于前端过滤<b>当前页</b>已加载的数据，不代表全量日志。
            「操作者关键字」同样是当前页前端过滤。
          </span>
        }
      />

      <Panel
        title="日志筛选"
        extra={clientFiltered ? `当前页 ${rawItems.length} 条 → 前端过滤后 ${items.length} 条` : `后端返回 ${rawItems.length} 条`}
      >
        <Space wrap>
          <Select
            placeholder="操作类型"
            style={{ width: 150 }}
            allowClear
            value={action}
            options={actionOptions}
            onChange={(v) => {
              setAction(v);
              setPage(1);
            }}
          />
          <Select
            placeholder="操作者（按 user_id 走后端筛选）"
            style={{ width: 240 }}
            allowClear
            showSearch
            optionFilterProp="label"
            loading={users.isLoading}
            value={userId}
            options={userOptions}
            onChange={(v) => {
              setUserId(v);
              setPage(1);
            }}
          />
          <DatePicker.RangePicker value={range} onChange={setRange} />
          <Input
            placeholder="操作者姓名（仅过滤当前页）"
            style={{ width: 200 }}
            allowClear
            value={operatorKeyword}
            onChange={(e) => setOperatorKeyword(e.target.value)}
          />
          <Button
            icon={<ReloadOutlined />}
            loading={logs.isFetching}
            onClick={() => logs.refetch()}
          >
            刷新
          </Button>
          <Button type="primary" icon={<DownloadOutlined />} onClick={handleExport}>
            导出审计日志
          </Button>
        </Space>
      </Panel>

      <Panel title="日志列表" extra={`共 ${total} 条`}>
        {logs.isError ? (
          <ErrorAlert title="审计日志加载失败" error={logs.error} onRetry={() => logs.refetch()} />
        ) : null}
        {clientFiltered ? (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message="时间范围 / 操作者姓名关键字只过滤当前页数据，后端未收到时间参数"
          />
        ) : null}
        <Table<AuditLogItem>
          rowKey="id"
          size="small"
          loading={logs.isLoading}
          columns={columns}
          dataSource={items}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p, ps) => {
              setPage(p);
              setPageSize(ps);
            },
          }}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={
                  rawItems.length
                    ? "当前页没有符合筛选条件的日志"
                    : "后端暂无审计日志记录（GET /system/audit-logs 返回 0 条）"
                }
              />
            ),
          }}
        />
      </Panel>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────
// 页面
// ────────────────────────────────────────────────────────────────────

export default function SystemSettings() {
  const [activeKey, setActiveKey] = useState("sso");
  return (
    <div>
      <PageHeader
        eyebrow="IT 管理员"
        title="系统管理"
        description="SSO 配置 / 组织架构 / 权限管理 / 审计日志 —— 仅 IT 管理员可访问（/system）"
      />
      <Card styles={{ body: { paddingTop: 8 } }}>
        <Tabs
          activeKey={activeKey}
          onChange={setActiveKey}
          // 切换页签保留已填写的表单与筛选条件
          destroyOnHidden={false}
          items={[
            { key: "sso", label: "SSO 配置", children: <SsoTab /> },
            { key: "org", label: "组织架构", children: <OrgTab /> },
            { key: "permissions", label: "权限管理", children: <PermissionsTab /> },
            { key: "audit", label: "审计日志", children: <AuditTab /> },
          ]}
        />
        <Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}>
          本页所有表格数据均来自真实后端接口；标注「原型未落库」的表单无持久化接口，不会写入数据库。
        </Paragraph>
      </Card>
    </div>
  );
}
