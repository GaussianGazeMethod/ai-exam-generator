/**
 * 移动端入口页「我的考试」。
 *
 * 为什么需要这一页：
 * 移动端此前只有 `/m/exam/:examId` 系列路由，根路径 `/` **没有任何路由**，
 * 直接打开 http://localhost:5174 是纯白屏 —— 用户反馈「移动端口无法打开」。
 * 移动端本来是设计给「扫码/短信链接直达」的，但人总要有个能打开首页看
 * 「我要考什么」的地方，否则链接一丢就彻底进不去。
 *
 * 数据源：GET /exam-taking/my-exams（仅返回本人答卷状态，不泄露他人成绩）。
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Card, List, Tag } from "antd-mobile";

import { api, devLogin, hasToken, WEB_BASE, type MyExamItem } from "../api/client";
import { fmtDateTime } from "../utils/format";

export default function MobileHome() {
  const navigate = useNavigate();
  const [items, setItems] = useState<MyExamItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [needLogin, setNeedLogin] = useState(false);
  const [loggingIn, setLoggingIn] = useState(false);

  const load = () => {
    setLoading(true);
    setError(null);
    api
      .get<{ items: MyExamItem[] }>("/exam-taking/my-exams")
      .then((r) => setItems(r.data.items ?? []))
      .catch((err) => {
        const status = (err as { response?: { status?: number } })?.response?.status;
        const detail =
          (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
        // 401/403 说明登录态无效（两端不同 Origin，localStorage 不共享）
        if (status === 401 || status === 403) {
          setNeedLogin(true);
          setError("未登录或登录已过期");
        } else {
          setError(detail ?? "加载考试列表失败，请确认后端服务可用。");
        }
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (!hasToken()) {
      setLoading(false);
      setNeedLogin(true);
      setError("未登录");
      return;
    }
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleLogin = async () => {
    setLoggingIn(true);
    try {
      await devLogin("employee");
      setNeedLogin(false);
      load();
    } catch (err) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail ?? "登录失败，请确认后端服务可用。");
    } finally {
      setLoggingIn(false);
    }
  };

  const pending = items.filter((i) => i.status === "in_progress" && i.my_status !== "submitted");
  const done = items.filter((i) => i.my_status === "submitted");

  const enterExam = (item: MyExamItem) => {
    // 已提交 → 直接看成绩；未提交 → 进入确认页（可续答）
    if (item.my_status === "submitted" && item.my_answer_id) {
      navigate(`/m/exam/${item.exam_id}/result/${item.my_answer_id}`);
      return;
    }
    navigate(`/m/exam/${item.exam_id}`);
  };

  return (
    <div style={{ padding: 12, minHeight: "100vh", background: "#f5f6f8" }}>
      <div style={{ padding: "8px 4px 16px" }}>
        <div style={{ fontSize: 20, fontWeight: 700 }}>我的考试</div>
        <div style={{ fontSize: 12, color: "#888", marginTop: 4 }}>
          共 {items.length} 场 · 待完成 {pending.length} 场 · 已完成 {done.length} 场
        </div>
      </div>

      {error && (
        <Card style={{ marginBottom: 12 }}>
          <div style={{ color: "#d4380d", fontSize: 13 }}>
            {error}
            {needLogin && (
              <div style={{ color: "#888", fontSize: 12, marginTop: 6 }}>
                移动端（5174）与管理端（5173）属不同站点，登录态不互通，
                需要在本端单独登录一次。
              </div>
            )}
          </div>
          {needLogin && (
            <Button
              size="small"
              color="primary"
              style={{ marginTop: 10 }}
              loading={loggingIn}
              onClick={handleLogin}
            >
              以员工身份登录
            </Button>
          )}
          <Button
            size="small"
            fill="outline"
            style={{ marginTop: 10, marginLeft: 8 }}
            onClick={() => {
              window.open(WEB_BASE, "_blank");
            }}
          >
            去管理端
          </Button>
        </Card>
      )}

      {loading && (
        <Card>
          <div style={{ textAlign: "center", color: "#999", padding: 12 }}>加载中…</div>
        </Card>
      )}

      {!loading && !error && items.length === 0 && (
        <Card>
          <div style={{ textAlign: "center", color: "#999", padding: 12 }}>
            暂无考试。管理员发布考试后，这里会出现待考任务。
          </div>
        </Card>
      )}

      {pending.length > 0 && (
        <Card title="待完成" style={{ marginBottom: 12 }}>
          <List>
            {pending.map((item) => (
              <List.Item
                key={item.exam_id}
                description={
                  <span style={{ fontSize: 12, color: "#888" }}>
                    {item.duration_minutes} 分钟
                    {item.deadline ? ` · 截止 ${fmtDateTime(item.deadline)}` : ""}
                  </span>
                }
                extra={
                  <Button size="small" color="primary" onClick={() => enterExam(item)}>
                    {item.my_status === "in_progress" ? "继续答题" : "开始答题"}
                  </Button>
                }
              >
                {item.name}
              </List.Item>
            ))}
          </List>
        </Card>
      )}

      {done.length > 0 && (
        <Card title="已完成">
          <List>
            {done.map((item) => (
              <List.Item
                key={item.exam_id}
                description={
                  <span style={{ fontSize: 12, color: "#888" }}>
                    {item.my_score === null ? "—" : `${item.my_score} 分`}
                    {item.my_submitted_at ? ` · ${fmtDateTime(item.my_submitted_at)}` : ""}
                  </span>
                }
                extra={
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <Tag color={item.my_is_passed ? "success" : "danger"}>
                      {item.my_is_passed ? "通过" : "未通过"}
                    </Tag>
                    <Button size="small" fill="outline" onClick={() => enterExam(item)}>
                      看成绩
                    </Button>
                  </div>
                }
              >
                {item.name}
              </List.Item>
            ))}
          </List>
        </Card>
      )}
    </div>
  );
}
