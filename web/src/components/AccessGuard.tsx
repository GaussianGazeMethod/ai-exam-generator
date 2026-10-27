/**
 * 路由级角色守卫。
 *
 * 与 config/menu.ts 的 ROUTE_ROLES 共用一份角色矩阵；越权访问时
 * 不静默跳转（用户会以为是 bug），而是明确告知所需角色并提供返回入口。
 */

import { Navigate, useLocation } from "react-router-dom";
import { Button, Result } from "antd";

import { rolesForPath } from "../config/menu";
import { ROLE_LABEL } from "../api/types";
import { useAuthStore } from "../store/auth";

export default function RoleGuard({ children }: { children: React.ReactNode }) {
  const { user } = useAuthStore();
  const location = useLocation();

  const allowed = rolesForPath(location.pathname);
  if (!allowed || !user) return <>{children}</>;

  if (!allowed.includes(user.role)) {
    return (
      <Result
        status="403"
        title="当前角色无权访问该页面"
        subTitle={`该页面仅对「${allowed.map((r) => ROLE_LABEL[r]).join(" / ")}」开放，当前登录角色为「${ROLE_LABEL[user.role]}」。`}
        extra={
          <Button type="primary" href="/">
            返回工作台
          </Button>
        }
      />
    );
  }

  return <>{children}</>;
}

/** 未登录跳转（保留原路径，登录后回到原页面）。 */
export function LoginRedirect() {
  const location = useLocation();
  return <Navigate to="/login" replace state={{ from: location.pathname }} />;
}
