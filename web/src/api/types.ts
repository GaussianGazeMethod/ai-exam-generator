/**
 * 后端 API 类型定义 — 与 backend/app/schemas 保持一致。
 *
 * 说明：spec 技术选型要求「TypeScript 前后端类型共享」，
 * 当前后端为 Python，故此处手工对齐 Pydantic schema，
 * 字段名与枚举值严格按后端定义书写。
 */

// ── 通用 ──

export interface MessageResponse {
  message: string;
  detail?: string | null;
}

export interface Paginated<T> {
  items: T[];
  total: number;
}

// ── 认证 / 用户 ──

export type UserRole =
  | "expert"
  | "hr_admin"
  | "employee"
  | "dept_manager"
  | "compliance"
  | "it_admin";

export interface User {
  id: string;
  name: string;
  email: string | null;
  role: UserRole;
  department_id: string | null;
  department_name: string | null;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: User;
}

// ── 制度文档（S1）──

export type DocStatus = "uploaded" | "parsing" | "parsed" | "generating" | "ready";

export interface Document {
  id: string;
  name: string;
  version: string;
  file_name: string;
  file_type: string;
  file_size: number;
  page_count: number;
  status: DocStatus;
  structured_content: string | null;
  remark: string | null;
  question_count: number;
  uploader_id: string | null;
  created_at: string;
  updated_at: string;
}

/** 解析后的结构化内容（structured_content 反序列化后）。 */
export interface ClauseItem {
  clause_no: string;
  chapter_no: string;
  section_no: string;
  text: string;
  paragraph_index?: number;
}

export interface SectionItem {
  section_no: string;
  title: string;
}

export interface ChapterItem {
  chapter_no: string;
  title: string;
  sections: SectionItem[];
  clauses: ClauseItem[];
}

export interface StructuredContent {
  document_name: string;
  file_type: string;
  page_count: number;
  parse_method: string;
  stats: {
    chapter_count: number;
    section_count: number;
    clause_count: number;
  };
  chapters: ChapterItem[];
  warnings: string[];
}

// ── 题目（S3）──

export type QuestionType =
  | "single_choice"
  | "multi_choice"
  | "true_false"
  | "fill_blank";

export type QuestionStatus = "pending" | "approved" | "rejected" | "flagged";

export interface Question {
  id: string;
  document_id: string | null;
  chapter_no: string | null;
  section_no: string | null;
  clause_no: string | null;
  question_type: QuestionType;
  status: QuestionStatus;
  content: string;
  options: string | null;
  answer: string;
  score: number;
  ai_raw_content: string | null;
  ai_raw_options: string | null;
  /**
   * M2-3 AI 质量自查结论（严格口径：以五个校验项为准）。
   * - true  ：五个校验项全部通过，且 AI 总评也通过
   * - false ：任一分项未通过，或分项与总评自相矛盾（需人工重点复核）
   * - null  ：未做校验，或校验未返回有效结构
   */
  ai_quality_pass: boolean | null;
  /** AI 质量自查说明（含未通过项与「分项与总评自相矛盾」提示） */
  ai_quality_note: string | null;
  usage_count: number;
  error_rate: number | null;
  is_active: boolean;
  reject_reason: string | null;
  created_at: string;
  updated_at: string;
}

/** 制度关联题目（GET /documents/{id}/questions 返回的是已解析结构）。 */
export interface DocumentQuestion {
  id: string;
  content: string;
  question_type: QuestionType;
  status: QuestionStatus;
  chapter_no: string | null;
  clause_no: string | null;
  options: string[];
  answer: string[];
}

// ── AI 出题任务（S2）──

export type TaskStatus = "pending" | "running" | "completed" | "failed";

export interface GenerationTask {
  task_id: string;
  document_id: string;
  document_name: string;
  status: TaskStatus;
  progress: number;
  message: string;
  generated_count: number;
  key_point_count: number;
  type_stats: Record<string, number>;
  errors: string[];
  created_at: string;
  finished_at: string | null;
}

export interface TriggerGenerationResponse {
  task_id: string;
  document_id: string;
  document_name: string;
  status: TaskStatus;
  message: string;
}

