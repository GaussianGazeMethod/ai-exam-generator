import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Radio,
  Row,
  Select,
  Space,
  Steps,
  Switch,
  Tag,
  Typography,
  message,
} from "antd";
import { QrcodeOutlined, ReloadOutlined, SendOutlined, SwapOutlined } from "@ant-design/icons";
import dayjs, { type Dayjs } from "dayjs";
import customParseFormat from "dayjs/plugin/customParseFormat";

// 需要它才能用 dayjs(str, "YYYY-MM-DD HH:mm") 解析后端存的固定格式字符串
dayjs.extend(customParseFormat);

import {
  useComposePreview,
  useConfigureExam,
  useCreateExam,
  useDefaultRatio,
  useDocuments,
  usePublishExam,
  useSwapExamQuestion,
  type CreateExamInput,
} from "../api/hooks";
import {
  QUESTION_TYPE_COLOR,
  QUESTION_TYPE_LABEL,
  type CandidateScope,
  type ComposePreview,
  type Exam,
  type QuestionType,
  type TypeRatio,
} from "../api/types";

const { Title, Text, Paragraph } = Typography;

/** 移动答题端地址（Vite dev: 5174）。生产由网关按 /m 前缀路由。 */
const MOBILE_ENTRY = `${window.location.protocol}//${window.location.hostname}:5174`;

const RATIO_FIELDS: { key: keyof TypeRatio; label: string }[] = [
  { key: "single_choice", label: "单选题" },
  { key: "multi_choice", label: "多选题" },
  { key: "true_false", label: "判断题" },
  { key: "fill_blank", label: "填空题" },
];

