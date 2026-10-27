import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, Button, Card, Select, Space, Typography, message } from "antd";
import { LoginOutlined } from "@ant-design/icons";

import { useAuthStore } from "../store/auth";
import type { UserRole } from "../api/types";

const { Title, Text } = Typography;

const ROLE_OPTIONS: { value: UserRole; label: string; desc: string }[] = [
  {
    value: "expert",
    label: "制度专家",
    desc: "上传制度 / 触发 AI 出题 / 审核题目 / 题库管理",
  },
  { value: "hr_admin", label: "HR 管理员", desc: "组卷 / 发布考试 / 查看统计" },
  { value: "employee", label: "员工", desc: "扫码答题 / 查看成绩" },
  { value: "dept_manager", label: "部门管理者", desc: "查看本部门统计" },
  { value: "compliance", label: "合规 / 高层", desc: "合规全景 / 导出报告" },
  { value: "it_admin", label: "IT 管理员", desc: "SSO / 组织架构 / 权限 / 审计" },
];

export default function LoginPage() {
  const { login, token, loading } = useAuthStore();
  const navigate = useNavigate();
  const [role, setRole] = useState<UserRole>("expert");

  useEffect(() => {
    if (token) navigate("/", { replace: true });
  }, [token, navigate]);

  const handleLogin = async () => {
    try {
      await login(role);
      message.success("登录成功");
    } catch (err) {
      message.error(`登录失败：${(err as Error).message}`);
    }
  };

  const current = ROLE_OPTIONS.find((r) => r.value === role);

  return (
    <div
      style={{
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        minHeight: "100vh",
        background: "#f0f2f5",
      }}
    >
      <Card style={{ width: 460 }}>
        <div style={{ textAlign: "center", marginBottom: 24 }}>
          <Title level={2} style={{ marginBottom: 4 }}>
            AI 试题生成系统
          </Title>
          <Text type="secondary">从制度文档自动生成试题，高效组织在线考试</Text>
        </div>

        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 20 }}
          message="原型阶段使用开发登录"
          description="正式环境将通过公司 SSO 统一认证自动登录。开发阶段可选择角色，用于验证各角色的页面权限。"
        />

        <div style={{ marginBottom: 8 }}>
          <Text strong>选择登录角色</Text>
        </div>
        <Select
          size="large"
          style={{ width: "100%", marginBottom: 8 }}
          value={role}
          onChange={(v) => setRole(v)}
          options={ROLE_OPTIONS.map((r) => ({ value: r.value, label: r.label }))}
        />
        {current && (
          <Text type="secondary" style={{ fontSize: 12, display: "block", marginBottom: 20 }}>
            该角色可访问：{current.desc}
          </Text>
        )}

        <Button
          type="primary"
          size="large"
          icon={<LoginOutlined />}
          onClick={handleLogin}
          loading={loading}
          block
        >
          登录
        </Button>

        <Space
          direction="vertical"
          size={4}
          style={{ width: "100%", marginTop: 20, textAlign: "center" }}
        >
          <Text type="secondary" style={{ fontSize: 12 }}>
            提示：要完整体验「上传制度 → AI 出题 → 审核」请使用「制度专家」角色
          </Text>
        </Space>
      </Card>
    </div>
  );
}