export interface AIStatus {
  llm_available: boolean;
  embedding_available: boolean;
  config: Record<string, string>;
  vector_store: {
    available: boolean;
    error: string | null;
    count: number;
    collection?: string;
    model?: string;
    persist_dir?: string;
  };
}

// ── 展示辅助 ──

export const QUESTION_TYPE_LABEL: Record<QuestionType, string> = {
  single_choice: "单选题",
  multi_choice: "多选题",
  true_false: "判断题",
  fill_blank: "填空题",
};

export const QUESTION_TYPE_COLOR: Record<QuestionType, string> = {
  single_choice: "blue",
  multi_choice: "purple",
  true_false: "cyan",
  fill_blank: "orange",
};

export const DOC_STATUS_LABEL: Record<DocStatus, string> = {
  uploaded: "已上传",
  parsing: "解析中",
  parsed: "已解析",
  generating: "生成中",
  ready: "就绪",
};

export const DOC_STATUS_COLOR: Record<DocStatus, string> = {
  uploaded: "default",
  parsing: "processing",
  parsed: "cyan",
  generating: "processing",
  ready: "green",
};

export const QUESTION_STATUS_LABEL: Record<QuestionStatus, string> = {
  pending: "待审核",
  approved: "已通过",
  rejected: "已弃用",
  flagged: "待复查",
};

export const QUESTION_STATUS_COLOR: Record<QuestionStatus, string> = {
  pending: "gold",
  approved: "green",
  rejected: "red",
  flagged: "orange",
};

/** 安全解析 structured_content 字符串。 */
export function parseStructuredContent(raw: string | null): StructuredContent | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as StructuredContent;
  } catch {
    return null;
  }
}

/** 安全解析 JSON 字符串数组。 */
export function parseJsonArray(raw: string | null): string[] {
  if (!raw) return [];
  try {
    const v = JSON.parse(raw);
    return Array.isArray(v) ? v.map(String) : [];
  } catch {
    return [];
  }
}
// ── 考试（S4）──

export type ExamStatus = "draft" | "pending" | "in_progress" | "finished" | "terminated";

export interface Exam {
  id: string;
  name: string;
  status: ExamStatus;
  document_ids: string;
  question_ids: string;
  duration_minutes: number;
  deadline: string | null;
  candidate_scope: string;
  allow_retake: boolean;
  max_retake_count: number;
  instructions: string | null;
  qr_code_url: string | null;
  exam_link: string | null;
  publisher_id: string | null;
  published_at: string | null;
  candidate_count: number;
  completed_count: number;
  created_at: string;
  updated_at: string;
}

export interface TypeRatio {
  single_choice: number;
  multi_choice: number;
  true_false: number;
  fill_blank: number;
}

export interface ComposePreviewQuestion {
  id: string;
  question_type: QuestionType;
  content: string;
  options: string[];
  answer: string[];
  chapter_no: string | null;
  clause_no: string | null;
  source: string;
}

export interface ComposePreview {
  questions: ComposePreviewQuestion[];
  total: number;
  expected_total: number;
  type_stats: Record<string, number>;
  shortages: Record<string, { wanted: number; available: number }>;
  warnings: string[];
  is_complete: boolean;
  ratio_used: TypeRatio;
}

export interface CandidateScope {
  type: "all" | "departments";
  dept_ids?: string[];
}

export interface ExamProgress {
  candidate_count: number;
  completed_count: number;
  not_started_users: {
    id: string;
    name: string;
    department_id: string | null;
    email: string | null;
  }[];
}

export interface ExamNotification {
  id: string;
  template_type: string;
  channel: string;
  title: string;
  content: string;
  recipient_count: number;
  is_sent: boolean;
  sent_at: string | null;
}

export const EXAM_STATUS_LABEL: Record<ExamStatus, string> = {
  draft: "草稿",
  pending: "待发布",
  in_progress: "进行中",
  finished: "已截止",
  terminated: "已终止",
};

export const EXAM_STATUS_COLOR: Record<ExamStatus, string> = {
  draft: "default",
  pending: "processing",
  in_progress: "blue",
  finished: "green",
  terminated: "red",
};

