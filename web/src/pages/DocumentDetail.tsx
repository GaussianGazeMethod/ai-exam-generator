import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  List,
  Progress,
  Row,
  Space,
  Table,
  Tag,
  Tooltip,
  Tree,
  Typography,
  message,
} from "antd";
import { ExperimentOutlined, ReloadOutlined, DeleteOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";

import {
  useDeleteDocument,
  useDocumentQuestions,
  useDocumentStructure,
  useGenerationTask,
  useReparseDocument,
  useTriggerGeneration,
} from "../api/hooks";
import {
  DOC_STATUS_COLOR,
  DOC_STATUS_LABEL,
  QUESTION_TYPE_COLOR,
  QUESTION_TYPE_LABEL,
  QUESTION_STATUS_COLOR,
  QUESTION_STATUS_LABEL,
  parseStructuredContent,
  type DocumentQuestion,
} from "../api/types";

const { Title, Text, Paragraph } = Typography;

export default function DocumentDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const { data: doc, structured, isLoading } = useDocumentStructure(id);
  const { data: docQuestions } = useDocumentQuestions(id);
  const reparse = useReparseDocument();
  const deleteDoc = useDeleteDocument();
  const trigger = useTriggerGeneration();

  const [taskId, setTaskId] = useState<string | undefined>();
  const { data: task } = useGenerationTask(taskId);

  // 章节树：章 → 条款（spec 要求「点击章节展开条款原文」）
  const treeData = useMemo(() => {
    const content = structured ?? parseStructuredContent(doc?.structured_content ?? null);
    if (!content) return [];
    return content.chapters.map((ch) => ({
      title: `${ch.chapter_no || "（未编号）"} ${ch.title}`.trim(),
      key: `ch-${ch.chapter_no}-${ch.title}`,
      children: ch.clauses.map((cl) => ({
        title: `${cl.clause_no || "(正文)"} ${cl.text.slice(0, 30)}${
          cl.text.length > 30 ? "…" : ""
        }`,
        key: `cl-${ch.chapter_no}-${cl.clause_no}`,
        isLeaf: true,
        clause: cl,
      })),
    }));
  }, [structured, doc?.structured_content]);

  const parsed = structured ?? parseStructuredContent(doc?.structured_content ?? null);
  const [selectedClause, setSelectedClause] = useState<{
    clause_no: string;
    text: string;
  } | null>(null);

  const qColumns: ColumnsType<DocumentQuestion> = [
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
      render: (t: DocumentQuestion["question_type"]) => (
        <Tag color={QUESTION_TYPE_COLOR[t]}>{QUESTION_TYPE_LABEL[t]}</Tag>
      ),
    },
    {
      title: "来源",
      key: "source",
      width: 150,
      render: (_, r) => `${r.chapter_no ?? ""} ${r.clause_no ?? ""}`.trim() || "—",
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 90,
      render: (s: DocumentQuestion["status"]) => (
        <Tag color={QUESTION_STATUS_COLOR[s]}>{QUESTION_STATUS_LABEL[s]}</Tag>
      ),
    },
  ];

  const handleTrigger = async () => {
    if (!id) return;
    try {
      const res = await trigger.mutateAsync({ document_id: id, max_points: 40 });
      setTaskId(res.task_id);
      message.success("出题任务已启动，可在下方查看实时进度");
    } catch (err) {
      message.error(`触发出题失败：${(err as Error).message}`);
    }
  };

  if (isLoading) {
    return <Card loading />;
  }

  if (!doc) {
    return <Empty description="制度不存在" />;
  }

  const running = task?.status === "running" || task?.status === "pending";

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
          {doc.name}
        </Title>
        <Space>
          <Button
            icon={<ReloadOutlined />}
            loading={reparse.isPending}
            onClick={async () => {
              try {
                const res = await reparse.mutateAsync(doc.id);
                message.success(res.message);
              } catch (err) {
                message.error(`重新解析失败：${(err as Error).message}`);
              }
            }}
          >
            重新解析
          </Button>
          <Button
            type="primary"
            icon={<ExperimentOutlined />}
            loading={trigger.isPending || running}
            onClick={handleTrigger}
          >
            触发 AI 出题
          </Button>
          <Button
            danger
            icon={<DeleteOutlined />}
            onClick={async () => {
              try {
                await deleteDoc.mutateAsync(doc.id);
                message.success("已删除");
                navigate("/documents");
              } catch (err) {
                message.error(`删除失败：${(err as Error).message}`);
              }
            }}
          >
            删除
          </Button>
        </Space>
      </div>

      {/* 出题任务进度（轮询 /generate/tasks/{id}） */}
      {task && (
        <Card
          size="small"
          style={{ marginBottom: 16 }}
          title={
            <Space>
              <span>AI 出题进度</span>
              <Tag
                color={
                  task.status === "completed"
                    ? "green"
                    : task.status === "failed"
                      ? "red"
                      : "processing"
                }
              >
                {task.status === "completed"
                  ? "已完成"
                  : task.status === "failed"
                    ? "失败"
                    : "进行中"}
              </Tag>
            </Space>
          }
        >
          <Progress
            percent={task.progress}
            status={
              task.status === "failed"
                ? "exception"
                : task.status === "completed"
                  ? "success"
                  : "active"
            }
          />
          <Text type="secondary">{task.message}</Text>
          {task.generated_count > 0 && (
            <div style={{ marginTop: 8 }}>
              <Space wrap>
                <span>
                  生成题目：<b>{task.generated_count}</b> 道
                </span>
                <span>
                  考点：<b>{task.key_point_count}</b> 个
                </span>
                {Object.entries(task.type_stats).map(([k, v]) => (
                  <Tag key={k} color={QUESTION_TYPE_COLOR[k as keyof typeof QUESTION_TYPE_COLOR]}>
                    {QUESTION_TYPE_LABEL[k as keyof typeof QUESTION_TYPE_LABEL]} {v}
                  </Tag>
                ))}
              </Space>
              <div style={{ marginTop: 8 }}>
                <Button
                  type="link"
                  style={{ paddingLeft: 0 }}
                  onClick={() => navigate("/review")}
                >
                  前往审核工作台 →
                </Button>
              </div>
            </div>
          )}
          {task.errors.length > 0 && (
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 8 }}
              message={`${task.errors.length} 项告警`}
              description={
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {task.errors.slice(0, 5).map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              }
            />
          )}
        </Card>
      )}

      <Row gutter={16}>
        <Col span={8}>
          <Card title="基本信息" size="small">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="版本">{doc.version}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={DOC_STATUS_COLOR[doc.status]}>{DOC_STATUS_LABEL[doc.status]}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="文件名">{doc.file_name}</Descriptions.Item>
              <Descriptions.Item label="页数">{doc.page_count ?? "-"}</Descriptions.Item>
              <Descriptions.Item label="关联题目">{doc.question_count} 道</Descriptions.Item>
              <Descriptions.Item label="上传时间">
                {String(doc.created_at).replace("T", " ").slice(0, 19)}
              </Descriptions.Item>
              {parsed && (
                <>
                  <Descriptions.Item label="解析方式">{parsed.parse_method}</Descriptions.Item>
                  <Descriptions.Item label="结构统计">
                    {parsed.stats.chapter_count} 章 / {parsed.stats.section_count} 节 /{" "}
                    {parsed.stats.clause_count} 条
                  </Descriptions.Item>
                </>
              )}
            </Descriptions>
            {parsed?.warnings?.length ? (
              <Alert
                type="warning"
                showIcon
                style={{ marginTop: 8 }}
                message={parsed.warnings.join("；")}
              />
            ) : null}
          </Card>
        </Col>

        <Col span={8}>
          <Card title="章节树" size="small" styles={{ body: { maxHeight: 420, overflow: "auto" } }}>
            {treeData.length === 0 ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="尚未解析出章节结构"
              />
            ) : (
              <Tree
                treeData={treeData}
                defaultExpandAll={false}
                onSelect={(_, info) => {
                  const node = info.node as unknown as {
                    clause?: { clause_no: string; text: string };
                  };
                  if (node.clause) setSelectedClause(node.clause);
                }}
              />
            )}
          </Card>
        </Col>

        <Col span={8}>
          <Card
            title="条款原文"
            size="small"
            extra={
              selectedClause ? (
                <Tag color="blue">{selectedClause.clause_no}</Tag>
              ) : (
                <Text type="secondary">点击左侧条款查看</Text>
              )
            }
            styles={{ body: { maxHeight: 420, overflow: "auto" } }}
          >
            {selectedClause ? (
              <Paragraph style={{ whiteSpace: "pre-wrap" }}>{selectedClause.text}</Paragraph>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未选择条款" />
            )}
          </Card>
        </Col>
      </Row>

      <Card
        title={`关联题目（${docQuestions?.total ?? 0} 道）`}
        style={{ marginTop: 16 }}
        extra={
          <Button type="link" onClick={() => navigate("/questions")}>
            前往题库
          </Button>
        }
      >
        <Table<DocumentQuestion>
          rowKey="id"
          size="small"
          columns={qColumns}
          dataSource={docQuestions?.items ?? []}
          pagination={{ pageSize: 10, showTotal: (t) => `共 ${t} 条` }}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="还没有题目，点击右上角「触发 AI 出题」"
              />
            ),
          }}
        />
      </Card>
    </div>
  );
}