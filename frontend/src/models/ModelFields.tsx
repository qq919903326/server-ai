import { useEffect, useRef, useState } from "react";
import {
  Alert,
  AutoComplete,
  Button,
  Form,
  Input,
  Select,
  Space,
  Switch,
  Typography,
} from "antd";
import { api } from "../api";

export function ModelFields({ config }: { config: Record<string, any> }) {
  const form = Form.useFormInstance();
  Form.useWatch("api_key", form);
  Form.useWatch("base_url", form);
  Form.useWatch("provider", form);
  Form.useWatch("key_env", form);
  Form.useWatch("clear_key", form);
  const source = () =>
    JSON.stringify(
      ["provider", "base_url", "key_env", "api_key", "clear_key"].map(
        (k) => form.getFieldValue(k) ?? config[k] ?? "",
      ),
    );
  const [catalog, setCatalog] = useState({
    stamp: source(),
    models: (config.available_models || []) as string[],
  });
  const [loading, setLoading] = useState(false),
    [error, setError] = useState("");
  const request = useRef(0),
    pending = useRef<string | null>(null);
  const choices = catalog.stamp === source() ? catalog.models : [];
  async function load() {
    const stamp = source();
    if (pending.current === stamp) return;
    pending.current = stamp;
    const generation = ++request.current;
    setLoading(true);
    setError("");
    const values = form.getFieldsValue();
    const payload = {
      provider: values.provider ?? config.provider,
      base_url: values.base_url ?? config.base_url,
      api_key: values.api_key || "",
      key_env: values.key_env ?? config.key_env ?? "",
      clear_key: values.clear_key || false,
    };
    try {
      const result = await api(
        "/models/" + config.id + "/catalog",
        "POST",
        payload,
      );
      if (generation === request.current && stamp === source()) {
        setCatalog({ stamp, models: result.models });
        if (!form.getFieldValue("model") && result.models.length)
          form.setFieldValue("model", result.models[0]);
      }
    } catch (e) {
      if (generation === request.current && stamp === source())
        setError((e as Error).message);
    } finally {
      if (generation === request.current) setLoading(false);
      if (pending.current === stamp) pending.current = null;
    }
  }
  useEffect(() => {
    if (config.has_key && !config.available_models?.length) void load();
  }, []);
  return (
    <>
      <Form.Item name="provider" label="服务商">
        <Select
          options={[
            { value: "deepseek", label: "DeepSeek" },
            { value: "mimo", label: "小米 MiMo" },
            { value: "openai", label: "OpenAI" },
            { value: "custom", label: "兼容接口" },
          ]}
        />
      </Form.Item>
      <Form.Item name="base_url" label="API 地址" rules={[{ required: true }]}>
        <Input placeholder="https://api.deepseek.com" />
      </Form.Item>
      <Form.Item
        name="api_key"
        label="API Key"
        extra="输入后离开输入框会自动获取可用模型。留空保留已保存的密钥。"
      >
        <Input.Password
          autoComplete="new-password"
          placeholder={config.has_key ? "已配置，留空保留" : "输入 API Key"}
          onBlur={() => {
            if (form.getFieldValue("api_key")) void load();
          }}
        />
      </Form.Item>
      <Form.Item
        name="key_env"
        label="密钥环境变量名"
        extra="也可通过服务端环境变量提供 Key。"
      >
        <Input
          placeholder="例如 DEEPSEEK_API_KEY"
          onBlur={() => {
            if (form.getFieldValue("key_env")) void load();
          }}
        />
      </Form.Item>
      <Form.Item
        name="clear_key"
        label="清除已保存密钥"
        valuePropName="checked"
      >
        <Switch />
      </Form.Item>
      <Space style={{ marginBottom: 14 }}>
        <Button loading={loading} onClick={() => void load()}>
          获取可用模型
        </Button>
        <Typography.Text type="secondary">
          {choices.length
            ? `已获取 ${choices.length} 个模型`
            : "尚未获取模型列表"}
        </Typography.Text>
      </Space>
      {error && (
        <Alert
          type="warning"
          showIcon
          message={error}
          style={{ marginBottom: 14 }}
        />
      )}
      <Form.Item
        name="model"
        label="模型名称"
        rules={[{ required: true, message: "请选择或填写模型 ID" }]}
        extra="可从厂商返回的列表中选择，也可手动填写模型 ID。"
      >
        <AutoComplete
          aria-label="模型名称"
          options={choices.map((value) => ({ value }))}
          filterOption={(value, option) =>
            String(option?.value).toLowerCase().includes(value.toLowerCase())
          }
          placeholder="获取列表后选择模型，或手动填写"
          allowClear
        />
      </Form.Item>
      <p className="helper">
        厂商列表可能包含语音、嵌入等模型。列表可见不保证支持当前聊天或工具功能，请按模型能力选择。
      </p>
    </>
  );
}
