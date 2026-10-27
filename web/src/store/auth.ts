import { create } from "zustand";
import api from "../api/client";
import type { User, UserRole } from "../api/types";

interface AuthState {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (role?: UserRole) => Promise<void>;
  switchRole: (role: UserRole) => Promise<void>;
  logout: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: JSON.parse(localStorage.getItem("user") || "null"),
  token: localStorage.getItem("token"),
  loading: false,

  /**
   * 开发登录。
   *
   * 原型阶段需按角色验证不同页面权限，故支持指定角色；
   * 不传角色时后端默认给 expert（可走通上传 → 出题 → 审核完整链路）。
   */
  login: async (role?: UserRole) => {
    set({ loading: true });
    try {
      const { data } = await api.post("/auth/dev-login", null, {
        params: role ? { role } : undefined,
      });
      localStorage.setItem("token", data.access_token);
      localStorage.setItem("user", JSON.stringify(data.user));
      set({ user: data.user, token: data.access_token, loading: false });
    } catch (err) {
      set({ loading: false });
      throw err;
    }
  },

  logout: () => {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    set({ user: null, token: null });
  },

  /**
   * 切换角色（原型阶段演示/评审用）。
   *
   * 重新走 dev-login 换取该角色的 token，使「菜单可见项 + 页面权限 +
   * 接口权限」三处同时按新角色生效，避免只改本地状态造成前后端不一致。
   */
  switchRole: async (role: UserRole) => {
    set({ loading: true });
    try {
      const { data } = await api.post("/auth/dev-login", null, { params: { role } });
      localStorage.setItem("token", data.access_token);
      localStorage.setItem("user", JSON.stringify(data.user));
      set({ user: data.user, token: data.access_token, loading: false });
    } catch (err) {
      set({ loading: false });
      throw err;
    }
  },
}));