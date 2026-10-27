/**
 * 移动答题端 API 客户端。
 *
 * 与 Web 管理端共用同一后端（/api/v1）与同一 Token 载体（localStorage["token"]）。
 * 当前演示通过各端独立的开发登录获取 Token；两个端口的 localStorage 不共享。
 * SSO 回调尚未实现。
 */

import axios from "axios";

export const api = axios.create({
  baseURL: "/api/v1",
  timeout: 30000,
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

export function hasToken(): boolean {
  return Boolean(localStorage.getItem("token"));
}

export function setToken(token: string): void {
  localStorage.setItem("token", token);
}

/**
 * 开发/演示用登录（真实环境由 SSO 回调写入 Token，见 spec M7-1）。
 *
 * 为什么移动端必须自己有一条登录路径：
 * 移动端在 5174、管理端在 5173 —— 端口不同即**不同 Origin**，
 * localStorage **不共享**。因此「在管理端登录后直接打开移动端」是拿不到
 * 登录态的，考生会被挡在「未登录」提示上，无法答题。
 */
export async function devLogin(role = "employee"): Promise<void> {
  const r = await api.post<{ access_token: string }>("/auth/dev-login", null, {
    params: { role },
  });
  setToken(r.data.access_token);
}

/** Web 管理端地址（用于「去登录」跳转）。 */
export const WEB_BASE = `${window.location.protocol}//${window.location.hostname}:5173`;

// ── 类型（与 backend/app/schemas 对齐）────────────────────────────────

export type QuestionType = "single_choice" | "multi_choice" | "true_false" | "fill_blank";

export interface ExamQuestion {
  order: number;
  id: string;
  question_type: QuestionType;
  content: string;
  options: string[];
  score: number;
  source: string;
}

export interface ExamStartResponse {
  answer_id: string;
  exam_name: string;
  duration_minutes: number;
  question_count: number;
  questions: ExamQuestion[];
  started_at: string;
  /** 该答卷已暂存的作答（刷新后回显） */
  my_answers?: Record<string, string[]>;
}

export interface ExamDetail {
  id: string;
  name: string;
  status: string;
  duration_minutes: number;
  deadline: string | null;
  instructions: string | null;
  candidate_count: number;
  completed_count: number;
  allow_retake: boolean;
  max_retake_count: number;
  question_count: number;
}

export interface AnswerDetailItem {
  order: number;
  question_id: string;
  question_type: QuestionType | null;
  content: string;
  user_answer: string[];
  correct_answer: string[];
  is_correct: boolean;
  score: number;
  source: string;
}

export interface ExamResultData {
  answer_id: string;
  exam_id: string;
  exam_name: string;
  status: string;
  total_score: number;
  max_score?: number;
  score_percent?: number;
  correct_count: number;
  wrong_count: number;
  is_passed: boolean;
  duration_seconds: number;
  submitted_at: string | null;
  pass_score: number;
  details: AnswerDetailItem[];
}

export interface ExamStatus {
  status: "not_started" | "in_progress" | "submitted";
  answer_id?: string;
  total_score?: number | null;
  is_passed?: boolean | null;
  submitted_at?: string | null;
}

/** 我的考试（GET /exam-taking/my-exams） */
export interface MyExamItem {
  exam_id: string;
  name: string;
  status: string;
  duration_minutes: number;
  deadline: string | null;
  instructions: string | null;
  allow_retake: boolean;
  max_retake_count: number;
  my_status: "not_started" | "in_progress" | "submitted";
  my_answer_id: string | null;
  my_score: number | null;
  my_max_score?: number;
  my_score_percent?: number | null;
  my_is_passed: boolean | null;
  my_submitted_at: string | null;
}

export const QUESTION_TYPE_LABEL: Record<QuestionType, string> = {
  single_choice: "单选题",
  multi_choice: "多选题",
  true_false: "判断题",
  fill_blank: "填空题",
};

// ── 接口调用 ─────────────────────────────────────────────────────────

export async function fetchExam(examId: string): Promise<ExamDetail> {
  const { data } = await api.get<ExamDetail>(`/exams/${examId}`);
  return data;
}

export async function fetchStatus(examId: string): Promise<ExamStatus> {
  const { data } = await api.get<ExamStatus>(`/exam-taking/${examId}/status`);
  return data;
}

export async function startExam(examId: string): Promise<ExamStartResponse> {
  const { data } = await api.post<ExamStartResponse>(`/exam-taking/${examId}/start`);
  return data;
}

export async function saveAnswer(
  examId: string,
  answerId: string,
  questionId: string,
  answer: string[],
): Promise<void> {
  await api.post(`/exam-taking/${examId}/answer`, {
    answer_id: answerId,
    question_id: questionId,
    answer,
  });
}

export async function submitExam(
  examId: string,
  answerId: string,
  payload?: { question_id: string; answer: string[] }[],
): Promise<{ answer_id: string; total_score: number }> {
  // 交卷前把最终作答再刷一遍，保证服务端缓存与本地一致（提交接口按缓存判卷）
  if (payload?.length) {
    await Promise.all(
      payload.map((p) => saveAnswer(examId, answerId, p.question_id, p.answer)),
    );
  }
  const { data } = await api.post<{ answer_id: string; total_score: number }>(
    `/exam-taking/${examId}/submit`,
  );
  return data;
}

export async function fetchResult(examId: string, answerId: string): Promise<ExamResultData> {
  const { data } = await api.get<ExamResultData>(`/exam-taking/${examId}/result/${answerId}`);
  return data;
}
