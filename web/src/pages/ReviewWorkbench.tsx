import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Empty,
  Input,
  Progress,
  Radio,
  Row,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from "antd";
import { CheckOutlined, CloseOutlined, EditOutlined, LeftOutlined, RightOutlined } from "@ant-design/icons";

import {
  useDocumentStructure,
  usePendingQuestions,
  useQuestion,
  useReviewQuestion,
  useUpdateQuestion,
} from "../api/hooks";
import {
  QUESTION_TYPE_COLOR,
  QUESTION_TYPE_LABEL,
  parseJsonArray,
  parseStructuredContent,
  type Question,
} from "../api/types";

const { Title, Text, Paragraph } = Typography;

/** 从选项文本中提取字母前缀（"A. xxx" → "A"）。 */
function optionLetter(option: string): string {
  const m = option.match(/^\s*([A-Za-z])[\.、\)]\s*/);
  return m ? m[1].toUpperCase() : option.trim().slice(0, 1).toUpperCase();
}

export default function ReviewWorkbench() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  // 待审核队列：仅用于「上一题 / 下一题」导航与进度条
  const { data: pending, isLoading: listLoading } = usePendingQuestions({
    page: 1,
    page_size: 200,
  });
  // 当前题目：直接按 ID 拉取，避免依赖待审核队列能一次装下全部题目
  // （此前 page_size 超限报 422、或题目已被处理时，工作台会整页打不开）
  const {
    data: fetchedQuestion,
    isLoading: questionLoading,
    isError: questionError,
  } = useQuestion(id);

  const review = useReviewQuestion();
  const updateQuestion = useUpdateQuestion();

  const questions: Question[] = pending?.items ?? [];
  const currentIndex = useMemo(() => questions.findIndex((q) => q.id === id), [questions, id]);
  const current: Question | undefined = fetchedQuestion ?? undefined;

  // 从当前题目反查来源制度，用于右侧原文对照
  const docQuery = useDocumentStructure(current?.document_id ?? undefined);
  const doc = docQuery.data;

  const [editing, setEditing] = useState(false);
  const [draftContent, setDraftContent] = useState("");
  const [draftOptions, setDraftOptions] = useState<string[]>([]);
  const [draftAnswer, setDraftAnswer] = useState<string[]>([]);

  // 切换题目时重置编辑态
  useEffect(() => {
    if (!current) return;
    setEditing(false);
    setDraftContent(current.content);
    setDraftOptions(parseJsonArray(current.options));
    setDraftAnswer(parseJsonArray(current.answer));
  }, [current?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // 来源条款原文
  const sourceClause = useMemo(() => {
    const content = docQuery.structured ?? parseStructuredContent(doc?.structured_content ?? null);
    if (!content || !current) return null;
    for (const ch of content.chapters) {
      for (const cl of ch.clauses) {
        if (cl.clause_no === current.clause_no) return cl;
      }
    }
    return null;
  }, [docQuery.structured, doc?.structured_content, current]);

  const goPrev = () => {
    if (currentIndex > 0) navigate(`/review/${questions[currentIndex - 1].id}`);
  };
  const goNext = () => {
    if (currentIndex < questions.length - 1) navigate(`/review/${questions[currentIndex + 1].id}`);
  };

  const doApprove = async () => {
    if (!current) return;
    try {
      if (editing) {
        await review.mutateAsync({
          questionId: current.id,
          action: "edit_approve",
          content: draftContent,
          options: JSON.stringify(draftOptions),
          answer: JSON.stringify(draftAnswer),
        });
        message.success("已保存修改并通过");
      } else {
        await review.mutateAsync({ questionId: current.id, action: "approve" });
        message.success("已通过");
      }
      goNext();
    } catch (err) {
      message.error(`操作失败：${(err as Error).message}`);
    }
  };

  const doReject = async () => {
    if (!current) return;
    try {
      await review.mutateAsync({
        questionId: current.id,
        action: "reject",
        reject_reason: "专家审核弃用",
      });
      message.success("已弃用");
      goNext();
    } catch (err) {
      message.error(`操作失败：${(err as Error).message}`);
    }
  };

  const doSaveOnly = async () => {
    if (!current) return;
    try {
      await updateQuestion.mutateAsync({
        questionId: current.id,
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

  // 快捷键：← → 切题，Enter 通过，E 编辑，D 弃用（spec M3 要求）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      const typing =
        target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.isContentEditable;
      if (typing) return;

      if (e.key === "ArrowLeft") {
        e.preventDefault();
        goPrev();
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        goNext();
      } else if (e.key === "Enter") {
        e.preventDefault();
        void doApprove();
      } else if (e.key.toLowerCase() === "e") {
        e.preventDefault();
        setEditing((v) => !v);
      } else if (e.key.toLowerCase() === "d") {
        e.preventDefault();
        void doReject();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }); // 每次渲染重新绑定，保证闭包里的 current 是最新的

  if (questionLoading) {
    return <Spin style={{ display: "block", margin: "120px auto" }} size="large" />;
  }

  if (questionError || !current) {
    return (
      <Card>
        <Empty description="题目不存在或已被删除">
          <Button type="primary" onClick={() => navigate("/review")}>
            返回审核列表
          </Button>
        </Empty>
      </Card>
    );
  }

  // 待审核队列为空时，仅提示导航不可用，不影响当前题目的审核操作
  const queueEmpty = !listLoading && questions.length === 0;

  const options = editing ? draftOptions : parseJsonArray(current.options);
  const answer = editing ? draftAnswer : parseJsonArray(current.answer);
  const isMulti = current.question_type === "multi_choice";
  const isTrueFalse = current.question_type === "true_false";
  const isFill = current.question_type === "fill_blank";

  const toggleDraftAnswer = (letter: string) => {
    setDraftAnswer((prev) =>
      prev.includes(letter) ? prev.filter((x) => x !== letter) : [...prev, letter],
    );
  };

  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 12,
        }}
      >
        <Space align="baseline">
          <Title level={3} style={{ margin: 0 }}>
            审核工作台
          </Title>
          <Text type="secondary">{doc?.name ?? ""}</Text>
        </Space>
        <div style={{ width: 280 }}>
          <Progress
            percent={
              questions.length > 0 && currentIndex >= 0
                ? Math.round(((currentIndex + 1) / questions.length) * 100)
                : 0
            }
            format={() =>
              questions.length > 0 && currentIndex >= 0
                ? `${currentIndex + 1} / ${questions.length}`
                : "—"
            }
          />
        </div>
      </div>

      <Row gutter={16}>
        {/* 左侧 60%：当前题目 */}
        <Col span={14}>
          <Card
            title={
              <Space wrap>
                <Tag color={QUESTION_TYPE_COLOR[current.question_type]}>
                  {QUESTION_TYPE_LABEL[current.question_type]}
                </Tag>
                <Text type="secondary">
                  来源：{current.chapter_no ?? ""} {current.clause_no ?? ""}
                </Text>
                {!editing && (
                  <Button size="small" icon={<EditOutlined />} onClick={() => setEditing(true)}>
                    编辑（E）
                  </Button>
                )}
              </Space>
            }
            styles={{ body: { minHeight: 380 } }}
          >
            {/*
              M2-3 AI 质量自查提示。
              口径（已与产品负责人确认）：以五个校验项为准、严格优先 ——
              任一分项未通过，或 AI 分项与总评自相矛盾，都会判为不通过。
              这条提示存在的唯一目的是辅助人工审核，宁可多提醒也不静默放行。
            */}
            {current.ai_quality_pass === false && (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 16 }}
                message="[注意] AI 自查未通过，请重点复核"
                description={
                  <Text style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>
                    {current.ai_quality_note ?? "AI 质量校验判定该题存在问题"}
                  </Text>
                }
              />
            )}
            {current.ai_quality_pass === true && current.ai_quality_note && (
              <Alert
                type="success"
                showIcon
                style={{ marginBottom: 16 }}
                message="AI 自查通过"
                description={
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {current.ai_quality_note}
                  </Text>
                }
              />
            )}

            {editing ? (
              <Input.TextArea
                rows={3}
                value={draftContent}
                onChange={(e) => setDraftContent(e.target.value)}
                placeholder="题干"
              />
            ) : (
              <Paragraph strong style={{ fontSize: 16, whiteSpace: "pre-wrap" }}>
                {current.content}
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
                    const selected = editing
                      ? draftAnswer.includes(label)
                      : answer.includes(label);
                    return (
                      <Button
                        key={label}
                        type={selected ? "primary" : "default"}
                        size="large"
                        onClick={() => editing && setDraftAnswer([label])}
                        disabled={!editing}
                      >
                        {label === "正确" ? "✓ 正确" : "✗ 错误"}
                        {selected && !editing ? "（正确答案）" : ""}
                      </Button>
                    );
                  })}
                </Space>
              ) : isMulti ? (
                <Checkbox.Group
                  value={editing ? draftAnswer : answer}
                  onChange={(vals) => {
                    if (!editing) return;
                    setDraftAnswer(vals as string[]);
                  }}
                  disabled={!editing}
                >
                  <Space direction="vertical" style={{ width: "100%" }}>
                    {options.map((opt, i) => {
                      const letter = optionLetter(opt);
                      const correct = answer.includes(letter);
                      const editable = editing && (
                        <Checkbox value={letter}>{opt}</Checkbox>
                      );
                      return (
                        <div
                          key={i}
                          style={{
                            padding: "8px 12px",
                            borderRadius: 6,
                            background: correct ? "#f6ffed" : "transparent",
                            border: correct ? "1px solid #b7eb8f" : "1px solid transparent",
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
                                <Checkbox
                                  checked={draftAnswer.includes(letter)}
                                  onChange={() => toggleDraftAnswer(letter)}
                                />
                              }
                            />
                          ) : (
                            <Text>
                              {opt}
                              {correct && <Tag color="green" style={{ marginLeft: 8 }}>正确答案</Tag>}
                            </Text>
                          )}
                        </div>
                      );
                    })}
                  </Space>
                </Checkbox.Group>
              ) : (
                <Radio.Group
                  value={answer[0]}
                  onChange={(e) => editing && setDraftAnswer([e.target.value])}
                  disabled={!editing}
                >
                  <Space direction="vertical" style={{ width: "100%" }}>
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
                            border: correct ? "1px solid #b7eb8f" : "1px solid transparent",
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
                                <Radio
                                  checked={draftAnswer[0] === letter}
                                  onChange={() => setDraftAnswer([letter])}
                                />
                              }
                            />
                          ) : (
                            <Text>
                              {opt}
                              {correct && <Tag color="green" style={{ marginLeft: 8 }}>正确答案</Tag>}
                            </Text>
                          )}
                        </div>
                      );
                    })}
                  </Space>
                </Radio.Group>
              )}
            </div>

            {editing && (
              <div style={{ marginTop: 16 }}>
                <Space>
                  <Button type="primary" onClick={doSaveOnly} loading={updateQuestion.isPending}>
                    仅保存
                  </Button>
                  <Button onClick={() => setEditing(false)}>取消编辑</Button>
                </Space>
              </div>
            )}

            {current.ai_raw_content && current.ai_raw_content !== current.content && (
              <Alert
                type="info"
                showIcon
                style={{ marginTop: 16 }}
                message="该题已被专家修改过"
                description={
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    AI 原始版本：{current.ai_raw_content.slice(0, 80)}…
                  </Text>
                }
              />
            )}
          </Card>
        </Col>

        {/* 右侧 40%：制度原文对照 */}
        <Col span={10}>
          <Card
            title="制度原文对照"
            styles={{ body: { minHeight: 380, maxHeight: 560, overflow: "auto" } }}
          >
            {sourceClause ? (
              <>
                <Text type="secondary">
                  《{doc?.name ?? "制度"}》{current.chapter_no ?? ""} {current.clause_no ?? ""}
                </Text>
                <Paragraph
                  style={{ marginTop: 12, whiteSpace: "pre-wrap", fontSize: 15, lineHeight: 1.8 }}
                >
                  {sourceClause.text}
                </Paragraph>
              </>
            ) : (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="未找到该题的来源条款原文"
              />
            )}
          </Card>
        </Col>
      </Row>

      {/* 底部操作栏 */}
      <Card style={{ marginTop: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <Space>
            <Button
              icon={<LeftOutlined />}
              onClick={goPrev}
              disabled={queueEmpty || currentIndex <= 0}
            >
              上一题
            </Button>
            <Button
              icon={<RightOutlined />}
              onClick={goNext}
              disabled={queueEmpty || currentIndex < 0 || currentIndex >= questions.length - 1}
            >
              下一题
            </Button>
          </Space>

          <Space>
            <Button
              type="primary"
              size="large"
              icon={<CheckOutlined />}
              loading={review.isPending}
              onClick={doApprove}
            >
              {editing ? "保存修改并通过" : "通过"}
            </Button>
            <Button size="large" danger icon={<CloseOutlined />} onClick={doReject}>
              弃用
            </Button>
          </Space>

          <Text type="secondary" style={{ fontSize: 12 }}>
            快捷键：← → 切题　Enter 通过　E 编辑　D 弃用
          </Text>
        </div>
      </Card>
    </div>
  );
}