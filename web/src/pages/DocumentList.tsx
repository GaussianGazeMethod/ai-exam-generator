import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Button,
  Card,
  Input,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";

import { useDeleteDocument, useDocuments } from "../api/hooks";
import {
  DOC_STATUS_COLOR,
  DOC_STATUS_LABEL,
  type Document,
  type DocStatus,
} from "../api/types";

const { Title } = Typography;

const STATUS_OPTIONS = (Object.keys(DOC_STATUS_LABEL) as DocStatus[]).map((k) => ({
  value: k,
  label: DOC_STATUS_LABEL[k],
}));

function formatSize(bytes: number): string {
  if (!bytes) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function DocumentList() {
  const navigate = useNavigate();
  const [keyword, setKeyword] = useState("");
  const [submittedKeyword, setSubmittedKeyword] = useState("");
  const [status, setStatus] = useState<string | undefined>();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const { data, isLoading, isFetching, refetch } = useDocuments({
    page,
    page_size: pageSize,
    status,
    keyword: submittedKeyword || undefined,
  });

  const deleteDoc = useDeleteDocument();

  const columns: ColumnsType<Document> = [
    {
      title: "制度名称",
      dataIndex: "name",
      key: "name",
      render: (name: string, record) => (
        <a onClick={() => navigate(`/documents/${record.id}`)}>{name}</a>
      ),
    },
    { title: "版本", dataIndex: "version", key: "version", width: 90 },
    {
      title: "类型",
      dataIndex: "file_type",
      key: "file_type",
      width: 80,
      render: (t: string) => <Tag>{String(t).toUpperCase()}</Tag>,
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 100,
      render: (s: DocStatus) => <Tag color={DOC_STATUS_COLOR[s]}>{DOC_STATUS_LABEL[s]}</Tag>,
    },
    {
      title: "关联题目",
      dataIndex: "question_count",
      key: "question_count",
      width: 100,
      render: (n: number, record) =>
        n > 0 ? <a onClick={() => navigate(`/documents/${record.id}`)}>{n} 道</a> : "—",
    },
    {
      title: "大小",
      dataIndex: "file_size",
      key: "file_size",
      width: 100,
      render: (n: number) => formatSize(n),
    },
    {
      title: "上传时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 170,
      render: (t: string) => (t ? String(t).replace("T", " ").slice(0, 19) : "-"),
    },
    {
      title: "操作",
      key: "action",
      width: 180,
      render: (_, record) => (
        <Space>
          <Button size="small" onClick={() => navigate(`/documents/${record.id}`)}>
            详情
          </Button>
          <Popconfirm
            title="确认删除该制度？"
            description="将同时删除关联题目与向量索引，不可恢复。"
            okText="删除"
            okButtonProps={{ danger: true }}
            cancelText="取消"
            onConfirm={async () => {
              try {
                await deleteDoc.mutateAsync(record.id);
                message.success("已删除");
              } catch (err) {
                message.error(`删除失败：${(err as Error).message}`);
              }
            }}
          >
            <Button size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

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
        <Title level={3} style={{ margin: 0 }}>
          制度管理
        </Title>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => refetch()} loading={isFetching}>
            刷新
          </Button>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => navigate("/documents/upload")}
          >
            上传制度
          </Button>
        </Space>
      </div>

      <Card>
        <Space style={{ marginBottom: 16 }} wrap>
          <Input.Search
            placeholder="搜索制度名称"
            style={{ width: 260 }}
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onSearch={(v) => {
              setSubmittedKeyword(v.trim());
              setPage(1);
            }}
            allowClear
          />
          <Select
            placeholder="状态筛选"
            style={{ width: 140 }}
            allowClear
            value={status}
            onChange={(v) => {
              setStatus(v);
              setPage(1);
            }}
            options={STATUS_OPTIONS}
          />
        </Space>

        <Table<Document>
          rowKey="id"
          loading={isLoading}
          columns={columns}
          dataSource={data?.items ?? []}
          pagination={{
            current: page,
            pageSize,
            total: data?.total ?? 0,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p, ps) => {
              setPage(p);
              setPageSize(ps);
            },
          }}
        />
      </Card>
    </div>
  );
}