export default function ExamCreate() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [form] = Form.useForm();

  // 步骤①：制度范围
  const [documentIds, setDocumentIds] = useState<string[]>([]);

  // 步骤②：组卷
  const [ratio, setRatio] = useState<TypeRatio | null>(null);
  const [preview, setPreview] = useState<ComposePreview | null>(null);
  // 预览阶段的换题在本地生效（此时考试尚未创建），记录被替换后的题目列表
  const [draftQuestions, setDraftQuestions] = useState<ComposePreview["questions"]>([]);

  // 步骤④：发布
  const [createdExamId, setCreatedExamId] = useState<string | null>(null);

  const { data: documents } = useDocuments({ page: 1, page_size: 500 });
  const { data: defaultRatio } = useDefaultRatio();
  const compose = useComposePreview();
  const createExam = useCreateExam();
  const configureExam = useConfigureExam();
  const swapLocal = useSwapExamQuestion();
  const publish = usePublishExam();
  // 发布成功后的考试（用于展示链接/二维码入口）
  const [published, setPublished] = useState<Exam | null>(null);

  const effectiveRatio: TypeRatio = useMemo(
    () =>
      ratio ??
      defaultRatio?.ratio ?? {
        single_choice: 4,
        multi_choice: 3,
        true_false: 2,
        fill_blank: 1,
      },
    [ratio, defaultRatio],
  );

  const ratioTotal = useMemo(
    () => Object.values(effectiveRatio).reduce((a, b) => a + b, 0),
    [effectiveRatio],
  );

  const docOptions = (documents?.items ?? []).map((d) => ({
    value: d.id,
    label: `${d.name}（v${d.version}，${d.question_count} 题）`,
  }));

  // 进入步骤②时自动抽题
  const handleCompose = async () => {
    if (ratioTotal !== 10) {
      message.error(`题型配比合计必须为 10 题（当前 ${ratioTotal} 题）`);
      return;
    }
    try {
      const res = await compose.mutateAsync({
        document_ids: documentIds,
        ratio: effectiveRatio,
      });
      setPreview(res);
      setDraftQuestions(res.questions);
      if (res.warnings?.length) {
        message.warning(res.warnings.join("；"));
      } else {
        message.success(`已抽题 ${res.total} 道`);
      }
    } catch (err) {
      message.error(`组卷失败：${(err as Error).message}`);
    }
  };

  // 换一题（预览阶段：从题库重新抽同一题型替换本地草稿）
  const handleSwap = async (index: number) => {
    const old = draftQuestions[index];
    try {
      const res = await compose.mutateAsync({
        document_ids: documentIds,
        ratio: effectiveRatio,
      });
      // 从新抽的结果里找一道同题型且不在当前草稿中的题
      const inUse = new Set(draftQuestions.map((q) => q.id));
      const candidate =
        res.questions.find((q) => q.question_type === old.question_type && !inUse.has(q.id)) ??
        res.questions.find((q) => q.question_type === old.question_type && q.id !== old.id);

      if (!candidate) {
        message.warning("题库中没有可替换的同题型题目");
        return;
      }
      setDraftQuestions((prev) => prev.map((q, i) => (i === index ? candidate : q)));
      message.success("已换一题");
    } catch (err) {
      message.error(`换题失败：${(err as Error).message}`);
    }
  };

  // 步骤④ → 发布
  const handlePublish = async () => {
    if (!createdExamId) return;
    try {
      const res = await publish.mutateAsync({ examId: createdExamId, notify: false });
      setPublished(res);
      message.success("考试已发布，考生可以开始答题了");
    } catch (err) {
      const msg = (err as Error).message;
      message.error(`发布失败：${msg}`);
    }
  };

  // 步骤③ → 创建考试并进入预览
  const handleCreate = async () => {
    try {
      const values = await form.validateFields();
      const scope: CandidateScope = values.scopeType === "all"
        ? { type: "all" }
        : { type: "departments", dept_ids: [] };

      const payload: CreateExamInput = {
        name: values.name,
        document_ids: documentIds,
        duration_minutes: values.duration_minutes,
        deadline: values.deadline ?? null,
        candidate_scope: scope,
        allow_retake: values.allow_retake ?? false,
        max_retake_count: values.allow_retake ? values.max_retake_count ?? 1 : 0,
        instructions: values.instructions ?? null,
        type_ratio: effectiveRatio,
      };
      const exam = await createExam.mutateAsync(payload);
      setCreatedExamId(exam.id);

      // 把预览草稿中被替换过的题目同步到已创建的考试上
      const serverIds = JSON.parse(exam.question_ids) as string[];
      for (let i = 0; i < draftQuestions.length; i += 1) {
        const draft = draftQuestions[i];
        const serverId = serverIds[i];
        if (serverId && draft.id !== serverId) {
          await swapLocal.mutateAsync({ examId: exam.id, old_question_id: serverId });
        }
      }

      // 关键：必须落一次「配置」。创建出的考试是 draft 状态，而发布接口只接受
      // pending —— 少了这一步，用户走到预览页点发布会收到
      // 「发布失败：Request failed with status code 400（当前：draft）」，
      // 且界面上完全看不出原因。
      await configureExam.mutateAsync({
        examId: exam.id,
        name: values.name,
        duration_minutes: values.duration_minutes,
        deadline: values.deadline ?? null,
        candidate_scope: scope,
        allow_retake: values.allow_retake ?? false,
        max_retake_count: values.allow_retake ? values.max_retake_count ?? 1 : 0,
        instructions: values.instructions ?? null,
      });

      // 关键：进入步骤④。此前漏了这一步，用户点「下一步：预览与发布」后
      // 进度条和页面都不动（其实考试已经建好了），表现为「点了没反应」。
      setStep(3);
      message.success("考试已创建（待发布）");
    } catch (err) {
      const msg = (err as Error).message;
      if (msg?.includes("must be")) return; // 表单校验错误
      message.error(`创建失败：${msg}`);
    }
  };

  const steps = [
    { title: "选择制度", desc: "确定考试范围的来源制度" },
    { title: "智能组卷", desc: "配置题型配比并预览试卷" },
    { title: "配置参数", desc: "时长 / 截止 / 考生范围" },
    { title: "预览与发布", desc: "确认后发布并生成二维码" },
  ];

  return (
    <div>
      <div
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}
      >
        <Title level={3} style={{ margin: 0 }}>
          新建考试
        </Title>
        <Button onClick={() => navigate("/exams")}>返回列表</Button>
      </div>

      <Steps current={step} style={{ marginBottom: 24 }} items={steps} />

      {/* ① 选择制度 */}
      {step === 0 && (
        <Card>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 20 }}
            message="选择制度范围"
            description="系统将从所选制度已入库（审核通过）的题目中，按题型配比随机抽题。不选则从全部制度中抽题。"
          />
          <Form layout="vertical" style={{ maxWidth: 640 }}>
            <Form.Item label="制度范围（支持多选）">
              <Select
                mode="multiple"
                placeholder="留空表示全部制度"
                style={{ width: "100%" }}
                value={documentIds}
                onChange={setDocumentIds}
                options={docOptions}
                allowClear
              />
            </Form.Item>
          </Form>
          <Button type="primary" size="large" onClick={() => setStep(1)}>
            下一步：智能组卷
          </Button>
        </Card>
      )}

      {/* ② 智能组卷 */}
      {step === 1 && (
        <Card
          title="② 智能组卷"
          extra={
            <Button
              type="primary"
              icon={<ReloadOutlined />}
              loading={compose.isPending}
              onClick={handleCompose}
            >
              重新抽题
            </Button>
          }
        >
          <Row gutter={24}>
            <Col span={7}>
              <Card size="small" title="题型配比设置">
                <Text type="secondary" style={{ fontSize: 12 }}>
                  合计必须为 10 题（spec：每场考试 10 题）
                </Text>
                <div style={{ marginTop: 12 }}>
                  {RATIO_FIELDS.map((f) => (
                    <div
                      key={f.key}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        marginBottom: 10,
                      }}
                    >
                      <Tag color={QUESTION_TYPE_COLOR[f.key as QuestionType]}>{f.label}</Tag>
                      <InputNumber
                        min={0}
                        max={10}
                        value={effectiveRatio[f.key]}
                        onChange={(v) =>
                          setRatio({ ...effectiveRatio, [f.key]: Number(v) || 0 })
                        }
                        style={{ width: 90 }}
                      />
                    </div>
                  ))}
                  <div
                    style={{
                      marginTop: 12,
                      paddingTop: 12,
                      borderTop: "1px solid #f0f0f0",
                      display: "flex",
                      justifyContent: "space-between",
                    }}
                  >
                    <Text strong>合计</Text>
                    <Text strong type={ratioTotal === 10 ? "success" : "danger"}>
                      {ratioTotal} 题
                    </Text>
                  </div>
                  <Button
                    size="small"
                    style={{ marginTop: 12 }}
                    onClick={() => setRatio(defaultRatio?.ratio ?? null)}
                  >
                    重置为默认配比
                  </Button>
                </div>
              </Card>
            </Col>

            <Col span={17}>
              <Card
                size="small"
                title={
                  <Space>
                    <span>抽题结果预览</span>
                    {preview && (
                      <Tag color={preview.is_complete ? "green" : "orange"}>
                        {draftQuestions.length} / {preview.expected_total} 题
                      </Tag>
                    )}
                  </Space>
                }
                styles={{ body: { maxHeight: 460, overflow: "auto" } }}
              >
                {!preview ? (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="点击右上角「重新抽题」生成试卷"
                  />
                ) : (
                  <>
                    {preview.warnings.length > 0 && (
                      <Alert
                        type="warning"
                        showIcon
                        style={{ marginBottom: 12 }}
                        message={preview.warnings.join("；")}
                      />
                    )}
                    <List
                      size="small"
                      dataSource={draftQuestions}
                      renderItem={(q, idx) => (
                        <List.Item
                          actions={[
                            <Button
                              key="swap"
                              size="small"
                              icon={<SwapOutlined />}
                              onClick={() => handleSwap(idx)}
                              loading={compose.isPending}
                            >
                              换一题
                            </Button>,
                          ]}
                        >
                          <List.Item.Meta
                            title={
                              <Space>
                                <Text type="secondary">{idx + 1}.</Text>
                                <Tag color={QUESTION_TYPE_COLOR[q.question_type]}>
                                  {QUESTION_TYPE_LABEL[q.question_type]}
                                </Tag>
                                <Text style={{ fontSize: 13 }}>{q.content.slice(0, 46)}</Text>
                              </Space>
                            }
                            description={
                              <Text type="secondary" style={{ fontSize: 12 }}>
                                来源：{q.source || "—"}
                              </Text>
                            }
                          />
                        </List.Item>
                      )}
                    />
                  </>
                )}
              </Card>
            </Col>
          </Row>

          <div style={{ marginTop: 16 }}>
            <Space>
              <Button onClick={() => setStep(0)}>上一步</Button>
              <Button type="primary" onClick={() => setStep(2)} disabled={!preview}>
                下一步：配置参数
              </Button>
            </Space>
          </div>
        </Card>
      )}

      {/* ③ 配置参数 */}
      {step === 2 && (
        <Card title="③ 考试配置">
          <Form
            form={form}
            layout="vertical"
            style={{ maxWidth: 640 }}
            initialValues={{
              duration_minutes: 30,
              scopeType: "all",
              allow_retake: false,
              max_retake_count: 1,
            }}
          >
            <Form.Item
              name="name"
              label="考试名称"
              rules={[{ required: true, message: "请填写考试名称" }]}
            >
              <Input placeholder="如：数据安全管理规范全员考试" />
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
              // 手填「2026-12-31 23:59」这种字符串极易写错格式且无法校验，
              // 改为下拉选择：日期用日期面板、时间用「时/分」下拉，值统一为
              // `YYYY-MM-DD HH:mm`（与后端 deadline 字符串契约一致）。
              getValueProps={(v?: string) => ({
                value: v ? dayjs(v, "YYYY-MM-DD HH:mm") : undefined,
              })}
              normalize={(v?: Dayjs | null) => (v ? v.format("YYYY-MM-DD HH:mm") : null)}
            >
              <DatePicker
                showTime={{ format: "HH:mm", minuteStep: 5 }}
                format="YYYY-MM-DD HH:mm"
                placeholder="选择截止日期与时间"
                style={{ width: 300 }}
                disabledDate={(d) => d.isBefore(dayjs().startOf("day"))}
              />
            </Form.Item>
            <Form.Item name="scopeType" label="考生范围">
              <Radio.Group>
                <Radio value="all">全公司</Radio>
                <Radio value="departments" disabled>
                  按部门勾选（需先同步组织架构）
                </Radio>
              </Radio.Group>
            </Form.Item>
            <Form.Item name="allow_retake" label="是否允许重考" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item noStyle shouldUpdate={(p, c) => p.allow_retake !== c.allow_retake}>
              {({ getFieldValue }) =>
                getFieldValue("allow_retake") ? (
                  <Form.Item name="max_retake_count" label="最大重考次数">
                    <InputNumber min={1} max={10} style={{ width: 200 }} />
                  </Form.Item>
                ) : null
              }
            </Form.Item>
            <Form.Item name="instructions" label="补充说明（考试须知，可选）">
              <Input.TextArea rows={3} placeholder="如：请独立完成，禁止查阅资料" />
            </Form.Item>
          </Form>
          <Space>
            <Button onClick={() => setStep(1)}>上一步</Button>
            <Button type="primary" loading={createExam.isPending} onClick={handleCreate}>
              下一步：预览与发布
            </Button>
          </Space>
        </Card>
      )}

      {/* ④ 预览与发布 */}
      {step === 3 && (
        <Card title="④ 预览与发布">
          <Alert
            type="success"
            showIcon
            style={{ marginBottom: 20 }}
            message="考试已创建为「草稿」"
            description="确认信息无误后点击发布，系统将把考试状态改为「进行中」并生成二维码/链接，同时按需推送通知给考生。"
          />
          <Row gutter={24}>
            <Col span={12}>
              <Card size="small" title="考试信息总览">
                <Form form={form} component={false}>
                  <Space direction="vertical" size={6}>
                    <div>
                      <Text type="secondary">考试名称：</Text>
                      <Text strong>{form.getFieldValue("name")}</Text>
                    </div>
                    <div>
                      <Text type="secondary">时长：</Text>
                      {form.getFieldValue("duration_minutes")} 分钟
                    </div>
                    <div>
                      <Text type="secondary">截止：</Text>
                      {form.getFieldValue("deadline") || "未设置"}
                    </div>
                    <div>
                      <Text type="secondary">考生范围：</Text>
                      {form.getFieldValue("scopeType") === "all" ? "全公司" : "指定部门"}
                    </div>
                    <div>
                      <Text type="secondary">允许重考：</Text>
                      {form.getFieldValue("allow_retake")
                        ? `是（最多 ${form.getFieldValue("max_retake_count")} 次）`
                        : "否"}
                    </div>
                    <div>
                      <Text type="secondary">题型配比：</Text>
                      {RATIO_FIELDS.map((f) => `${f.label}${effectiveRatio[f.key]}`).join(" + ")}
                    </div>
                    <div>
                      <Text type="secondary">题目数：</Text>
                      {draftQuestions.length} 题
                    </div>
                  </Space>
                </Form>
              </Card>
            </Col>
            <Col span={12}>
              <Card size="small" title="试卷题目">
                <List
                  size="small"
                  dataSource={draftQuestions}
                  style={{ maxHeight: 320, overflow: "auto" }}
                  renderItem={(q, idx) => (
                    <List.Item>
                      <Space>
                        <Text type="secondary">{idx + 1}.</Text>
                        <Tag color={QUESTION_TYPE_COLOR[q.question_type]}>
                          {QUESTION_TYPE_LABEL[q.question_type]}
                        </Tag>
                        <Text style={{ fontSize: 13 }}>{q.content.slice(0, 36)}</Text>
                      </Space>
                    </List.Item>
                  )}
                />
              </Card>
            </Col>
          </Row>

          <div style={{ marginTop: 20 }}>
            <Space>
              <Button onClick={() => setStep(2)}>上一步</Button>
              {/*
                向导最后一页叫「预览与发布」，用户预期在这里就能发布。
                此前只有一个跳转按钮，跳到详情页后还要再点一次「发布考试」，
                而那时若状态不对就报 400，用户完全无从判断。
                现在直接在这里发布（考试此时已是「待发布」状态）。
              */}
              {!published && (
                <Button
                  type="primary"
                  size="large"
                  icon={<SendOutlined />}
                  loading={publish.isPending}
                  disabled={!createdExamId}
                  onClick={handlePublish}
                >
                  确认发布
                </Button>
              )}
              <Button
                size="large"
                onClick={() => navigate(`/exams/${createdExamId}`)}
                disabled={!createdExamId}
              >
                前往详情页 →
              </Button>
            </Space>
          </div>

          {published && (
            <Card size="small" title="发布成功" style={{ marginTop: 16 }}>
              <Space direction="vertical" size={8} style={{ width: "100%" }}>
                <Alert
                  type="success"
                  showIcon
                  message="考试已发布，考生可在「我的考试」看到待考任务"
                  description={`考试链接：${published.exam_link ?? "—"}`}
                />
                <Space wrap>
                  <Button
                    icon={<QrcodeOutlined />}
                    onClick={() => window.open(MOBILE_ENTRY, "_blank")}
                  >
                    打开移动答题端
                  </Button>
                  <Button onClick={() => navigate(`/exams/${published.id}`)}>查看考试详情</Button>
                  <Button type="link" onClick={() => navigate("/exams")}>
                    返回考试列表
                  </Button>
                </Space>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  移动端入口：{MOBILE_ENTRY}　|　考生扫码进入：{published.exam_link ?? "—"}
                </Text>
              </Space>
            </Card>
          )}
        </Card>
      )}
    </div>
  );
}