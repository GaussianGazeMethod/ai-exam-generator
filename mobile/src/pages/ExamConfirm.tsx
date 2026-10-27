/**
 * 移动端「考试确认」页。
 *
 * 数据全部来自真实接口：GET /exams/{id}（考试信息）+ GET /exam-taking/{id}/status（本人状态）。
 * 之前这一页是写死的「数据安全管理规范 考试 / 考生：张三 / 10 题 / 30 分钟」。
 */

import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Button, Toast } from "antd-mobile";

import {
  devLogin,
  fetchExam,
  fetchStatus,
  hasToken,
  startExam,
  WEB_BASE,
  type ExamDetail,
  type ExamStatus,
} from "../api/client";
import { fmtDateTime } from "../utils/format";

export default function ExamConfirm() {
  const { examId = "" } = useParams();
  const navigate = useNavigate();

  const [exam, setExam] = useState<ExamDetail | null>(null);
  const [status, setStatus] = useState<ExamStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [loggingIn, setLoggingIn] = useState(false);

  useEffect(() => {
    if (!hasToken()) {
      setLoading(false);
      // 移动端(5174)与管理端(5173)是不同 Origin，登录态不共享，
      // 必须在本端登录一次（真实环境由 SSO 回调写入，见 spec M7-1）。
      setError("未登录：请在下方以员工身份登录后重试（移动端与管理端登录态不互通）。");
      return;
    }
    Promise.all([fetchExam(examId), fetchStatus(examId)])
      .then(([e, s]) => {
        setExam(e);
        setStatus(s);
      })
      .catch((err) => setError(readError(err)))
      .finally(() => setLoading(false));
  }, [examId]);

  const handleStart = async () => {
    setStarting(true);
    try {
      const data = await startExam(examId);
      // 用 replace 进入答题页，避免考生返回后重复开卷（服务端会再校验）
      navigate(`/m/exam/${examId}/taking/${data.answer_id}`, { replace: true });
    } catch (err) {
      Toast.show({ content: readError(err) });
    } finally {
      setStarting(false);
    }
  };

  const inProgress = status?.status === "in_progress" && status.answer_id;
  const submitted = status?.status === "submitted" && status.answer_id;

  return (
    <div className="m-page">
      <div className="m-head">
        <div className="m-title">{exam?.name ?? (loading ? "加载中…" : "考试")}</div>
        <div className="m-sub">
          <span>考试确认</span>
          <span>{exam?.deadline ? `截止 ${fmtDateTime(exam.deadline)}` : "无截止时间"}</span>
        </div>
      </div>

      <div className="m-body">
        {error && (
          <div className="m-card" style={{ borderColor: "#f6d4d7", background: "#fff8f8" }}>
            <div style={{ color: "var(--red)", fontWeight: 700, marginBottom: 8 }}>无法开始考试</div>
            <div style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.7 }}>{error}</div>
            {!hasToken() && (
              <Button
                color="primary"
                size="small"
                style={{ marginTop: 12 }}
                loading={loggingIn}
                onClick={async () => {
                  setLoggingIn(true);
                  try {
                    await devLogin("employee");
                    window.location.reload();
                  } catch {
                    setError("登录失败，请确认后端服务可用。");
                  } finally {
                    setLoggingIn(false);
                  }
                }}
              >
                以员工身份登录
              </Button>
            )}
            <a
              href={`${WEB_BASE}/login`}
              style={{ display: "inline-block", marginTop: 12, marginLeft: 12, color: "var(--blue)", fontWeight: 700 }}
            >
              去管理端 →
            </a>
          </div>
        )}

        {!error && loading && (
          <div className="m-card m-empty">正在加载考试信息…</div>
        )}

        {!error && !loading && exam && (
          <>
            <div className="m-card">
              <div className="m-row">
                <span className="m-label">题目数量</span>
                <span>{exam.question_count} 题</span>
              </div>
              <div className="m-row">
                <span className="m-label">考试时长</span>
                <span>{exam.duration_minutes} 分钟</span>
              </div>
              <div className="m-row">
                <span className="m-label">截止时间</span>
                <span>{exam.deadline ? fmtDateTime(exam.deadline) : "不限"}</span>
              </div>
              <div className="m-row">
                <span className="m-label">重考</span>
                <span>
                  {exam.allow_retake ? `允许（最多 ${exam.max_retake_count} 次）` : "不允许"}
                </span>
              </div>
              <div className="m-row">
                <span className="m-label">我的状态</span>
                <span>
                  {submitted ? (
                    <span className="m-correct">已提交（{status?.total_score ?? 0} 分）</span>
                  ) : inProgress ? (
                    <span style={{ color: "var(--amber-deep)" }}>有进行中的答卷</span>
                  ) : (
                    "未开始"
                  )}
                </span>
              </div>
            </div>

            <div className="m-card">
              <div style={{ fontWeight: 800, marginBottom: 8 }}>考试须知</div>
              <div style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.8 }}>
                {exam.instructions ||
                  "请在截止时间前独立完成，不得抄袭或替考；开始后计时不可暂停，倒计时结束将自动交卷。"}
              </div>
            </div>

            {exam.status !== "in_progress" && (
              <div className="m-card" style={{ background: "#fff7e7", borderColor: "#f2e0bd" }}>
                <div style={{ color: "var(--amber-deep)", fontWeight: 700 }}>
                  该考试当前状态为「
                  {exam.status === "finished"
                    ? "已截止"
                    : exam.status === "terminated"
                      ? "已终止"
                      : exam.status === "pending"
                        ? "待发布"
                        : "草稿"}
                  」，无法作答。
                </div>
              </div>
            )}
          </>
        )}
      </div>

      <div className="m-foot">
        <button
          className="m-btn ghost"
          onClick={() =>
            submitted && status?.answer_id
              ? navigate(`/m/exam/${examId}/result/${status.answer_id}`)
              : undefined
          }
          disabled={!submitted}
        >
          查看成绩
        </button>
        <button
          className="m-btn primary"
          disabled={starting || loading || Boolean(error) || exam?.status !== "in_progress"}
          onClick={handleStart}
        >
          {starting ? "正在开启…" : inProgress || submitted ? "继续 / 重考" : "开始考试"}
        </button>
      </div>
    </div>
  );
}

function readError(err: unknown): string {
  const e = err as { response?: { data?: { detail?: unknown } }; message?: string };
  const detail = e?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return e?.message || "请求失败，请稍后重试";
}
