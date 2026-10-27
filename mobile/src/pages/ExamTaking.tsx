/**
 * 移动端「在线答题」页 — 对齐评审稿 05 屏。
 *
 * 关键行为（都不是装饰，直接影响能不能考完）：
 * - 题目与时长来自 GET /exam-taking/{id}/start（真实试卷，随机顺序）；
 * - 倒计时以服务端 started_at + duration_minutes 计算，刷新页面不会「重置时间」；
 * - 每次作答即时暂存到后端（/answer），断网/刷新/换设备都不丢答案；
 * - 倒计时归零自动提交，无需人工干预；
 * - 答题卡可跳题，已答/未答/当前题一眼可辨。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Dialog, Toast } from "antd-mobile";

import {
  QUESTION_TYPE_LABEL,
  fetchStatus,
  hasToken,
  saveAnswer,
  startExam,
  submitExam,
  type ExamQuestion,
} from "../api/client";
import { fmtCountdown } from "../utils/format";

type AnswerMap = Record<string, string[]>;

export default function ExamTaking() {
  const { examId = "", answerId = "" } = useParams();
  const navigate = useNavigate();

  const [questions, setQuestions] = useState<ExamQuestion[]>([]);
  const [examName, setExamName] = useState("");
  const [durationMinutes, setDurationMinutes] = useState(0);
  const [startedAt, setStartedAt] = useState<string | null>(null);
  const [deadlineTs, setDeadlineTs] = useState<number | null>(null);
  const [remaining, setRemaining] = useState<number>(0);

  const [current, setCurrent] = useState(0);
  const [answers, setAnswers] = useState<AnswerMap>({});
  const [sheetOpen, setSheetOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  const submittedRef = useRef(false);
  const answersRef = useRef<AnswerMap>({});
  answersRef.current = answers;

  // ── 取卷（刷新后按 answerId 复用同一份答卷，不再重复开卷）──────────
  useEffect(() => {
    if (!hasToken()) {
      setError("未登录：请先在管理端登录后再答题。");
      setLoading(false);
      return;
    }
    let alive = true;
    (async () => {
      try {
        // 先看这份答卷是否已提交，避免刷新后又在「已提交」的卷子上答题
        const st = await fetchStatus(examId);
        if (st.status === "submitted" && st.answer_id) {
          navigate(`/m/exam/${examId}/result/${st.answer_id}`, { replace: true });
          return;
        }

        // 存在进行中的同一份答卷时直接复用：/start 会新建答卷，
        // 刷新页面后重复调用会开出第二份卷子（且不允许重考时直接报错）。
        if (st.status === "in_progress" && st.answer_id === answerId) {
          const restored = readSessionCache(answerId);
          if (restored) {
            if (!alive) return;
            setQuestions(restored.questions);
            setExamName(restored.exam_name);
            setDurationMinutes(restored.duration_minutes);
            setStartedAt(restored.started_at);
            setAnswers(restored.my_answers ?? {});
            const startMs = new Date(restored.started_at).getTime();
            setDeadlineTs(startMs + restored.duration_minutes * 60 * 1000);
            setLoading(false);
            return;
          }
          // 会话缓存丢失（换设备/清缓存）：回到确认页让考生重新开卷，避免开出空卷
          setError("答题会话已失效（本设备缓存被清理）。请返回考试确认页重新进入考试，已暂存的答案不会丢失。");
          setLoading(false);
          return;
        }

        const data = await startExam(examId);
        if (!alive) return;

        writeSessionCache(answerId, data);
        setQuestions(data.questions ?? []);
        setExamName(data.exam_name);
        setDurationMinutes(data.duration_minutes);
        setStartedAt(data.started_at);
        setAnswers(data.my_answers ?? {});
        const startMs = new Date(data.started_at).getTime();
        setDeadlineTs(startMs + data.duration_minutes * 60 * 1000);
      } catch (err) {
        if (alive) setError(readError(err));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [examId, answerId, navigate]);

  // ── 倒计时（按服务端开始时间推算，刷新不重置）────────────────────
  useEffect(() => {
    if (deadlineTs === null) return;
    const tick = () => {
      const left = Math.max(0, Math.round((deadlineTs - Date.now()) / 1000));
      setRemaining(left);
      if (left <= 0 && !submittedRef.current) {
        void doSubmit(true);
      }
    };
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deadlineTs]);

  const q = questions[current];
  const answeredCount = useMemo(
    () => questions.filter((item) => (answers[item.id] ?? []).length > 0).length,
    [questions, answers],
  );

  // ── 暂存作答 ─────────────────────────────────────────────────────
  const persist = useCallback(
    async (questionId: string, value: string[]) => {
      try {
        await saveAnswer(examId, answerId, questionId, value);
        setSavedAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
      } catch {
        Toast.show({ content: "答案暂存失败，网络恢复后会自动重试" });
      }
    },
    [examId, answerId],
  );

  const setAnswer = (questionId: string, value: string[]) => {
    setAnswers((prev) => ({ ...prev, [questionId]: value }));
    void persist(questionId, value);
  };

  const toggleOption = (option: string) => {
    if (!q) return;
    const letter = optionLetter(option);
    if (q.question_type === "multi_choice") {
      const cur = answers[q.id] ?? [];
      const next = cur.includes(letter) ? cur.filter((x) => x !== letter) : [...cur, letter];
      setAnswer(q.id, next);
    } else {
      setAnswer(q.id, [letter]);
    }
  };

  const setTrueFalse = (value: "正确" | "错误") => {
    if (!q) return;
    setAnswer(q.id, [value]);
  };

  // ── 交卷 ─────────────────────────────────────────────────────────
  async function doSubmit(auto = false) {
    if (submittedRef.current) return;
    submittedRef.current = true;
    setSubmitting(true);
    try {
      const payload = Object.entries(answersRef.current).map(([question_id, answer]) => ({
        question_id,
        answer,
      }));
      const res = await submitExam(examId, answerId, payload);
      Toast.show({ content: auto ? "时间到，已自动交卷" : "已提交答卷" });
      navigate(`/m/exam/${examId}/result/${res.answer_id ?? answerId}`, { replace: true });
    } catch (err) {
      submittedRef.current = false;
      Toast.show({ content: readError(err) });
    } finally {
      setSubmitting(false);
    }
  }

  const confirmSubmit = () => {
    const unanswered = questions.length - answeredCount;
    Dialog.confirm({
      content:
        unanswered > 0
          ? `还有 ${unanswered} 题未作答，未答题目将计 0 分。确认提交？`
          : "确认提交答卷？提交后不可修改。",
      confirmText: "提交",
      cancelText: "再检查一下",
      onConfirm: () => doSubmit(false),
    });
  };

  if (error) {
    return (
      <div className="m-page">
        <div className="m-head">
          <div className="m-title">在线考试</div>
        </div>
        <div className="m-body">
          <div className="m-card" style={{ borderColor: "#f6d4d7", background: "#fff8f8" }}>
            <div style={{ color: "var(--red)", fontWeight: 700, marginBottom: 8 }}>无法继续答题</div>
            <div style={{ fontSize: 13, lineHeight: 1.7, color: "var(--ink-2)" }}>{error}</div>
          </div>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="m-page">
        <div className="m-head">
          <div className="m-title">正在准备试卷…</div>
        </div>
        <div className="m-body">
          <div className="m-card m-empty">正在从题库抽题，请稍候…</div>
        </div>
      </div>
    );
  }

  if (!q) {
    return (
      <div className="m-page">
        <div className="m-head">
          <div className="m-title">{examName || "在线考试"}</div>
        </div>
        <div className="m-body">
          <div className="m-card m-empty">本场考试暂无题目，请联系管理员确认组卷结果。</div>
        </div>
      </div>
    );
  }

  const chosen = answers[q.id] ?? [];
  const urgent = remaining <= 300;

  return (
    <div className="m-page">
      <div className="m-head">
        <div className="m-title">{examName || "在线考试"}</div>
        <div className="m-sub">
          <span>共 {questions.length} 题 · 已答 {answeredCount} 题</span>
          <span>第 {current + 1} / {questions.length} 题</span>
        </div>
      </div>

      <div className="m-body">
        <div className={`m-timer${urgent ? " urgent" : ""}`}>
          <span>{urgent ? "即将结束" : "剩余时间"}</span>
          <b>{fmtCountdown(remaining)}</b>
        </div>

        <div className="m-card">
          <span className="m-tag">{QUESTION_TYPE_LABEL[q.question_type]}</span>
          <span className="m-tag gray" style={{ marginLeft: 8 }}>
            {q.score} 分
          </span>
          <div className="m-q">{q.content}</div>

          {q.question_type === "fill_blank" ? (
            <input
              value={chosen[0] ?? ""}
              onChange={(e) => setAnswer(q.id, [e.target.value])}
              placeholder="请填写答案"
              style={{
                width: "100%",
                padding: "12px 13px",
                fontSize: 14,
                borderRadius: 10,
                border: "1px solid var(--line)",
                outline: "none",
              }}
            />
          ) : q.question_type === "true_false" ? (
            (["正确", "错误"] as const).map((label) => (
              <div
                key={label}
                className={`m-option${chosen.includes(label) ? " active" : ""}`}
                onClick={() => setTrueFalse(label)}
              >
                <span className="m-radio" />
                <span>
                  {label === "正确" ? "✓ " : "✗ "}
                  {label}
                </span>
              </div>
            ))
          ) : (
            q.options.map((opt, i) => {
              const letter = optionLetter(opt);
              const active = chosen.includes(letter);
              return (
                <div
                  key={i}
                  className={`m-option${active ? " active" : ""}`}
                  onClick={() => toggleOption(opt)}
                >
                  <span className={`m-radio${q.question_type === "multi_choice" ? " square" : ""}`} />
                  <span>{opt}</span>
                </div>
              );
            })
          )}

          {q.question_type === "multi_choice" && (
            <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 6 }}>
              多选题：可选 2~3 项，选完请点「下一题」继续
            </div>
          )}

          <div className="m-source">
            来源：{q.source || "—"}
            {savedAt && (
              <span style={{ marginLeft: 8, color: "var(--cyan)" }}>答案已暂存 {savedAt}</span>
            )}
          </div>
        </div>
      </div>

      <div className="m-foot">
        <button className="m-btn" disabled={current === 0} onClick={() => setCurrent(current - 1)}>
          ‹ 上一题
        </button>
        <button className="m-btn" onClick={() => setSheetOpen(true)}>
          答题卡（{answeredCount}/{questions.length}）
        </button>
        {current === questions.length - 1 ? (
          <button className="m-btn primary" disabled={submitting} onClick={confirmSubmit}>
            {submitting ? "提交中…" : "提交答卷"}
          </button>
        ) : (
          <button className="m-btn primary" onClick={() => setCurrent(current + 1)}>
            下一题 ›
          </button>
        )}
      </div>

      {sheetOpen && (
        <>
          <div className="m-sheet-mask" onClick={() => setSheetOpen(false)} />
          <div className="m-sheet">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <b>答题卡</b>
              <span style={{ fontSize: 12, color: "var(--muted)" }}>
                已答 {answeredCount} / {questions.length} · 剩余 {fmtCountdown(remaining)}
              </span>
            </div>
            <div className="m-sheet-grid">
              {questions.map((item, i) => {
                const done = (answers[item.id] ?? []).length > 0;
                return (
                  <div
                    key={item.id}
                    className={`m-sheet-num${i === current ? " current" : ""}${done ? " done" : ""}`}
                    onClick={() => {
                      setCurrent(i);
                      setSheetOpen(false);
                    }}
                  >
                    {i + 1}
                  </div>
                );
              })}
            </div>
            <button
              className="m-btn primary"
              style={{ width: "100%", marginTop: 16 }}
              onClick={() => {
                setSheetOpen(false);
                confirmSubmit();
              }}
            >
              交卷
            </button>
          </div>
        </>
      )}
    </div>
  );
}

/** 从选项文本里取字母（"A. 内容" → "A"），与 Web 审核页保持同一判据。 */
function optionLetter(option: string): string {
  const m = option.match(/^\s*([A-Za-z])[\.、\)]\s*/);
  return m ? m[1].toUpperCase() : option.trim().slice(0, 1).toUpperCase();
}

/**
 * 试卷与倒计时起点的本地会话缓存。
 *
 * 存在的唯一理由：刷新页面时必须复用同一份答卷和同一个开始时间
 * （否则会重新开卷、并让倒计时重置）。答案本身由后端持久化，
 * 这里缓存的是「这份卷子长什么样」，刷新后不需要重新抽题。
 */
const SESSION_KEY = (answerId: string) => `exam-session:${answerId}`;

type SessionCache = {
  questions: ExamQuestion[];
  exam_name: string;
  duration_minutes: number;
  started_at: string;
  my_answers?: AnswerMap;
};

function readSessionCache(answerId: string): SessionCache | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY(answerId));
    return raw ? (JSON.parse(raw) as SessionCache) : null;
  } catch {
    return null;
  }
}

function writeSessionCache(answerId: string, data: SessionCache) {
  try {
    sessionStorage.setItem(SESSION_KEY(answerId), JSON.stringify(data));
  } catch {
    /* 隐私模式下不可用，仅影响刷新体验 */
  }
}

function readError(err: unknown): string {
  const e = err as { response?: { data?: { detail?: unknown } }; message?: string };
  const detail = e?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return e?.message || "请求失败，请稍后重试";
}
