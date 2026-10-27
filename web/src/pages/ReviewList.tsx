import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Button,
  Card,
  Empty,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";

import {
  useBatchReview,
  useDocuments,
  usePendingQuestions,
  useReviewQuestion,
} from "../api/hooks";
import {
  QUESTION_TYPE_COLOR,
  QUESTION_TYPE_LABEL,
  parseJsonArray,
  type Question,
} from "../api/types";

const { Title, Text } = Typography;

export default function ReviewList() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [documentId, setDocumentId] = useState<string | undefined>(
    searchParams.get("document_id") ?? undefined,
  );
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const { data: documents } = useDocuments({ page: 1, page_size: 100 });
  const { data, isLoading, isFetching, refetch } = usePendingQuestions({
    page,
    page_size: pageSize,
    document_id: documentId,
  });

  const review = useReviewQuestion();
  const batch = useBatchReview();

  const docOptions = (documents?.items ?? []).map((d) => ({
    value: d.id,
    label: d.name,
  }));

  const columns: ColumnsType<Question> = [
    {
      title: "题目摘要",
      dataIndex: "content",
      key: "content",
      ellipsis: true,
      render: (text: string, record) => (
        <a onClick={() => navigate(`/review/${record.id}`)}>{text}</a>
      ),
    },
    {
      title: "题型",
      dataIndex: "question_type",
      key: "question_type",
      width: 90,
      render: (t: Question["question_type"]) => (
        <Tag color={QUESTION_TYPE_COLOR[t]}>{QUESTION_TYPE_LABEL[t]}</Tag>
      ),
    },
    {
      title: "来源章节",
      key: "source",
      width: 160,
      render: (_, r) => `${r.chapter_no ?? ""} ${r.clause_no ?? ""}`.trim() || "—",
    },
    {
      title: "答案",
      key: "answer",
      width: 120,
      ellipsis: true,
      render: (_, r) => parseJsonArray(r.answer).join("、") || "—",
    },
    {
      title: "生成时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 170,
      render: (t: string) => (t ? String(t).replace("T", " ").slice(0, 19) : "-"),
    },
    {
      title: "操作",
      key: "action",
      width: 220,
      render: (_, record) => (
        <Space>
          <Button
            size="small"
            type="primary"
            onClick={() => navigate(`/review/${record.id}`)}
          >
            审核
          </Button>
          <Button
            size="small"
            onClick={async () => {
              try {
                await review.mutateAsync({ questionId: record.id, action: "approve" });
                message.success("已通过");
              } catch (err) {
                message.error(`操作失败：${(err as Error).message}`);
              }
            }}
          >
            通过
          </Button>
          <Popconfirm
            title="弃用该题目？"
            okText="弃用"
            okButtonProps={{ danger: true }}
            cancelText="取消"
            onConfirm={async () => {
              try {
                await review.mutateAsync({
                  questionId: record.id,
                  action: "reject",
                  reject_reason: "专家审核弃用",
                });
                message.success("已弃用");
              } catch (err) {
                message.error(`操作失败：${(err as Error).message}`);
              }
            }}
          >
            <Button size="small" danger>
              弃用
            </Button>
          </Popconfirm>
        </Space>
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
            题目审核
          </Title>
          <Text type="secondary">待审核 {total} 道</Text>
        </Space>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => refetch()} loading={isFetching}>
            刷新
          </Button>
          <Popconfirm
            title={`确认全部通过 ${selectedIds.length} 道题目？`}
            okText="全部通过"
            cancelText="取消"
            disabled={selectedIds.length === 0}
            onConfirm={async () => {
              try {
                const res = await batch.mutateAsync({
                  question_ids: selectedIds,
                  action: "approve",
                });
                message.success(res.message);
                setSelectedIds([]);
              } catch (err) {
                message.error(`批量操作失败：${(err as Error).message}`);
              }
            }}
          >
            <Button type="primary" disabled={selectedIds.length === 0}>
              批量通过（{selectedIds.length}）
            </Button>
          </Popconfirm>
          <Popconfirm
            title={`确认批量弃用 ${selectedIds.length} 道题目？`}
            okText="批量弃用"
            okButtonProps={{ danger: true }}
            cancelText="取消"
            disabled={selectedIds.length === 0}
            onConfirm={async () => {
              try {
                const res = await batch.mutateAsync({
                  question_ids: selectedIds,
                  action: "reject",
                });
                message.success(res.message);
                setSelectedIds([]);
              } catch (err) {
                message.error(`批量操作失败：${(err as Error).message}`);
              }
            }}
          >
            <Button danger disabled={selectedIds.length === 0}>
              批量弃用
            </Button>
          </Popconfirm>
        </Space>
      </div>

      <Card>
        <Space style={{ marginBottom: 16 }}>
          <Select
            placeholder="按制度筛选"
            style={{ width: 260 }}
            allowClear
            value={documentId}
            onChange={(v) => {
              setDocumentId(v);
              setPage(1);
              setSelectedIds([]);
            }}
            options={docOptions}
          />
        </Space>

        <Table<Question>
          rowKey="id"
          loading={isLoading}
          columns={columns}
          dataSource={data?.items ?? []}
          rowSelection={{
            selectedRowKeys: selectedIds,
            onChange: (keys) => setSelectedIds(keys as string[]),
          }}
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
                description="暂无待审核题目 —— 请先在制度详情页触发 AI 出题"
              />
            ),
          }}
        />
      </Card>
    </div>
  );
}