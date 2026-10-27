/**
 * TanStack Query hooks — 前端数据层唯一入口。
 *
 * spec 技术选型要求：「Axios + React Query（TanStack Query）——
 * React Query 处理缓存/重试/分页极其方便，特别适合考试列表、统计看板等数据密集页面」
 *
 * 约定：
 * - 所有服务端状态都经由此处的 hook 获取，页面组件不直接调用 axios
 * - mutation 成功后按需 invalidate 相关 query key
 * - 出题任务是异步的，用 useGenerationTask 做进度轮询，终态自动停止
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from "@tanstack/react-query";

import api from "./client";
import type {
  AIStatus,
  AuditLogItem,
  CandidateScope,
  AnswerDetail,
  ChapterStatRow,
  ComposePreview,
  ComposePreviewQuestion,
  ComplianceOverviewResponse,
  Department,
  DepartmentStatRow,
  Exam,
  ExamNotification,
  ExamDashboard,
  ExamOverview,
  ExamProgress,
  ExamStatus,
  TypeRatio,
  Document,
  DocumentQuestion,
  GenerationTask,
  ExamRecord,
  LoginResponse,
  MyExamItem,
  Paginated,
  PersonStatRow,
  Question,
  QuestionStatRow,
  RoleConfig,
  StructuredContent,
  SystemUser,
  TriggerGenerationResponse,
  User,
  UserRole,
} from "./types";

// ──────────────────────────────────────────────────────────
// Query Keys
// ──────────────────────────────────────────────────────────

export const qk = {
  me: ["auth", "me"] as const,
  documents: (params?: object) => ["documents", params ?? {}] as const,
  document: (id: string) => ["documents", id] as const,
  documentQuestions: (id: string) => ["documents", id, "questions"] as const,
  pendingQuestions: (params?: object) => ["questions", "pending", params ?? {}] as const,
  questions: (params?: object) => ["questions", params ?? {}] as const,
  question: (id: string) => ["questions", id] as const,
  generationTask: (taskId: string) => ["generate", "tasks", taskId] as const,
  generationTasks: () => ["generate", "tasks"] as const,
  aiStatus: ["generate", "status"] as const,
};

// ──────────────────────────────────────────────────────────
// 认证
// ──────────────────────────────────────────────────────────

export function useDevLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (role?: string) => {
      const { data } = await api.post<LoginResponse>("/auth/dev-login", null, {
        params: role ? { role } : undefined,
      });
      return data;
    },
    onSuccess: (data) => {
      localStorage.setItem("token", data.access_token);
      localStorage.setItem("user", JSON.stringify(data.user));
      qc.invalidateQueries();
    },
  });
}

/** 开发登录可选角色（登录页的选择器由后端提供，避免前后端角色清单漂移）。 */
export function useDevRoles() {
  return useQuery<{ roles: { value: UserRole; label: string }[] }>({
    queryKey: ["auth", "dev-roles"],
    queryFn: async () =>
      (await api.get<{ roles: { value: UserRole; label: string }[] }>("/auth/dev-roles")).data,
    staleTime: Infinity,
  });
}

export function useMe(options?: Partial<UseQueryOptions<User>>) {
  return useQuery<User>({
    queryKey: qk.me,
    queryFn: async () => (await api.get<User>("/auth/me")).data,
    ...options,
  });
}

// ──────────────────────────────────────────────────────────
// S1 制度文档
// ──────────────────────────────────────────────────────────

export interface DocumentListParams {
  page?: number;
  page_size?: number;
  status?: string;
  keyword?: string;
}

export function useDocuments(params: DocumentListParams = {}) {
  return useQuery<Paginated<Document>>({
    queryKey: qk.documents(params),
    queryFn: async () => (await api.get<Paginated<Document>>("/documents", { params })).data,
  });
}

export function useDocument(id: string | undefined) {
  return useQuery<Document>({
    queryKey: qk.document(id ?? ""),
    queryFn: async () => (await api.get<Document>(`/documents/${id}`)).data,
    enabled: Boolean(id),
  });
}

/** 制度详情页的章节树（从 structured_content 解析）。 */
export function useDocumentStructure(id: string | undefined) {
  const docs = useDocument(id);
  const structured: StructuredContent | null = (() => {
    const raw = docs.data?.structured_content;
    if (!raw) return null;
    try {
      return JSON.parse(raw) as StructuredContent;
    } catch {
      return null;
    }
  })();
  return { ...docs, structured };
}

