import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Input,
  List,
  Popconfirm,
  Progress,
  QRCode,
  Row,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import {
  CopyOutlined,
  ReloadOutlined,
  SendOutlined,
  StopOutlined,
  SwapOutlined,
} from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";

import {
  useExam,
  useExamNotifications,
  useExamProgress,
  useExamQuestions,
  usePublishExam,
  useRemindExam,
  useSwapExamQuestion,
  useTerminateExam,
} from "../api/hooks";
import {
  EXAM_STATUS_COLOR,
  EXAM_STATUS_LABEL,
  QUESTION_TYPE_COLOR,
  QUESTION_TYPE_LABEL,
  parseCandidateScope,
  parseIdArray,
  type ComposePreviewQuestion,
  type ExamNotification,
  type ExamProgress,
  type ExamStatus,
} from "../api/types";

const { Title, Text, Paragraph } = Typography;

export default function ExamDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const { data: exam, isLoading } = useExam(id);
  const { data: questions, refetch: refetchQuestions } = useExamQuestions(id);
  const { data: progress } = useExamProgress(id);
  const { data: notifications } = useExamNotifications(id);

  const publish = usePublishExam();
  const remind = useRemindExam();
  const terminate = useTerminateExam();
  const swap = useSwapExamQuestion();

  const [userSearch, setUserSearch] = useState("");

  if (isLoading) return <Spin style={{ display: "block", margin: "120px auto" }} size="large" />;
  if (!exam) {
    return (
      <Card>
        <Empty description="考试不存在">
          <Button type="primary" onClick={() => navigate("/exams")}>
            返回列表
          </Button>
        </Empty>
      </Card>
    );
  }

  const scope = parseCandidateScope(exam.candidate_scope);
  const questionIds = parseIdArray(exam.question_ids);
  const canEdit = exam.status === "draft" || exam.status === "pending";
  const isRunning = exam.status === "in_progress";
  const finished = exam.status === "finished" || exam.status === "terminated";
  const pct =
    exam.candidate_count > 0
      ? Math.round((exam.completed_count / exam.candidate_count) * 100)
      : 0;

  // 考试链接指向**移动答题端**（5174），而不是管理端（5173）。
  // 此前写的是 replace(":5173", ":5173")，等于没换端口，扫码后打开的是
  // 管理端地址，考生根本进不去答题页。
  const mobileOrigin = `${window.location.protocol}//${window.location.hostname}:5174`;
  const examLink = exam.exam_link ? `${mobileOrigin}${exam.exam_link}` : "";

  const notStarted = (progress?.not_started_users ?? []).filter((u) =>
    userSearch ? u.name.includes(userSearch) : true,
  );

  const qColumns: ColumnsType<ComposePreviewQuestion> = [
    {
      title: "题号",
      key: "idx",
      width: 60,
      render: (_, __, i) => i + 1,
    },
    {
      title: "题型",
      dataIndex: "question_type",
      key: "type",
      width: 90,
      render: (t: ComposePreviewQuestion["question_type"]) => (
        <Tag color={QUESTION_TYPE_COLOR[t]}>{QUESTION_TYPE_LABEL[t]}</Tag>
      ),
    },
    { title: "题目", dataIndex: "content", key: "content", ellipsis: true },
    {
      title: "来源",
      dataIndex: "source",
      key: "source",
      width: 150,
      render: (s: string) => s || "—",
    },
    {
      title: "操作",
      key: "action",
      width: 100,
      render: (_, record) =>
        canEdit ? (
          <Button
            size="small"
            icon={<SwapOutlined />}
            loading={swap.isPending}
            onClick={async () => {
              try {
                await swap.mutateAsync({ examId: exam.id, old_question_id: record.id });
                message.success("已换题");
                refetchQuestions();
              } catch (err) {
                message.error(`换题失败：${(err as Error).message}`);
              }
            }}
          >
            换一题
          </Button>
        ) : (
          <Text type="secondary">—</Text>
        ),
    },
  ];

  const notStartedColumns: ColumnsType<ExamProgress["not_started_users"][number]> = [
    { title: "姓名", dataIndex: "name", key: "name" },
    {
      title: "邮箱",
      dataIndex: "email",
      key: "email",
      render: (v: string | null) => v || "—",
    },
  ];

  const channelLabel: Record<string, string> = {
    wecom: "企业微信",
    dingtalk: "钉钉",
    feishu: "飞书",
    sms: "短信",
  };

  return (
    <div>
      <div
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}
      >
        <Space align="baseline">
          <Button onClick={() => navigate("/exams")}>返回列表</Button>
          <Title level={3} style={{ margin: 0 }}>
            {exam.name}
          </Title>
          <Tag color={EXAM_STATUS_COLOR[exam.status]}>{EXAM_STATUS_LABEL[exam.status]}</Tag>
        </Space>
        <Space>
          {(exam.status === "pending" || exam.status === "draft") && (
            <Button
              type="primary"
              icon={<SendOutlined />}
              loading={publish.isPending}
              onClick={async () => {
                try {
                  const res = await publish.mutateAsync({ examId: exam.id, notify: true });
                  message.success(`已发布：${res.exam_link}`);
                } catch (err) {
                  message.error(`发布失败：${(err as Error).message}`);
                }
              }}
            >
              发布考试
            </Button>
          )}
          {isRunning && (
            <>
              <Button
                icon={<SendOutlined />}
                loading={remind.isPending}
                onClick={async () => {
                  try {
                    const res = await remind.mutateAsync({ examId: exam.id });
                    message.success(res.message);
                  } catch (err) {
                    message.error(`催促失败：${(err as Error).message}`);
                  }
                }}
              >
                一键催促未考
              </Button>
              <Popconfirm
                title="确认提前终止考试？"
                okText="终止"
                okButtonProps={{ danger: true }}
                cancelText="取消"
                onConfirm={async () => {
                  try {
                    await terminate.mutateAsync(exam.id);
                    message.success("已终止");
                  } catch (err) {
                    message.error(`终止失败：${(err as Error).message}`);
                  }
                }}
              >
                <Button danger icon={<StopOutlined />}>
                  终止考试
                </Button>
              </Popconfirm>
            </>
          )}
          {finished && (
            <Button type="primary" onClick={() => navigate("/statistics")}>
              查看统计看板 →
            </Button>
          )}
        </Space>
      </div>

      <Row gutter={16}>
        <Col span={10}>
          <Card title="基本信息" size="small">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="考试时长">{exam.duration_minutes} 分钟</Descriptions.Item>
              <Descriptions.Item label="截止时间">{exam.deadline || "未设置"}</Descriptions.Item>
              <Descriptions.Item label="考生范围">
                {scope.type === "all" ? "全公司" : `指定部门（${scope.dept_ids?.length ?? 0} 个）`}
              </Descriptions.Item>
              <Descriptions.Item label="允许重考">
                {exam.allow_retake ? `是（最多 ${exam.max_retake_count} 次）` : "否"}
              </Descriptions.Item>
              <Descriptions.Item label="题目数量">{questionIds.length} 题</Descriptions.Item>
              <Descriptions.Item label="发布时间">
                {exam.published_at ? String(exam.published_at).replace("T", " ").slice(0, 19) : "未发布"}
              </Descriptions.Item>
            </Descriptions>
            {exam.instructions && (
              <>
                <Text strong style={{ fontSize: 13 }}>
                  考试须知
                </Text>
                <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 4 }}>
                  {exam.instructions}
                </Paragraph>
              </>
            )}
          </Card>
        </Col>

        <Col span={7}>
          <Card title="进度监控" size="small">
            <div style={{ textAlign: "center", marginBottom: 12 }}>
              <Progress
                type="dashboard"
                percent={pct}
                format={() => `${pct}%`}
                strokeColor={pct >= 100 ? "#52c41a" : "#1677ff"}
              />
            </div>
            <Space direction="vertical" size={4} style={{ width: "100%" }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <Text type="secondary">已考</Text>
                <Text strong>{exam.completed_count} 人</Text>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <Text type="secondary">未考</Text>
                <Text strong type={(progress?.not_started_users.length ?? 0) > 0 ? "danger" : undefined}>
                  {progress?.not_started_users.length ?? Math.max(0, exam.candidate_count - exam.completed_count)} 人
                </Text>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <Text type="secondary">应考</Text>
                <Text strong>{exam.candidate_count} 人</Text>
              </div>
            </Space>
          </Card>

          <Card title="题库分布" size="small" style={{ marginTop: 12 }}>
            {(() => {
              const stats: Record<string, number> = {};
              (questions?.items ?? []).forEach((q) => {
                stats[q.question_type] = (stats[q.question_type] ?? 0) + 1;
              });
              return (
                <Space wrap>
                  {Object.entries(stats).map(([t, n]) => (
                    <Tag key={t} color={QUESTION_TYPE_COLOR[t as ComposePreviewQuestion["question_type"]]}>
                      {QUESTION_TYPE_LABEL[t as ComposePreviewQuestion["question_type"]]} {n}
                    </Tag>
                  ))}
                </Space>
              );
            })()}
          </Card>
        </Col>

        <Col span={7}>
          <Card title="二维码 + 链接" size="small">
            {exam.exam_link ? (
              <>
                <div style={{ textAlign: "center" }}>
                  <QRCode value={examLink} size={130} />
                </div>
                <Input.Group compact style={{ marginTop: 12 }}>
                  <Input value={examLink} readOnly style={{ width: "calc(100% - 40px)" }} />
                  <Button
                    icon={<CopyOutlined />}
                    onClick={() => {
                      navigator.clipboard.writeText(examLink);
                      message.success("链接已复制");
                    }}
                  />
                </Input.Group>
                <Text type="secondary" style={{ fontSize: 12, display: "block", marginTop: 8 }}>
                  考生扫码或点击链接进入答题
                </Text>
              </>
            ) : (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="发布后生成二维码与链接"
              />
            )}
          </Card>
        </Col>
      </Row>

      <Card title={`试卷题目（${questions?.total ?? 0} 题）`} style={{ marginTop: 16 }}>
        <Table<ComposePreviewQuestion>
          rowKey="id"
          size="small"
          loading={!questions}
          columns={qColumns}
          dataSource={questions?.items ?? []}
          pagination={false}
          locale={{
            emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="试卷暂无题目" />,
          }}
        />
      </Card>

      {/* 未考人员列表 */}
      {(isRunning || finished) && (
        <Card
          title={`未考人员（${progress?.not_started_users.length ?? 0} 人）`}
          style={{ marginTop: 16 }}
          extra={
            <Input.Search
              placeholder="搜索姓名"
              style={{ width: 200 }}
              value={userSearch}
              onChange={(e) => setUserSearch(e.target.value)}
              allowClear
            />
          }
        >
          <Table<ExamProgress["not_started_users"][number]>
            rowKey="id"
            size="small"
            columns={notStartedColumns}
            dataSource={notStarted}
            pagination={{ pageSize: 10, showTotal: (t) => `共 ${t} 人` }}
            locale={{
              emptyText: (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有未考人员" />
              ),
            }}
          />
        </Card>
      )}

      {/* 推送记录 */}
      <Card title="推送记录" style={{ marginTop: 16 }}>
        <List<ExamNotification>
          size="small"
          dataSource={notifications?.items ?? []}
          locale={{
            emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无推送记录" />,
          }}
          renderItem={(n) => (
            <List.Item>
              <List.Item.Meta
                title={
                  <Space>
                    <Tag color="blue">{channelLabel[n.channel] ?? n.channel}</Tag>
                    <Text strong>{n.title}</Text>
                    <Tag color={n.is_sent ? "green" : "red"}>{n.is_sent ? "已发送" : "发送失败"}</Tag>
                  </Space>
                }
                description={
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {n.content}　·　接收 {n.recipient_count} 人　·　{n.sent_at ?? "—"}
                  </Text>
                }
              />
            </List.Item>
          )}
        />
      </Card>
    </div>
  );
}