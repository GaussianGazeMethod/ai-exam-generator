/**
 * 设计系统与信息架构常量 — 对齐 spec/页面层级树与核心服务模块.md 第四节的
 * 「页面 × 角色」矩阵，以及 UIdesign 评审稿的导航分组。
 *
 * 导航、路由守卫、面包屑三处共用本文件，避免「菜单能点但页面 403」
 * 或「页面能开但菜单没有」这类不一致（自测清单 1.5 类缺陷）。
 */

import type { UserRole } from "../api/types";

export interface NavItem {
  /** 路由路径，也是菜单选中键 */
  key: string;
  label: string;
  icon?: string;
  roles: UserRole[];
  /** 需要动态徽标（如待审核数量）时标记 */
  badge?: "pendingQuestions";
}

/**
 * 侧栏导航。
 *
 * 注意：后端接口的角色限制必须与这里的 roles 一致，
 * 例如 /statistics/by-question 仅 expert/hr_admin 可访问。
 */
export const NAV_GROUPS: { group: string; items: NavItem[] }[] = [
  {
    group: "工作空间",
    items: [
      { key: "/", label: "工作台", icon: "⌂", roles: ["expert", "hr_admin", "dept_manager", "compliance", "it_admin", "employee"] },
      { key: "/documents", label: "制度管理", icon: "▤", roles: ["expert"] },
      { key: "/review", label: "题目审核", icon: "◇", roles: ["expert"], badge: "pendingQuestions" },
      { key: "/questions", label: "题库管理", icon: "▣", roles: ["expert"] },
      { key: "/exams", label: "考试管理", icon: "□", roles: ["hr_admin"] },
      // 后端 overview/by-chapter/dashboard 对 hr_admin 与 compliance 同时开放；
      // 合规角色点进来看到的是「制度维度」视图（个人明细在页面上被隐藏，见 StatisticsDashboard）。
      { key: "/statistics", label: "统计看板", icon: "◒", roles: ["hr_admin", "compliance"] },
      { key: "/statistics/dept", label: "部门统计", icon: "◔", roles: ["dept_manager"] },
      { key: "/statistics/compliance", label: "合规全景", icon: "◎", roles: ["compliance"] },
      { key: "/statistics/question-quality", label: "题目质量分析", icon: "◑", roles: ["expert"] },
    ],
  },
  {
    group: "系统",
    items: [
      { key: "/system", label: "系统管理", icon: "⚙", roles: ["it_admin"] },
      { key: "/profile", label: "个人中心", icon: "♙", roles: ["expert", "hr_admin", "dept_manager", "compliance", "it_admin", "employee"] },
      { key: "/my-exams", label: "我的考试", icon: "✎", roles: ["employee"] },
    ],
  },
];

/** 当前角色可见的导航项。 */
export function navItemsForRole(role: UserRole | undefined): NavItem[] {
  if (!role) return [];
  return NAV_GROUPS.flatMap((g) => g.items).filter((i) => i.roles.includes(role));
}

/** 页面标题（路由 → 标题）。用于顶部栏与面包屑。 */
export const ROUTE_TITLE: Record<string, string> = {
  "/": "工作台",
  "/documents": "制度管理",
  "/documents/upload": "上传制度",
  "/review": "题目审核",
  "/questions": "题库管理",
  "/exams": "考试管理",
  "/exams/create": "智能组卷",
  "/statistics": "统计看板",
  "/statistics/dept": "部门统计",
  "/statistics/compliance": "合规全景",
  "/statistics/question-quality": "题目质量分析",
  "/system": "系统管理",
  "/profile": "个人中心",
  "/my-exams": "我的考试",
};

/**
 * 面包屑（路由 → 层级链路）。
 * 详情类页面（带 ID）在页面内自行补充末级节点。
 */
export const ROUTE_BREADCRUMB: Record<string, { title: string; path?: string }[]> = {
  "/": [{ title: "工作台" }],
  "/documents": [{ title: "制度管理" }],
  "/documents/upload": [{ title: "制度管理", path: "/documents" }, { title: "上传制度" }],
  "/review": [{ title: "题目审核" }],
  "/questions": [{ title: "题库管理" }],
  "/exams": [{ title: "考试管理" }],
  "/exams/create": [{ title: "考试管理", path: "/exams" }, { title: "智能组卷" }],
  "/statistics": [{ title: "统计看板", path: "/statistics" }, { title: "HR 全景" }],
  "/statistics/dept": [{ title: "统计看板" }, { title: "部门统计" }],
  "/statistics/compliance": [{ title: "统计看板" }, { title: "合规全景" }],
  "/statistics/question-quality": [{ title: "统计看板" }, { title: "题目质量分析" }],
  "/system": [{ title: "系统管理" }],
  "/profile": [{ title: "个人中心" }],
  "/my-exams": [{ title: "我的考试" }],
};

/**
 * 路由级角色守卫规则。
 *
 * 与后端 require_role 保持一致（不一致会导致页面能打开但接口 403）：
 * - /statistics          → hr_admin
 * - /statistics/dept     → dept_manager
 * - /statistics/compliance → compliance
 * - /statistics/question-quality → expert, hr_admin
 */
export const ROUTE_ROLES: Record<string, UserRole[]> = {
  "/documents": ["expert"],
  "/review": ["expert"],
  "/questions": ["expert"],
  "/exams": ["hr_admin"],
  "/statistics": ["hr_admin", "compliance"],
  "/statistics/dept": ["dept_manager"],
  "/statistics/compliance": ["compliance"],
  "/statistics/question-quality": ["expert", "hr_admin"],
  "/system": ["it_admin"],
  "/profile": ["expert", "hr_admin", "dept_manager", "compliance", "it_admin", "employee"],
  "/my-exams": ["employee"],
};

/** 取路径匹配的角色限制（支持 /exams/create 这类子路径）。 */
export function rolesForPath(pathname: string): UserRole[] | undefined {
  if (ROUTE_ROLES[pathname]) return ROUTE_ROLES[pathname];
  const segs = pathname.split("/").filter(Boolean);
  while (segs.length > 1) {
    segs.pop();
    const candidate = `/${segs.join("/")}`;
    if (ROUTE_ROLES[candidate]) return ROUTE_ROLES[candidate];
  }
  return undefined;
}

/** 当前路径对应的菜单高亮键（最长匹配）。 */
export function selectedNavKey(pathname: string): string {
  const items = NAV_GROUPS.flatMap((g) => g.items);
  if (pathname === "/") return "/";
  const matches = items
    .filter((i) => i.key !== "/" && (pathname === i.key || pathname.startsWith(`${i.key}/`)))
    .sort((a, b) => b.key.length - a.key.length);
  return matches[0]?.key ?? pathname;
}