export function useDocumentQuestions(id: string | undefined, page = 1, pageSize = 100) {
  return useQuery<Paginated<DocumentQuestion>>({
    queryKey: qk.documentQuestions(id ?? ""),
    queryFn: async () =>
      (
        await api.get<Paginated<DocumentQuestion>>(`/documents/${id}/questions`, {
          params: { page, page_size: pageSize },
        })
      ).data,
    enabled: Boolean(id),
  });
}

export interface UploadDocumentInput {
  file: File;
  name: string;
  version?: string;
  remark?: string;
}

export function useUploadDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: UploadDocumentInput) => {
      const form = new FormData();
      form.append("file", input.file);
      const { data } = await api.post<Document>("/documents", form, {
        params: {
          name: input.name,
          version: input.version ?? "1.0",
          remark: input.remark ?? "",
        },
        headers: { "Content-Type": "multipart/form-data" },
        // 上传 + 解析 + 建索引可能耗时较久
        timeout: 300000,
      });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });
}

export function useReparseDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (docId: string) =>
      (await api.post<{ message: string }>(`/documents/${docId}/parse`, null, { timeout: 300000 }))
        .data,
    onSuccess: (_d, docId) => {
      qc.invalidateQueries({ queryKey: qk.document(docId) });
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

export function useDeleteDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (docId: string) =>
      (await api.delete<{ message: string }>(`/documents/${docId}`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });
}

// ──────────────────────────────────────────────────────────
// S2 AI 出题
// ──────────────────────────────────────────────────────────

export interface TriggerGenerationInput {
  document_id: string;
  max_points?: number;
  quality_check?: boolean;
}

export function useTriggerGeneration() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: TriggerGenerationInput) =>
      (
        await api.post<TriggerGenerationResponse>("/generate/questions", null, {
          params: {
            document_id: input.document_id,
            max_points: input.max_points ?? 40,
            quality_check: input.quality_check ?? true,
          },
        })
      ).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.generationTasks() });
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

/**
 * 轮询出题任务进度。
 *
 * 任务到达 completed / failed 后停止轮询（refetchInterval 返回 false）。
 * 失败时也停止，避免无意义的重试压力。
 */
export function useGenerationTask(taskId: string | undefined) {
  return useQuery<GenerationTask>({
    queryKey: qk.generationTask(taskId ?? ""),
    queryFn: async () => (await api.get<GenerationTask>(`/generate/tasks/${taskId}`)).data,
    enabled: Boolean(taskId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "completed" || status === "failed" ? false : 1200;
    },
  });
}

export function useAIStatus() {
  return useQuery<AIStatus>({
    queryKey: qk.aiStatus,
    queryFn: async () => (await api.get<AIStatus>("/generate/status")).data,
  });
}

// ──────────────────────────────────────────────────────────
// S3 题库 / 审核
// ──────────────────────────────────────────────────────────

export interface PendingParams {
  page?: number;
  page_size?: number;
  document_id?: string;
}

export function usePendingQuestions(params: PendingParams = {}, enabled = true) {
  return useQuery<Paginated<Question>>({
    queryKey: qk.pendingQuestions(params),
    queryFn: async () =>
      (await api.get<Paginated<Question>>("/questions/pending", { params })).data,
    enabled,
  });
}

export interface QuestionListParams extends PendingParams {
  question_type?: string;
  status?: string;
  keyword?: string;
}

export function useQuestions(params: QuestionListParams = {}, enabled = true) {
  return useQuery<Paginated<Question>>({
    queryKey: qk.questions(params),
    queryFn: async () => (await api.get<Paginated<Question>>("/questions", { params })).data,
    enabled,
  });
}

export function useQuestion(id: string | undefined) {
  return useQuery<Question>({
    queryKey: qk.question(id ?? ""),
    queryFn: async () => (await api.get<Question>(`/questions/${id}`)).data,
    enabled: Boolean(id),
  });
}

export interface ReviewInput {
  questionId: string;
  action: "approve" | "edit_approve" | "reject";
  content?: string;
  options?: string;
  answer?: string;
  reject_reason?: string;
}

