import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Descriptions,
  Empty,
  Input,
  Radio,
  Row,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { EditOutlined, LeftOutlined, SaveOutlined } from "@ant-design/icons";
import type { ColumnsType } from "antd/es/table";

import {
  useDocumentStructure,
  useQuestion,
  useToggleQuestionActive,
  useUpdateQuestion,
} from "../api/hooks";
import {
  QUESTION_STATUS_COLOR,
  QUESTION_STATUS_LABEL,
  QUESTION_TYPE_COLOR,
  QUESTION_TYPE_LABEL,
  parseJsonArray,
  parseStructuredContent,
  type Question,
} from "../api/types";

const { Title, Text, Paragraph } = Typography;

function optionLetter(option: string): string {
  const m = option.match(/^\s*([A-Za-z])[\.、\)]\s*/);
  return m ? m[1].toUpperCase() : option.trim().slice(0, 1).toUpperCase();
}

export default function QuestionDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const { data: question, isLoading, isError } = useQuestion(id);
  const docQuery = useDocumentStructure(question?.document_id ?? undefined);
  const doc = docQuery.data;

  const updateQuestion = useUpdateQuestion();
  const toggleActive = useToggleQuestionActive();

  const [editing, setEditing] = useState(false);
  const [draftContent, setDraftContent] = useState("");
  const [draftOptions, setDraftOptions] = useState<string[]>([]);
  const [draftAnswer, setDraftAnswer] = useState<string[]>([]);

  useEffect(() => {
    if (!question) return;
    setEditing(false);
    setDraftContent(question.content);
    setDraftOptions(parseJsonArray(question.options));
    setDraftAnswer(parseJsonArray(question.answer));
  }, [question?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // 来源条款原文（spec 要求题目详情可回看来源）
  const sourceClause = useMemo(() => {
    const content =
      docQuery.structured ?? parseStructuredContent(doc?.structured_content ?? null);
    if (!content || !question) return null;
    for (const ch of content.chapters) {
      for (const cl of ch.clauses) {
        if (cl.clause_no === question.clause_no) return cl;
      }
    }
    return null;
  }, [docQuery.structured, doc?.structured_content, question]);

  if (isLoading) return <Spin style={{ display: "block", margin: "120px auto" }} size="large" />;

  if (isError || !question) {
    return (
      <Card>
        <Empty description="题目不存在或已被删除">
          <Button type="primary" onClick={() => navigate("/questions")}>
            返回题库
          </Button>
        </Empty>
      </Card>
    );
  }

  const options = editing ? draftOptions : parseJsonArray(question.options);
  const answer = editing ? draftAnswer : parseJsonArray(question.answer);
  const isFill = question.question_type === "fill_blank";
  const isTrueFalse = question.question_type === "true_false";
  const isMulti = question.question_type === "multi_choice";
  const modified = question.ai_raw_content && question.ai_raw_content !== question.content;

  const handleSave = async () => {
    try {
      await updateQuestion.mutateAsync({
        questionId: question.id,
        content: draftContent,
        options: JSON.stringify(draftOptions),
        answer: JSON.stringify(draftAnswer),
      });
      message.success("已保存");
      setEditing(false);
    } catch (err) {
      message.error(`保存失败：${(err as Error).message}`);
    }
  };

  const usageColumns: ColumnsType<{ exam_name: string; error_rate: string; date: string }> = [
    { title: "考试名称", dataIndex: "exam_name", key: "exam_name" },
    { title: "错误率", dataIndex: "error_rate", key: "error_rate" },
    { title: "日期", dataIndex: "date", key: "date" },
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
          <Button icon={<LeftOutlined />} onClick={() => navigate("/questions")}>
            返回题库
          </Button>
          <Title level={3} style={{ margin: 0 }}>
            题目详情
          </Title>
        </Space>
        <Space>
          {!editing && (
            <Button icon={<EditOutlined />} onClick={() => setEditing(true)}>
              编辑
            </Button>
          )}
          <Button
            color={question.is_active ? "default" : "primary"}
            onClick={async () => {
              await toggleActive.mutateAsync({
                questionId: question.id,
                is_active: !question.is_active,
              });
              message.success(question.is_active ? "已下架" : "已重新激活");
            }}
          >
            {question.is_active ? "下架" : "重新激活"}
          </Button>
        </Space>
      </div>

      {modified && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="该题已被专家修改过"
          description={
            <Text type="secondary" style={{ fontSize: 12 }}>
              AI 原始版本：{question.ai_raw_content}
            </Text>
          }
        />
      )}

      <Row gutter={16}>
        <Col span={16}>
          <Card
            title={
              <Space>
                <Tag color={QUESTION_TYPE_COLOR[question.question_type]}>
                  {QUESTION_TYPE_LABEL[question.question_type]}
                </Tag>
                <Tag color={QUESTION_STATUS_COLOR[question.status]}>
                  {QUESTION_STATUS_LABEL[question.status]}
                </Tag>
                <Text type="secondary">
                  {question.chapter_no ?? ""} {question.clause_no ?? ""}
                </Text>
              </Space>
            }
          >
            {editing ? (
              <Input.TextArea
                rows={3}
                value={draftContent}
                onChange={(e) => setDraftContent(e.target.value)}
              />
            ) : (
              <Paragraph strong style={{ fontSize: 16, whiteSpace: "pre-wrap" }}>
                {question.content}
              </Paragraph>
            )}

            <div style={{ marginTop: 20 }}>
              {isFill ? (
                editing ? (
                  <Input
                    value={draftAnswer[0] ?? ""}
                    onChange={(e) => setDraftAnswer([e.target.value])}
                    placeholder="填空答案"
                  />
                ) : (
                  <Text>
                    答案：<Text strong>{answer.join("、")}</Text>
                  </Text>
                )
              ) : isTrueFalse ? (
                <Space>
                  {["正确", "错误"].map((label) => {
                    const selected = editing ? draftAnswer.includes(label) : answer.includes(label);
                    return (
                      <Button
                        key={label}
                        type={selected ? "primary" : "default"}
                        size="large"
                        disabled={!editing}
                        onClick={() => editing && setDraftAnswer([label])}
                      >
                        {label}
                        {selected ? "（正确答案）" : ""}
                      </Button>
                    );
                  })}
                </Space>
              ) : (
                <>
                  {isMulti ? (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      多选题（可勾选多个正确答案）
                    </Text>
                  ) : null}
                  <Space direction="vertical" style={{ width: "100%", marginTop: 8 }}>
                    {options.map((opt, i) => {
                      const letter = optionLetter(opt);
                      const correct = answer.includes(letter);
                      return (
                        <div
                          key={i}
                          style={{
                            padding: "8px 12px",
                            borderRadius: 6,
                            background: correct ? "#f6ffed" : "transparent",
                            border: correct ? "1px solid #b7eb8f" : "1px solid #f0f0f0",
                          }}
                        >
                          {editing ? (
                            <Input
                              value={draftOptions[i]}
                              onChange={(e) => {
                                const next = [...draftOptions];
                                next[i] = e.target.value;
                                setDraftOptions(next);
                              }}
                              addonBefore={
                                isMulti ? (
                                  <Checkbox
                                    checked={draftAnswer.includes(letter)}
                                    onChange={() =>
                                      setDraftAnswer((prev) =>
                                        prev.includes(letter)
                                          ? prev.filter((x) => x !== letter)
                                          : [...prev, letter],
                                      )
                                    }
                                  />
                                ) : (
                                  <Radio
                                    checked={draftAnswer[0] === letter}
                                    onChange={() => setDraftAnswer([letter])}
                                  />
                                )
                              }
                            />
                          ) : (
                            <Text>
                              {opt}
                              {correct && (
                                <Tag color="green" style={{ marginLeft: 8 }}>
                                  正确答案
                                </Tag>
                              )}
                            </Text>
                          )}
                        </div>
                      );
                    })}
                  </Space>
                </>
              )}
            </div>

            {editing && (
              <div style={{ marginTop: 16 }}>
                <Space>
                  <Button
                    type="primary"
                    icon={<SaveOutlined />}
                    loading={updateQuestion.isPending}
                    onClick={handleSave}
                  >
                    保存修改
                  </Button>
                  <Button onClick={() => setEditing(false)}>取消</Button>
                </Space>
              </div>
            )}
          </Card>
        </Col>

        <Col span={8}>
          <Card title="题目信息" size="small" style={{ marginBottom: 16 }}>
            <Descriptions column={1} size="small">
              <Descriptions.Item label="来源制度">{doc?.name ?? "—"}</Descriptions.Item>
              <Descriptions.Item label="来源章节">
                {`${question.chapter_no ?? ""} ${question.clause_no ?? ""}`.trim() || "—"}
              </Descriptions.Item>
              <Descriptions.Item label="分值">{question.score}</Descriptions.Item>
              <Descriptions.Item label="使用次数">{question.usage_count}</Descriptions.Item>
              <Descriptions.Item label="错误率">
                {question.error_rate === null || question.error_rate === undefined
                  ? "—"
                  : `${question.error_rate}%`}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                {question.is_active ? "已上架" : "已下架"}
              </Descriptions.Item>
              <Descriptions.Item label="创建时间">
                {String(question.created_at).replace("T", " ").slice(0, 19)}
              </Descriptions.Item>
              {question.reject_reason && (
                <Descriptions.Item label="弃用原因">{question.reject_reason}</Descriptions.Item>
              )}
            </Descriptions>
          </Card>

          <Card title="制度原文对照" size="small">
            {sourceClause ? (
              <>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {sourceClause.clause_no}
                </Text>
                <Paragraph style={{ marginTop: 8, whiteSpace: "pre-wrap", lineHeight: 1.8 }}>
                  {sourceClause.text}
                </Paragraph>
              </>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未找到来源条款" />
            )}
          </Card>
        </Col>
      </Row>

      <Card title="考试使用记录" style={{ marginTop: 16 }}>
        <Table<{ exam_name: string; error_rate: string; date: string }>
          rowKey="exam_name"
          size="small"
          columns={usageColumns}
          dataSource={[]}
          pagination={false}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="该题尚未在任何考试中使用"
              />
            ),
          }}
        />
      </Card>
    </div>
  );
}