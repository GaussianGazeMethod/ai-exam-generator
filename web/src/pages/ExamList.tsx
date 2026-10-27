import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Button,
  Card,
  DatePicker,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";
import dayjs, { type Dayjs } from "dayjs";
import customParseFormat from "dayjs/plugin/customParseFormat";

// 后端 deadline 存的是固定格式字符串，解析需要它
dayjs.extend(customParseFormat);

import { useDeleteExam, useExams, useTerminateExam, useUpdateExam } from "../api/hooks";
import {
  EXAM_STATUS_COLOR,
  EXAM_STATUS_LABEL,
  parseIdArray,
  type Exam,
  type ExamStatus,
} from "../api/types";

const { Title, Text } = Typography;

const STATUS_OPTIONS = (Object.keys(EXAM_STATUS_LABEL) as ExamStatus[]).map((k) => ({
  value: k,
  label: EXAM_STATUS_LABEL[k],
}));

export default function ExamList() {
  const navigate = useNavigate();
  const [keyword, setKeyword] = useState("");
  const [submittedKeyword, setSubmittedKeyword] = useState("");
  const [status, setStatus] = useState<string | undefined>();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const terminate = useTerminateExam();
  const remove = useDeleteExam();
  const update = useUpdateExam();
  const [editing, setEditing] = useState<Exam | null>(null);
  const [editForm] = Form.useForm();

  const { data, isLoading, isFetching, refetch } = useExams({
    page,
    page_size: pageSize,
    status,
    keyword: submittedKeyword || undefined,
  });

  const columns: ColumnsType<Exam> = [
    {
      title: "考试名称",
      dataIndex: "name",
      key: "name",
      render: (name: string, record) => (
        <a onClick={() => navigate(`/exams/${record.id}`)}>{name}</a>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 100,
      render: (s: ExamStatus) => <Tag color={EXAM_STATUS_COLOR[s]}>{EXAM_STATUS_LABEL[s]}</Tag>,
    },
    {
      title: "题目数",
      key: "qcount",
      width: 90,
      render: (_, r) => `${parseIdArray(r.question_ids).length} 题`,
    },
    {
      title: "已考 / 应考",
      key: "progress",
      width: 150,
      render: (_, r) => {
        const pct = r.candidate_count > 0 ? Math.round((r.completed_count / r.candidate_count) * 100) : 0;
        return (
          <Space direction="vertical" size={2} style={{ width: "100%" }}>
            <Text style={{ fontSize: 12 }}>
              {r.completed_count} / {r.candidate_count}（{pct}%）
            </Text>
            <Progress percent={pct} size="small" showInfo={false} />
          </Space>
        );
      },
    },
    {
      title: "时长",
      dataIndex: "duration_minutes",
      key: "duration_minutes",
      width: 90,
      render: (n: number) => `${n} 分钟`,
    },
    {
      title: "截止时间",
      dataIndex: "deadline",
      key: "deadline",
      width: 160,
      render: (t: string | null) => t || "—",
    },
    {
      title: "发布时间",
      dataIndex: "published_at",
      key: "published_at",
      width: 170,
      render: (t: string | null) => (t ? String(t).replace("T", " ").slice(0, 19) : "—"),
    },
    {
      title: "操作",
      key: "action",
      width: 260,
      render: (_, record) => (
        <Space>
          <Button size="small" onClick={() => navigate(`/exams/${record.id}`)}>
            详情
          </Button>
          {/*
            草稿/待发布时提供「编辑」与「删除」。
            用户反馈：「草稿状态的考试任务无法再编辑或者删除」——后果是点错一步就
            产生一个清理不掉的草稿，列表越堆越多，用户只能重建，制造垃圾数据。
          */}
          {(record.status === "draft" || record.status === "pending") && (
            <Button
              size="small"
              icon={<EditOutlined />}
              onClick={() => {
                setEditing(record);
                editForm.setFieldsValue({
                  name: record.name,
                  duration_minutes: record.duration_minutes,
                  deadline: record.deadline ?? null,
                  instructions: record.instructions ?? null,
                });
              }}
            >
              编辑
            </Button>
          )}
          {(record.status === "draft" || record.status === "pending") && (
            <Popconfirm
              title="确认删除该考试？"
              description="删除后不可恢复。已发布或已有答卷的考试无法删除。"
              okText="删除"
              okButtonProps={{ danger: true }}
              cancelText="取消"
              onConfirm={async () => {
                try {
                  const res = await remove.mutateAsync(record.id);
                  message.success(res.message ?? "已删除");
                } catch (err) {
                  // 后端会说明原因（例如状态不允许），原样透出，不要吞掉
                  message.error(`删除失败：${(err as Error).message}`);
                }
              }}
            >
              <Button size="small" danger icon={<DeleteOutlined />}>
                删除
              </Button>
            </Popconfirm>
          )}
          {record.status === "in_progress" && (
            <Popconfirm
              title="确认提前终止该考试？"
              description="终止后考生将无法继续答题。"
              okText="终止"
              okButtonProps={{ danger: true }}
              cancelText="取消"
              onConfirm={async () => {
                try {
                  await terminate.mutateAsync(record.id);
                  message.success("已终止");
                } catch (err) {
                  message.error(`终止失败：${(err as Error).message}`);
                }
              }}
            >
              <Button size="small" danger>
                终止
              </Button>
            </Popconfirm>
          )}
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
        <Space align="baseline">
          <Title level={3} style={{ margin: 0 }}>
            考试管理
          </Title>
          <Text type="secondary">共 {data?.total ?? 0} 场</Text>
        </Space>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => refetch()} loading={isFetching}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate("/exams/create")}>
            新建考试
          </Button>
        </Space>
      </div>

      <Card>
        <Space style={{ marginBottom: 16 }} wrap>
          <Input.Search
            placeholder="搜索考试名称"
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

        <Table<Exam>
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
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="还没有考试 —— 请先确保题库有已通过题目，再点「新建考试」"
              />
            ),
          }}
        />
      </Card>

      {/* 编辑草稿/待发布考试：此前草稿状态没有任何编辑入口 */}
      <Modal
        open={editing !== null}
        title={`编辑考试：${editing?.name ?? ""}`}
        okText="保存"
        cancelText="取消"
        confirmLoading={update.isPending}
        onCancel={() => setEditing(null)}
        onOk={async () => {
          if (!editing) return;
          try {
            const values = await editForm.validateFields();
            await update.mutateAsync({ examId: editing.id, ...values });
            message.success("已保存");
            setEditing(null);
          } catch (err) {
            const msg = (err as Error).message;
            if (msg?.includes("must be")) return; // 表单校验错误
            message.error(`保存失败：${msg}`);
          }
        }}
      >
        <Form form={editForm} layout="vertical">
          <Form.Item
            name="name"
            label="考试名称"
            rules={[{ required: true, message: "请填写考试名称" }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="duration_minutes"
            label="考试时长（分钟）"
            rules={[{ required: true, message: "请填写考试时长" }]}
          >
            <InputNumber min={5} max={300} style={{ width: 200 }} />
          </Form.Item>
          <Form.Item
            name="deadline"
            label="截止日期 + 时间"
            getValueProps={(v?: string) => ({
              value: v ? dayjs(v, "YYYY-MM-DD HH:mm") : undefined,
            })}
            normalize={(v?: Dayjs | null) => (v ? v.format("YYYY-MM-DD HH:mm") : null)}
          >
            <DatePicker
              showTime={{ format: "HH:mm", minuteStep: 5 }}
              format="YYYY-MM-DD HH:mm"
              placeholder="选择截止日期与时间"
              style={{ width: 260 }}
            />
          </Form.Item>
          <Form.Item name="instructions" label="补充说明（考试须知）">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}