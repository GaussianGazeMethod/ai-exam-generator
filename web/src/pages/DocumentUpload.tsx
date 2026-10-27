import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, Button, Card, Form, Input, Progress, Space, Typography, Upload, message } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import type { UploadFile } from "antd";

import { useUploadDocument } from "../api/hooks";

const { Title, Text } = Typography;
const { Dragger } = Upload;

interface FormValues {
  name: string;
  version?: string;
  remark?: string;
}

export default function DocumentUpload() {
  const navigate = useNavigate();
  const [form] = Form.useForm<FormValues>();
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState("");

  const uploadDoc = useUploadDocument();

  const handleSubmit = async (values: FormValues) => {
    const file = fileList[0]?.originFileObj;
    if (!file) {
      message.warning("请先选择制度文档");
      return;
    }

    // 上传 → 解析 → 建索引在同一个请求内完成，用阶段提示代替真实进度
    setProgress(15);
    setStage("正在上传文件…");
    const timer1 = window.setTimeout(() => {
      setProgress(45);
      setStage("正在解析文档结构（识别章/节/条款）…");
    }, 800);
    const timer2 = window.setTimeout(() => {
      setProgress(75);
      setStage("正在建立条款向量索引…");
    }, 2600);

    try {
      const doc = await uploadDoc.mutateAsync({
        file,
        name: values.name,
        version: values.version,
        remark: values.remark,
      });
      window.clearTimeout(timer1);
      window.clearTimeout(timer2);
      setProgress(100);
      setStage("完成");
      message.success(`上传并解析成功：${doc.name}`);
      navigate(`/documents/${doc.id}`);
    } catch (err) {
      window.clearTimeout(timer1);
      window.clearTimeout(timer2);
      setProgress(0);
      setStage("");
      message.error(`上传失败：${(err as Error).message}`);
    }
  };

  const busy = uploadDoc.isPending;

  return (
    <div>
      <Title level={3}>上传制度文档</Title>
      <Card style={{ maxWidth: 860 }}>
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 20 }}
          message="上传后系统会自动完成三件事"
          description="① 解析文档结构（识别章 / 节 / 条款层级树）　② 把条款写入向量库供出题时检索　③ 之后可在详情页一键触发 AI 出题"
        />

        <Form form={form} layout="vertical" onFinish={handleSubmit} disabled={busy}>
          <Form.Item label="制度文档" required>
            <Dragger
              accept=".doc,.docx,.pdf"
              maxCount={1}
              fileList={fileList}
              beforeUpload={() => false} // 阻止自动上传，由表单统一提交
              onChange={({ fileList: fl }) => setFileList(fl.slice(-1))}
            >
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">点击或拖拽 Word / PDF 文件到此区域</p>
              <p className="ant-upload-hint" style={{ color: "#999" }}>
                支持 .doc / .docx / .pdf，单个文件不超过 50MB
              </p>
            </Dragger>
          </Form.Item>

          <Form.Item
            name="name"
            label="制度名称"
            rules={[{ required: true, message: "请填写制度名称" }]}
          >
            <Input placeholder="如：数据安全管理规范" />
          </Form.Item>

          <Form.Item name="version" label="版本号" initialValue="1.0">
            <Input placeholder="1.0" style={{ width: 200 }} />
          </Form.Item>

          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={3} placeholder="可选，例如本次修订要点" />
          </Form.Item>

          {busy && (
            <div style={{ marginBottom: 16 }}>
              <Progress percent={progress} status="active" />
              <Text type="secondary">{stage}</Text>
            </div>
          )}

          <Form.Item style={{ marginBottom: 0 }}>
            <Space>
              <Button type="primary" htmlType="submit" loading={busy} size="large">
                上传并解析
              </Button>
              <Button onClick={() => navigate("/documents")} disabled={busy}>
                返回列表
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}