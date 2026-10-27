/**
 * 移动端「考试结果」页 — 对齐评审稿 06 屏。
 *
 * 数据来自 GET /exam-taking/{examId}/result/{answerId}（真实判卷结果 + 逐题明细）。
 * 之前这一页是写死的 85 分 / 8 对 2 错 / 用时 18 分 32 秒。
 */

import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import {
  QUESTION_TYPE_LABEL,
  fetchResult,
  hasToken,
  type AnswerDetailItem,
  type ExamResultData,
} from "../api/client";
import { fmtDateTime, fmtDuration } from "../utils/format";

export default function ExamResult() {
  const { examId = "", answerId = "" } = useParams();
  const navigate = useNavigate();

  const [result, setResult] = useState<ExamResultData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openWrong, setOpenWrong] = useState<string | null>(null);

  useEffect(() => {
    if (!hasToken()) {
      setError("未登录：请先在管理端登录后再查看成绩。");
      setLoading(false);
      return;
    }
    fetchResult(examId, answerId)
      .then(setResult)
      .catch((err) => setError(readError(err)))
      .finally(() => setLoading(false));
  }, [examId, answerId]);

  if (loading) {
    return (
      <div className="m-page">
        <div className="m-head">
          <div className="m-title">正在加载成绩…</div>
        </div>
        <div className="m-body">
          <div className="m-card m-empty">请稍候</div>
        </div>
      </div>
    );
  }

  if (error || !result) {
    return (
      <div className="m-page">
        <div className="m-head">
          <div className="m-title">考试结果</div>
        </div>
        <div className="m-body">
          <div className="m-card" style={{ borderColor: "#f6d4d7", background: "#fff8f8" }}>
            <div style={{ color: "var(--red)", fontWeight: 700, marginBottom: 8 }}>无法加载成绩</div>
            <div style={{ fontSize: 13, lineHeight: 1.7, color: "var(--ink-2)" }}>
              {error ?? "答卷不存在"}
            </div>
          </div>
        </div>
      </div>
    );
  }

  const wrongs = result.details.filter((d) => !d.is_correct);
  const passed = result.is_passed;
  const ringColor = passed ? "var(--green)" : "var(--red)";

  return (
    <div className="m-page">
      <div className="m-head">
        <div className="m-title">{result.exam_name || "考试结果"}</div>
        <div className="m-sub">
          <span>{result.status === "submitted" ? "已完成" : result.status}</span>
          <span>{result.submitted_at ? fmtDateTime(result.submitted_at) : ""}</span>
        </div>
      </div>

      <div className="m-result-hero">
        <div className="m-kicker">
          本次考试得分
          {result.max_score ? `（满分 ${Math.round(result.max_score)} 分）` : ""}
        </div>
        <div
          className="m-score-ring"
          style={{
            border: `7px solid ${ringColor}`,
            color: ringColor,
            background: passed ? "#f2fbf7" : "#fff5f6",
          }}
        >
          <span>{Math.round(result.total_score)}</span>
        </div>
        <span className={`m-pass ${passed ? "ok" : "no"}`}>
          {passed ? "✓ 已通过" : `✗ 未通过（及格线 ${result.pass_score}%）`}
        </span>
        {result.score_percent !== undefined && (
          <div className="m-kicker" style={{ marginTop: 8 }}>
            得分率 {result.score_percent}%（通过判定按得分率计算）
          </div>
        )}

        <div className="m-grid3">
          <div className="m-cell">
            <b className="m-correct">{result.correct_count}</b>
            <small>答对题目</small>
          </div>
          <div className="m-cell">
            <b className="m-wrong">{result.wrong_count}</b>
            <small>错题</small>
          </div>
          <div className="m-cell">
            <b>{fmtDuration(result.duration_seconds)}</b>
            <small>用时</small>
          </div>
        </div>
      </div>

      <div className="m-body" style={{ paddingTop: 0 }}>
        <div className="m-card">
          <div style={{ fontWeight: 800, marginBottom: 6 }}>
            错题回顾（{wrongs.length} 题）
          </div>
          {wrongs.length === 0 ? (
            <div className="m-empty">全部答对，没有错题</div>
          ) : (
            wrongs.map((d) => (
              <WrongRow
                key={d.question_id}
                item={d}
                open={openWrong === d.question_id}
                onToggle={() =>
                  setOpenWrong(openWrong === d.question_id ? null : d.question_id)
                }
              />
            ))
          )}
        </div>

        <div className="m-card">
          <div style={{ fontWeight: 800, marginBottom: 8 }}>全部题目对照</div>
          {result.details.map((d) => (
            <div key={d.question_id} className="m-row">
              <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {d.order}. {d.content}
              </span>
              <span className={d.is_correct ? "m-correct" : "m-wrong"}>
                {d.is_correct ? "正确" : "错误"}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="m-foot" style={{ justifyContent: "center" }}>
        <button className="m-btn primary" style={{ width: "100%" }} onClick={() => navigate(`/m/exam/${examId}`)}>
          完成
        </button>
      </div>
    </div>
  );
}

function WrongRow({
  item,
  open,
  onToggle,
}: {
  item: AnswerDetailItem;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <div style={{ borderTop: "1px solid #f0f2f6", padding: "12px 0" }}>
      <div
        style={{ display: "flex", gap: 8, alignItems: "flex-start", cursor: "pointer" }}
        onClick={onToggle}
      >
        <span className="m-tag gray" style={{ flex: "none" }}>
          {item.order}
        </span>
        <span style={{ flex: 1, fontSize: 13, lineHeight: 1.6 }}>{item.content}</span>
        <span style={{ color: "var(--muted)", flex: "none" }}>{open ? "⌃" : "›"}</span>
      </div>
      {open && (
        <div style={{ marginTop: 10, fontSize: 13, lineHeight: 1.9, color: "var(--ink-2)" }}>
          {item.question_type && (
            <div className="m-tag" style={{ marginBottom: 8 }}>
              {QUESTION_TYPE_LABEL[item.question_type] ?? item.question_type}
            </div>
          )}
          <div>
            我的答案：<span className="m-wrong">{item.user_answer.join("、") || "未作答"}</span>
          </div>
          <div>
            正确答案：<span className="m-correct">{item.correct_answer.join("、")}</span>
          </div>
          {item.source && <div className="m-source">来源：{item.source}</div>}
        </div>
      )}
    </div>
  );
}

function readError(err: unknown): string {
  const e = err as { response?: { data?: { detail?: unknown } }; message?: string };
  const detail = e?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return e?.message || "请求失败，请稍后重试";
}