export function useReviewQuestion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: ReviewInput) => {
      const { questionId, action, ...rest } = input;
      if (action === "reject") {
        const { data } = await api.post<Question>(`/questions/${questionId}/reject`, null, {
          params: { reason: rest.reject_reason ?? "" },
        });
        return data;
      }
      const { data } = await api.post<Question>(`/questions/${questionId}/approve`, {
        action,
        content: rest.content ?? null,
        options: rest.options ?? null,
        answer: rest.answer ?? null,
      });
      return data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["questions"] });
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

export function useBatchReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: { question_ids: string[]; action: "approve" | "reject" }) =>
      (await api.post<{ message: string }>("/questions/batch-review", input)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["questions"] }),
  });
}

export function useToggleQuestionActive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: { questionId: string; is_active: boolean }) =>
      (
        await api.put<Question>(`/questions/${input.questionId}/status`, null, {
          params: { is_active: input.is_active },
        })
      ).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["questions"] }),
  });
}

export function useUpdateQuestion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      questionId: string;
      content?: string;
      options?: string;
      answer?: string;
      score?: number;
    }) => {
      const { questionId, ...body } = input;
      return (await api.put<Question>(`/questions/${questionId}`, body)).data;
    },
    onSuccess: (_d, v) => {
      qc.invalidateQueries({ queryKey: qk.question(v.questionId) });
      qc.invalidateQueries({ queryKey: ["questions"] });
    },
  });
}
// ──────────────────────────────────────────────────────────
// S4 考试管理
// ──────────────────────────────────────────────────────────

export interface ExamListParams {
  page?: number;
  page_size?: number;
  status?: string;
  keyword?: string;
}

export function useExams(params: ExamListParams = {}, enabled = true) {
  return useQuery<Paginated<Exam>>({
    queryKey: ["exams", params],
    queryFn: async () => (await api.get<Paginated<Exam>>("/exams", { params })).data,
    enabled,
  });
}

export function useExam(id: string | undefined) {
  return useQuery<Exam>({
    queryKey: ["exams", id ?? ""],
    queryFn: async () => (await api.get<Exam>(`/exams/${id}`)).data,
    enabled: Boolean(id),
  });
}

export function useExamQuestions(id: string | undefined) {
  return useQuery<Paginated<ComposePreviewQuestion>>({
    queryKey: ["exams", id ?? "", "questions"],
    queryFn: async () =>
      (await api.get<Paginated<ComposePreviewQuestion>>(`/exams/${id}/questions`)).data,
    enabled: Boolean(id),
  });
}

export function useExamProgress(id: string | undefined) {
  return useQuery<ExamProgress>({
    queryKey: ["exams", id ?? "", "progress"],
    queryFn: async () => (await api.get<ExamProgress>(`/exams/${id}/progress`)).data,
    enabled: Boolean(id),
  });
}

export function useExamNotifications(id: string | undefined) {
  return useQuery<Paginated<ExamNotification>>({
    queryKey: ["exams", id ?? "", "notifications"],
    queryFn: async () =>
      (await api.get<Paginated<ExamNotification>>(`/exams/${id}/notifications`)).data,
    enabled: Boolean(id),
  });
}

export function useDefaultRatio() {
  return useQuery<{ ratio: TypeRatio; total: number }>({
    queryKey: ["exams", "default-ratio"],
    queryFn: async () => (await api.get<{ ratio: TypeRatio; total: number }>("/exams/default-ratio")).data,
    staleTime: Infinity,
  });
}

export function useComposePreview() {
  return useMutation({
    mutationFn: async (input: {
      document_ids: string[];
      chapter_nos?: string[];
      ratio?: TypeRatio | null;
    }) => (await api.post<ComposePreview>("/exams/compose-preview", input)).data,
  });
}

export interface CreateExamInput {
  name: string;
  document_ids: string[];
  duration_minutes: number;
  deadline?: string | null;
  candidate_scope?: CandidateScope;
  allow_retake: boolean;
  max_retake_count: number;
  instructions?: string | null;
  type_ratio?: TypeRatio | null;
}

export function useCreateExam() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: CreateExamInput) =>
      (await api.post<Exam>("/exams", input)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["exams"] }),
  });
}