/** 安全解析 ID 数组字符串。 */
export function parseIdArray(raw: string | null): string[] {
  return parseJsonArray(raw);
}

/** 安全解析考生范围。 */
export function parseCandidateScope(raw: string | null): CandidateScope {
  if (!raw) return { type: "all" };
  try {
    const v = JSON.parse(raw);
    return typeof v === "object" && v ? (v as CandidateScope) : { type: "all" };
  } catch {
    return { type: "all" };
  }
}

// ── 统计看板（S6）────────────────────────────────────────────────────
//
// 后端 schemas/statistics.py 中的列表项为 list[dict]（未建子模型），
// 故此处按 api/v1/statistics.py 的实际写入字段手工对齐。

/** GET /statistics/exams/{id}/overview（含 dashboard 聚合返回的 overview 字段） */
export interface ExamOverview {
  exam_id: string;
  exam_name: string;
  exam_status?: ExamStatus;
  candidate_count: number;
  completed_count: number;
  not_started_count: number;
  average_score: number;
  pass_rate: number;
  highest_score: number;
  lowest_score: number;
  /** 及格线（默认 60，由后端 PASS_SCORE 决定；通过判定按得分率比较） */
  pass_score?: number;
  /** 试卷满分（各题分值之和） */
  max_score?: number;
  /** 平均得分率（0-100），与通过率同口径 */
  average_score_percent?: number;
  passed_count?: number;
  failed_count?: number;
}

export interface ChapterStatRow {
  chapter_no: string | null;
  section_no?: string | null;
  total_answers: number;
  correct_answers: number;
  correct_rate: number;
}

export interface DepartmentStatRow {
  dept_id: string | null;
  dept_name: string;
  candidate_count: number;
  completed_count: number;
  not_started_count: number;
  average_score: number;
  pass_rate: number;
  /** 按平均分降序的名次 */
  rank?: number;
}

export interface PersonStatRow {
  user_id: string;
  name: string;
  dept_id: string | null;
  dept_name: string;
  score: number;
  /** 得分率（0-100），通过判定口径 */
  score_percent?: number;
  is_passed: boolean;
  correct_count: number;
  wrong_count: number;
  duration_seconds: number;
  submitted_at: string;
  submit_type?: string | null;
  ip_address?: string | null;
  /** 按分数降序的名次（1 起） */
  rank?: number;
}

export interface QuestionStatRow {
  /** 试卷内题号（1 起） */
  order: number;
  question_id: string;
  question_type: QuestionType;
  content: string;
  chapter_no: string | null;
  clause_no: string | null;
  total_answers: number;
  correct_answers: number;
  correct_rate: number;
  /** 错误率 = 100 − 正确率 */
  error_rate: number;
  usage_count: number;
  /** 是否达到错误率预警阈值（后端 error_threshold） */
  needs_review: boolean;
}

export interface ComplianceExamRef {
  exam_id: string;
  name: string;
}

export interface ComplianceRow {
  document_id: string | null;
  document_name: string;
  exam_count: number;
  /** 该制度下的考试（带 ID，支持下钻到考试统计） */
  exams?: ComplianceExamRef[];
  exam_names: string[];
  latest_status: string;
  coverage_rate: number;
  pass_rate: number;
  /** complete / partial / pending */
  status: "complete" | "partial" | "pending" | string;
  status_label: string;
}

/** 单份答卷逐题明细（下钻） */
export interface AnswerDetailItem {
  order: number;
  question_id: string;
  question_type: QuestionType;
  content: string;
  options: string[];
  correct_answer: string[];
  user_answer: string[];
  is_correct: boolean;
  score: number;
  chapter_no: string | null;
  clause_no: string | null;
}

export interface AnswerDetail {
  answer_id: string;
  exam_id: string;
  user_id: string;
  user_name: string;
  dept_name: string;
  total_score: number;
  is_passed: boolean;
  correct_count: number;
  wrong_count: number;
  duration_seconds: number;
  submitted_at: string | null;
  submit_type?: string | null;
  ip_address?: string | null;
  details: AnswerDetailItem[];
}

