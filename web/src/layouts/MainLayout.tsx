/**
 * 应用主布局 — 对齐 UIdesign 评审稿。
 *
 * 结构：深海军蓝侧栏（品牌 / 分组导航 / 当前用户）+ 顶栏（面包屑、页面标题、
 * 消息中心、角色切换、退出）+ 内容区。
 *
 * 导航项与角色矩阵来自 config/menu.ts（与后端 require_role 对齐），
 * 侧栏不再自行维护一份菜单，避免「菜单能点但接口 403」。
 */

import { useMemo } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { Badge, Button, Dropdown, Popover, Spin, Tooltip, message } from "antd";
import {
  BellOutlined,
  DownOutlined,
  LogoutOutlined,
  ReloadOutlined,
  UserOutlined,
} from "@ant-design/icons";

import { NAV_GROUPS, ROUTE_BREADCRUMB, ROUTE_TITLE, selectedNavKey } from "../config/menu";
import { ROLE_LABEL, type UserRole } from "../api/types";
import { useAuthStore } from "../store/auth";
import { useInbox, usePendingQuestions } from "../api/hooks";
import { fmtFromNow } from "../utils/format";
import RoleGuard from "../components/AccessGuard";

const ALL_ROLES: UserRole[] = [
  "expert",
  "hr_admin",
  "dept_manager",
  "compliance",
  "it_admin",
  "employee",
];