export function useSwapExamQuestion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: { examId: string; old_question_id: string }) =>
      (
        await api.post<Exam>(`/exams/${input.examId}/swap-question`, {
          old_question_id: input.old_question_id,
        })
      ).data,
    onSuccess: (_d, v) => {
      qc.invalidateQueries({ queryKey: ["exams", v.examId, "questions"] });
      qc.invalidateQueries({ queryKey: ["exams", v.examId] });
    },
  });
}

export function useConfigureExam() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      examId: string;
      name: string;
      duration_minutes: number;
      deadline?: string | null;
      candidate_scope?: CandidateScope;
      allow_retake: boolean;
      max_retake_count: number;
      instructions?: string | null;
    }) => {
      const { examId, ...body } = input;
      return (await api.post<Exam>(`/exams/${examId}/configure`, body)).data;
    },
    onSuccess: (_d, v) => qc.invalidateQueries({ queryKey: ["exams", v.examId] }),
  });
}

export function usePublishExam() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: { examId: string; notify?: boolean; channel?: string }) =>
      (
        await api.post<Exam>(`/exams/${input.examId}/publish`, null, {
          params: { notify: input.notify ?? true, channel: input.channel ?? "dingtalk" },
        })
      ).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["exams"] }),
  });
}

export function useRemindExam() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: { examId: string; channel?: string }) =>
      (
        await api.post<{ message: string; reminded_count: number }>(
          `/exams/${input.examId}/remind`,
          null,
          { params: { channel: input.channel ?? "dingtalk" } },
        )
      ).data,
    onSuccess: (_d, v) => {
      qc.invalidateQueries({ queryKey: ["exams", v.examId, "notifications"] });
    },
  });
}

export function useTerminateExam() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (examId: string) =>
      (await api.post<Exam>(`/exams/${examId}/terminate`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["exams"] }),
  });
}

export function useUpdateExam() {
  /** 修改考试配置（草稿/待发布/进行中可改）。 */
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      examId: string;
      name?: string;
      duration_minutes?: number;
      deadline?: string | null;
      allow_retake?: boolean;
      instructions?: string | null;
    }) => {
      const { examId, ...body } = input;
      return (await api.put<Exam>(`/exams/${examId}`, body)).data;
    },
    onSuccess: (_d, v) => {
      qc.invalidateQueries({ queryKey: ["exams"] });
      qc.invalidateQueries({ queryKey: ["exams", v.examId] });
    },
  });
}

export function useDeleteExam() {
  /** 删除考试（后端仅允许草稿/待发布状态，且无答卷）。 */
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (examId: string) =>
      (await api.delete<{ success: boolean; message: string }>(`/exams/${examId}`)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["exams"] }),
  });
}

// ──────────────────────────────────────────────────────────
// S6 统计看板
// ──────────────────────────────────────────────────────────
//
// 后端角色限制（与 config/menu.ts 的 ROUTE_ROLES 必须一致）：
//   overview / by-chapter → hr_admin, dept_manager, compliance
//   by-dept               → hr_admin, dept_manager（dept_manager 只返回本部门）
//   by-person             → hr_admin, dept_manager
//   by-question           → expert, hr_admin
//   compliance            → compliance

/** 统计看板的考试下拉：一次取最多 100 场，按状态分组展示由页面负责。 */
export function useExamOptions(enabled = true) {
  return useQuery<Paginated<Exam>>({
    queryKey: ["exams", "options"],
    queryFn: async () =>
      (await api.get<Paginated<Exam>>("/exams", { params: { page: 1, page_size: 100 } })).data,
    enabled,
    staleTime: 60_000,
  });
}

/** HR 全景看板：一次请求拿到概览 + 章节 + 部门 + 个人 + 题目（后端已按角色收口数据范围）。 */
export function useExamDashboard(examId: string | undefined, errorThreshold = 30, enabled = true) {
  return useQuery<ExamDashboard>({
    queryKey: ["statistics", examId ?? "", "dashboard", errorThreshold],
    queryFn: async () =>
      (
        await api.get<ExamDashboard>(`/statistics/exams/${examId}/dashboard`, {
          params: { error_threshold: errorThreshold },
        })
      ).data,
    enabled: Boolean(examId) && enabled,
  });
}

