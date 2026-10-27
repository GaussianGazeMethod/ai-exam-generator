import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Button,
  Card,
  Empty,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";

import { useDocuments, useQuestions, useToggleQuestionActive } from "../api/hooks";
import {
  QUESTION_STATUS_COLOR,
  QUESTION_STATUS_LABEL,
  QUESTION_TYPE_COLOR,
  QUESTION_TYPE_LABEL,
  parseJsonArray,
  type Question,
  type QuestionStatus,
  type QuestionType,
} from "../api/types";

const { Title, Text } = Typography;

const TYPE_OPTIONS = (Object.keys(QUESTION_TYPE_LABEL) as QuestionType[]).map((k) => ({
  value: k,
  label: QUESTION_TYPE_LABEL[k],
}));

const STATUS_OPTIONS = (Object.keys(QUESTION_STATUS_LABEL) as QuestionStatus[]).map((k) => ({
  value: k,
  label: QUESTION_STATUS_LABEL[k],
}));

export default function QuestionBank() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [keyword, setKeyword] = useState("");
  const [submittedKeyword, setSubmittedKeyword] = useState("");
  const [questionType, setQuestionType] = useState<string | undefined>();
  const [status, setStatus] = useState<string | undefined>(
    searchParams.get("status") ?? undefined,
  );
  const [documentId, setDocumentId] = useState<string | undefined>();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const { data: documents } = useDocuments({ page: 1, page_size: 100 });
  const { data, isLoading, isFetching, refetch } = useQuestions({
    page,
    page_size: pageSize,
    keyword: submittedKeyword || undefined,
    question_type: questionType,
    status,
    document_id: documentId,
  });

  const toggleActive = useToggleQuestionActive();

  const docOptions = (documents?.items ?? []).map((d) => ({ value: d.id, label: d.name }));

  const columns: ColumnsType<Question> = [
    {
      title: "题目摘要",
      dataIndex: "content",
      key: "content",
      ellipsis: true,
      render: (text: string, record) => (
        <a onClick={() => navigate(`/questions/${record.id}`)}>{text}</a>
      ),
    },
    {
      title: "题型",
      dataIndex: "question_type",
      key: "question_type",
      width: 90,
      render: (t: QuestionType) => <Tag color={QUESTION_TYPE_COLOR[t]}>{QUESTION_TYPE_LABEL[t]}</Tag>,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 90,
      render: (s: QuestionStatus) => (
        <Tag color={QUESTION_STATUS_COLOR[s]}>{QUESTION_STATUS_LABEL[s]}</Tag>
      ),
    },
    {
      title: "来源章节",
      key: "source",
      width: 150,
      render: (_, r) => `${r.chapter_no ?? ""} ${r.clause_no ?? ""}`.trim() || "—",
    },
    {
      title: "答案",
      key: "answer",
      width: 110,
      ellipsis: true,
      render: (_, r) => parseJsonArray(r.answer).join("、") || "—",
    },
    {
      title: "使用次数",
      dataIndex: "usage_count",
      key: "usage_count",
      width: 90,
    },
    {
      title: "错误率",
      dataIndex: "error_rate",
      key: "error_rate",
      width: 90,
      render: (v: number | null) =>
        v === null || v === undefined ? "—" : <Text type={v > 50 ? "danger" : undefined}>{v}%</Text>,
    },
    {
      title: "上架",
      dataIndex: "is_active",
      key: "is_active",
      width: 80,
      render: (active: boolean, record) => (
        <Tooltip title={active ? "点击下架（暂停使用）" : "点击重新激活"}>
          <Tag
            style={{ cursor: "pointer" }}
            color={active ? "green" : "default"}
            onClick={async () => {
              await toggleActive.mutateAsync({
                questionId: record.id,
                is_active: !record.is_active,
              });
            }}
          >
            {active ? "已上架" : "已下架"}
          </Tag>
        </Tooltip>
      ),
    },
    {
      title: "操作",
      key: "action",
      width: 120,
      render: (_, record) => (
        <Button size="small" onClick={() => navigate(`/questions/${record.id}`)}>
          详情 / 编辑
        </Button>
      ),
    },
  ];

  const total = data?.total ?? 0;

  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 16,
        }}
      >
        <Space align="baseline">
          <Title level={3} style={{ margin: 0 }}>
            题库管理
          </Title>
          <Text type="secondary">共 {total} 道</Text>
        </Space>
        <Button icon={<ReloadOutlined />} onClick={() => refetch()} loading={isFetching}>
          刷新
        </Button>
      </div>

      <Card>
        <Space style={{ marginBottom: 16 }} wrap>
          <Input.Search
            placeholder="搜索题目关键词"
            style={{ width: 240 }}
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onSearch={(v) => {
              setSubmittedKeyword(v.trim());
              setPage(1);
            }}
            allowClear
          />
          <Select
            placeholder="题型"
            style={{ width: 120 }}
            allowClear
            value={questionType}
            onChange={(v) => {
              setQuestionType(v);
              setPage(1);
            }}
            options={TYPE_OPTIONS}
          />
          <Select
            placeholder="状态"
            style={{ width: 120 }}
            allowClear
            value={status}
            onChange={(v) => {
              setStatus(v);
              setPage(1);
            }}
            options={STATUS_OPTIONS}
          />
          <Select
            placeholder="来源制度"
            style={{ width: 220 }}
            allowClear
            value={documentId}
            onChange={(v) => {
              setDocumentId(v);
              setPage(1);
            }}
            options={docOptions}
          />
        </Space>

        <Table<Question>
          rowKey="id"
          loading={isLoading}
          columns={columns}
          dataSource={data?.items ?? []}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p, ps) => {
              setPage(p);
              setPageSize(ps);
            },
          }}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="题库暂无题目 —— 请先在制度详情页触发 AI 出题并在审核工作台通过"
              />
            ),
          }}
        />
      </Card>
    </div>
  );
}