/** GET /statistics/exams/{id}/dashboard（HR 全景看板聚合） */
export interface ExamDashboard {
  overview: ExamOverview;
  chapter_stats: ChapterStatRow[];
  dept_stats: DepartmentStatRow[];
  person_stats: PersonStatRow[];
  question_stats: QuestionStatRow[];
  /** company = 全公司；department = 已按部门管理者数据范围收口 */
  scope: "company" | "department";
  generated_at: string;
}

/** GET /statistics/compliance */
export interface ComplianceOverviewResponse {
  items: ComplianceRow[];
  period: string | null;
  total: number;
  fully_covered: number;
  pending: number;
  generated_at: string;
}

export const COMPLIANCE_STATUS_COLOR: Record<string, string> = {
  complete: "green",
  partial: "gold",
  pending: "default",
};

// ── 系统管理（S8）────────────────────────────────────────────────────

/** GET /system/departments（对齐 schemas/user.py DepartmentResponse） */
export interface Department {
  id: string;
  name: string;
  parent_id: string | null;
  external_id: string | null;
}

/** GET /system/users（对齐 schemas/user.py UserResponse） */
export interface SystemUser {
  id: string;
  name: string;
  email: string | null;
  role: UserRole;
  department_id: string | null;
  department_name: string | null;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface RoleConfig {
  role: UserRole;
  name: string;
  permissions: string[];
  data_scope: string;
}

export interface AuditLogItem {
  id: string;
  user_id: string | null;
  user_name: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  detail: string | null;
  ip_address: string | null;
  created_at: string;
}

/** 审计日志操作类型（与 models/audit_log.py 注释一致）。 */
export const AUDIT_ACTION_LABEL: Record<string, string> = {
  login: "登录",
  upload: "上传",
  parse: "解析",
  generate: "出题",
  review: "审核",
  compose: "组卷",
  publish: "发布",
  exam_take: "答题",
  export: "导出",
  config: "配置",
  notify: "通知",
};

export const AUDIT_ACTION_COLOR: Record<string, string> = {
  login: "default",
  upload: "blue",
  parse: "cyan",
  generate: "geekblue",
  review: "green",
  compose: "purple",
  publish: "green",
  exam_take: "blue",
  export: "orange",
  config: "magenta",
  notify: "gold",
};

/** 角色中文名（全站唯一来源）。 */
export const ROLE_LABEL: Record<UserRole, string> = {
  expert: "制度专家",
  hr_admin: "HR 管理员",
  employee: "员工",
  dept_manager: "部门管理者",
  compliance: "合规 / 高层",
  it_admin: "IT 管理员",
};

// ── 我的考试 / 个人中心（S5）──────────────────────────────────────────

export type MyExamStatus = "not_started" | "in_progress" | "submitted";

export interface MyExamItem {
  exam_id: string;
  name: string;
  status: ExamStatus;
  duration_minutes: number;
  deadline: string | null;
  instructions: string | null;
  allow_retake: boolean;
  max_retake_count: number;
  my_status: MyExamStatus;
  my_answer_id: string | null;
  my_score: number | null;
  /** 本人这场考试的卷面满分 */
  my_max_score?: number;
  /** 本人得分率（0-100） */
  my_score_percent?: number | null;
  my_is_passed: boolean | null;
  my_submitted_at: string | null;
}

export interface ExamRecord {
  answer_id: string;
  exam_id: string;
  exam_name: string;
  exam_status: ExamStatus | null;
  status: string;
  total_score: number | null;
  max_score?: number;
  score_percent?: number;
  correct_count: number | null;
  wrong_count: number | null;
  is_passed: boolean | null;
  duration_seconds: number | null;
  started_at: string | null;
  submitted_at: string | null;
  deadline: string | null;
}

export const MY_EXAM_STATUS_LABEL: Record<MyExamStatus, string> = {
  not_started: "未作答",
  in_progress: "答题中",
  submitted: "已提交",
};

export const MY_EXAM_STATUS_COLOR: Record<MyExamStatus, string> = {
  not_started: "gold",
  in_progress: "blue",
  submitted: "green",
};