export function useExamOverview(examId: string | undefined, enabled = true) {
  return useQuery<ExamOverview>({
    queryKey: ["statistics", examId ?? "", "overview"],
    queryFn: async () =>
      (await api.get<ExamOverview>(`/statistics/exams/${examId}/overview`)).data,
    enabled: Boolean(examId) && enabled,
  });
}

export function useChapterStats(examId: string | undefined, enabled = true) {
  return useQuery<{ chapters: ChapterStatRow[] }>({
    queryKey: ["statistics", examId ?? "", "by-chapter"],
    queryFn: async () =>
      (await api.get<{ chapters: ChapterStatRow[] }>(`/statistics/exams/${examId}/by-chapter`))
        .data,
    enabled: Boolean(examId) && enabled,
  });
}

export function useDeptStats(examId: string | undefined, enabled = true) {
  return useQuery<{ departments: DepartmentStatRow[] }>({
    queryKey: ["statistics", examId ?? "", "by-dept"],
    queryFn: async () =>
      (
        await api.get<{ departments: DepartmentStatRow[] }>(
          `/statistics/exams/${examId}/by-dept`,
        )
      ).data,
    enabled: Boolean(examId) && enabled,
  });
}

export function usePersonStats(examId: string | undefined, enabled = true) {
  return useQuery<{ persons: PersonStatRow[] }>({
    queryKey: ["statistics", examId ?? "", "by-person"],
    queryFn: async () =>
      (await api.get<{ persons: PersonStatRow[] }>(`/statistics/exams/${examId}/by-person`)).data,
    enabled: Boolean(examId) && enabled,
  });
}

/**
 * 按题目分析（含错误率与 needs_review 标记）。
 *
 * error_threshold 传给后端参与 needs_review 判定，保证「服务端标记」与
 * 页面阈值口径一致（此前前端本地过滤，两者可能不一致）。
 */
export function useQuestionStats(
  examId: string | undefined,
  errorThreshold = 30,
  enabled = true,
) {
  return useQuery<{
    questions: QuestionStatRow[];
    needs_review_count: number;
    error_threshold: number;
  }>({
    queryKey: ["statistics", examId ?? "", "by-question", errorThreshold],
    queryFn: async () =>
      (
        await api.get<{
          questions: QuestionStatRow[];
          needs_review_count: number;
          error_threshold: number;
        }>(`/statistics/exams/${examId}/by-question`, {
          params: { error_threshold: errorThreshold },
        })
      ).data,
    enabled: Boolean(examId) && enabled,
  });
}

/** 专家视角的题目质量分析：不传 examId 表示跨全部已发布考试汇总。 */
export function useQuestionQuality(
  params: { exam_id?: string; error_threshold?: number; limit?: number } = {},
  enabled = true,
) {
  return useQuery<{
    questions: QuestionStatRow[];
    total_scanned: number;
    needs_review_count: number;
    error_threshold: number;
    exam_ids: string[];
  }>({
    queryKey: ["statistics", "question-quality", params],
    queryFn: async () =>
      (
        await api.get<{
          questions: QuestionStatRow[];
          total_scanned: number;
          needs_review_count: number;
          error_threshold: number;
          exam_ids: string[];
        }>("/statistics/question-quality", { params })
      ).data,
    enabled,
  });
}

export function useComplianceOverview(period?: string, enabled = true) {
  return useQuery<ComplianceOverviewResponse>({
    queryKey: ["statistics", "compliance", period ?? "all"],
    queryFn: async () =>
      (
        await api.get<ComplianceOverviewResponse>("/statistics/compliance", {
          params: period ? { period } : undefined,
        })
      ).data,
    enabled,
  });
}

/** 答卷逐题明细（部门/个人明细表下钻）。 */
export function useAnswerDetail(answerId: string | undefined, enabled = true) {
  return useQuery<AnswerDetail>({
    queryKey: ["statistics", "answers", answerId ?? ""],
    queryFn: async () =>
      (await api.get<AnswerDetail>(`/statistics/answers/${answerId}`)).data,
    enabled: Boolean(answerId) && enabled,
  });
}

// ──────────────────────────────────────────────────────────
// S7 通知 / 消息中心
// ──────────────────────────────────────────────────────────

export interface InboxItem {
  type: string;
  level: "info" | "warn" | "success" | "danger";
  title: string;
  detail: string;
  link?: string | null;
  created_at?: string;
}