export default function MainLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, loading, logout, switchRole } = useAuthStore();

  const role = user?.role;
  const selected = selectedNavKey(location.pathname);

  // 待审核题目数作为侧栏徽标（仅专家角色会请求，其他角色接口会 403）
  const pendingQuery = usePendingQuestions(
    role === "expert" ? { page: 1, page_size: 1 } : {},
  );
  const pendingCount = role === "expert" ? (pendingQuery.data?.total ?? 0) : 0;

  const inbox = useInbox(Boolean(user));

  const navGroups = useMemo(
    () =>
      NAV_GROUPS.map((g) => ({
        ...g,
        items: g.items.filter((i) => (role ? i.roles.includes(role) : false)),
      })).filter((g) => g.items.length > 0),
    [role],
  );

  const crumbs = useMemo(() => {
    const base = ROUTE_BREADCRUMB[location.pathname];
    if (base) return base;
    // 详情页（/documents/:id 等）在标题链路末尾补「详情」
    const title = ROUTE_TITLE[location.pathname];
    if (title) return [{ title }];
    const parent = `/${location.pathname.split("/").filter(Boolean)[0] ?? ""}`;
    const parentCrumbs = ROUTE_BREADCRUMB[parent] ?? [];
    return [...parentCrumbs, { title: "详情" }];
  }, [location.pathname]);

  const pageTitle = useMemo(() => {
    const t = ROUTE_TITLE[location.pathname];
    if (t) return t;
    const parent = `/${location.pathname.split("/").filter(Boolean)[0] ?? ""}`;
    return `${ROUTE_TITLE[parent] ?? "页面"}详情`;
  }, [location.pathname]);

  const handleLogout = () => {
    logout();
    navigate("/login", { replace: true });
  };

  const handleSwitchRole = async (next: UserRole) => {
    try {
      await switchRole(next);
      message.success(`已切换为「${ROLE_LABEL[next]}」视角`);
      navigate("/", { replace: true });
    } catch {
      message.error("角色切换失败");
    }
  };

  const initials = (user?.name ?? "未").trim().slice(0, 1);

  const inboxContent = (
    <div style={{ width: 340 }}>
      <div className="panel-head" style={{ marginBottom: 8 }}>
        <h3 style={{ margin: 0, fontSize: 13 }}>消息中心</h3>
        <Button
          type="text"
          size="small"
          icon={<ReloadOutlined />}
          onClick={() => inbox.refetch()}
        />
      </div>
      {inbox.isLoading ? (
        <div style={{ padding: 16, textAlign: "center" }}>
          <Spin size="small" />
        </div>
      ) : (inbox.data?.items?.length ?? 0) === 0 ? (
        <div className="app-empty">暂无待办与通知</div>
      ) : (
        <div style={{ maxHeight: 380, overflowY: "auto" }}>
          {inbox.data?.items.map((item, idx) => (
            <div
              key={`${item.type}-${idx}`}
              className="activity"
              style={{ cursor: item.link ? "pointer" : "default" }}
              onClick={() => {
                if (item.link) navigate(item.link);
              }}
            >
              <i
                className="dot"
                style={
                  item.level === "warn"
                    ? { background: "var(--amber)", boxShadow: "0 0 0 4px var(--amber-soft)" }
                    : item.level === "success"
                      ? { background: "var(--green)", boxShadow: "0 0 0 4px var(--green-soft)" }
                      : undefined
                }
              />
              <div>
                <b>{item.title}</b>
                <small>{item.detail}</small>
              </div>
              <time>{item.created_at ? fmtFromNow(item.created_at) : ""}</time>
            </div>
          ))}
        </div>
      )}
    </div>
  );

  return (
    <div className="app-shell">
      <aside className="app-side">
        <div className="app-brand">
          <span className="app-brand-dot">✓</span>
          <span>
            制度传达中心
            <small>AI 试题生成系统</small>
          </span>
        </div>

        {navGroups.map((group) => (
          <div key={group.group}>
            <div className="app-side-group">{group.group}</div>
            {group.items.map((item) => (
              <div
                key={item.key}
                className={`app-nav${selected === item.key ? " active" : ""}`}
                onClick={() => navigate(item.key)}
              >
                <span className="ico">{item.icon}</span>
                <span>{item.label}</span>
                {item.badge === "pendingQuestions" && pendingCount > 0 ? (
                  <span className="badge">{pendingCount}</span>
                ) : null}
              </div>
            ))}
          </div>
        ))}

        <Dropdown
          trigger={["click"]}
          menu={{
            items: [
              { key: "profile", label: "个人中心", icon: <UserOutlined /> },
              { type: "divider" as const },
              { key: "logout", label: "退出登录", icon: <LogoutOutlined />, danger: true },
            ],
            onClick: ({ key }) => {
              if (key === "profile") navigate("/profile");
              if (key === "logout") handleLogout();
            },
          }}
        >
          <div className="app-user">
            <span className="app-avatar">{initials}</span>
            <div style={{ minWidth: 0 }}>
              <b>{user?.name ?? "未登录"}</b>
              <small>
                {role ? ROLE_LABEL[role] : "—"}
                {user?.department_name ? ` · ${user.department_name}` : ""}
              </small>
            </div>
          </div>
        </Dropdown>
      </aside>

      <div className="app-main">
        <header className="app-topbar">
          <div style={{ minWidth: 0 }}>
            <div className="eyebrow">
              {crumbs
                .map((c) => c.title)
                .join(" / ")}
            </div>
            <div className="title">{pageTitle}</div>
          </div>

          <div className="app-actions">
            <Popover
              content={inboxContent}
              trigger="click"
              placement="bottomRight"
              title={null}
              arrow={false}
            >
              <Badge count={inbox.data?.unread ?? 0} size="small" offset={[-2, 2]}>
                <Button icon={<BellOutlined />}>消息</Button>
              </Badge>
            </Popover>

            <Tooltip title="原型阶段：一键切换角色视角，用于评审各角色的页面权限">
              <Dropdown
                trigger={["click"]}
                menu={{
                  selectedKeys: role ? [role] : [],
                  items: ALL_ROLES.map((r) => ({ key: r, label: ROLE_LABEL[r] })),
                  onClick: ({ key }) => handleSwitchRole(key as UserRole),
                }}
              >
                <Button loading={loading} icon={<UserOutlined />}>
                  {role ? ROLE_LABEL[role] : "选择角色"} <DownOutlined />
                </Button>
              </Dropdown>
            </Tooltip>

            <Button icon={<LogoutOutlined />} onClick={handleLogout}>
              退出
            </Button>
          </div>
        </header>

        <main className="app-body">
          <RoleGuard>
            <Outlet />
          </RoleGuard>
        </main>
      </div>
    </div>
  );
}