export function useInbox(enabled = true) {
  return useQuery<{ items: InboxItem[]; total: number; unread: number }>({
    queryKey: ["notifications", "inbox"],
    queryFn: async () =>
      (await api.get<{ items: InboxItem[]; total: number; unread: number }>("/notifications/inbox"))
        .data,
    enabled,
    refetchInterval: 60_000,
  });
}

export function useNotificationChannels() {
  return useQuery<{ channels: { id: string; name: string; enabled: boolean }[] }>({
    queryKey: ["notifications", "channels"],
    queryFn: async () =>
      (
        await api.get<{ channels: { id: string; name: string; enabled: boolean }[] }>(
          "/notifications/channels",
        )
      ).data,
    staleTime: Infinity,
  });
}

export function useNotificationTemplates() {
  return useQuery<{ templates: { type: string; title: string; content: string }[] }>({
    queryKey: ["notifications", "templates"],
    queryFn: async () =>
      (
        await api.get<{ templates: { type: string; title: string; content: string }[] }>(
          "/notifications/templates",
        )
      ).data,
    staleTime: Infinity,
  });
}

// ──────────────────────────────────────────────────────────
// S8 系统管理（组织架构 / 权限 / 审计日志）
// ──────────────────────────────────────────────────────────

export function useDepartments(enabled = true) {
  return useQuery<Department[]>({
    queryKey: ["system", "departments"],
    queryFn: async () => (await api.get<Department[]>("/system/departments")).data,
    enabled,
    staleTime: 60_000,
  });
}

export interface SystemUserParams {
  page?: number;
  page_size?: number;
  role?: string;
  department_id?: string;
  keyword?: string;
}

export function useSystemUsers(params: SystemUserParams = {}, enabled = true) {
  return useQuery<Paginated<SystemUser>>({
    queryKey: ["system", "users", params],
    queryFn: async () =>
      (await api.get<Paginated<SystemUser>>("/system/users", { params })).data,
    enabled,
  });
}

export function useUpdateSystemUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      userId: string;
      name?: string;
      role?: UserRole;
      department_id?: string | null;
      is_active?: boolean;
    }) => {
      const { userId, ...body } = input;
      return (await api.put<SystemUser>(`/system/users/${userId}`, body)).data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["system", "users"] }),
  });
}

export function useRoles(enabled = true) {
  return useQuery<RoleConfig[]>({
    queryKey: ["system", "roles"],
    queryFn: async () => (await api.get<RoleConfig[]>("/system/roles")).data,
    enabled,
    staleTime: Infinity,
  });
}

export interface AuditLogParams {
  page?: number;
  page_size?: number;
  action?: string;
  user_id?: string;
}

export function useAuditLogs(params: AuditLogParams = {}, enabled = true) {
  return useQuery<Paginated<AuditLogItem>>({
    queryKey: ["system", "audit-logs", params],
    queryFn: async () =>
      (await api.get<Paginated<AuditLogItem>>("/system/audit-logs", { params })).data,
    enabled,
  });
}

export function useTriggerOrgSync() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      (await api.post<{ message: string }>("/system/org/sync")).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["system", "departments"] });
      qc.invalidateQueries({ queryKey: ["system", "users"] });
    },
  });
}

// ──────────────────────────────────────────────────────────
// S5 我的考试（员工端 / 个人中心）
// ──────────────────────────────────────────────────────────

/** 员工考试中心：进行中 + 已截止的考试及本人状态。 */
export function useMyExams(enabled = true) {
  return useQuery<{ items: MyExamItem[]; total: number }>({
    queryKey: ["exam-taking", "my-exams"],
    queryFn: async () =>
      (await api.get<{ items: MyExamItem[]; total: number }>("/exam-taking/my-exams")).data,
    enabled,
  });
}

/** 我的考试记录（成绩历史）。 */
export function useMyExamRecords(
  params: { page?: number; page_size?: number; user_id?: string } = {},
  enabled = true,
) {
  return useQuery<Paginated<ExamRecord>>({
    queryKey: ["exam-taking", "records", params],
    queryFn: async () =>
      (await api.get<Paginated<ExamRecord>>("/exam-taking/records", { params })).data,
    enabled,
  });
}
