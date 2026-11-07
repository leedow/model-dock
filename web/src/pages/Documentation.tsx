import { useState } from "react";
import { Alert, Button, Card, Form, Input, Space, Tabs, Tag } from "antd";
import {
  ApiOutlined,
  ArrowRightOutlined,
  BookOutlined,
  LockOutlined,
} from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { api, json } from "../api";
import { CodeBlock, Copy, PageHeading } from "../components";
import { useAction, useAuth } from "../hooks";

export function DocsPage() {
  const { t } = useTranslation();
  const origin = window.location.origin;
  const [tab, setTab] = useState("python");
  const examples: Record<string, string> = {
    python: `from openai import OpenAI\nimport os\n\nclient = OpenAI(\n    base_url="${origin}/v1",\n    api_key=os.environ["MODELDock_API_KEY"],\n)\n\nstream = client.chat.completions.create(\n    model="mock-chat",\n    messages=[{"role": "user", "content": "Hello, ModelDock!"}],\n    stream=True,\n)\nfor chunk in stream:\n    if chunk.choices:\n        print(chunk.choices[0].delta.content or "", end="", flush=True)`,
    curl: `curl ${origin}/v1/chat/completions \\\n  -H "Authorization: Bearer $MODELDock_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{\n    "model": "mock-chat",\n    "messages": [{"role":"user","content":"Hello!"}],\n    "stream": true\n  }'`,
    async: `import os, time, httpx\n\nclient = httpx.Client(\n    base_url="${origin}",\n    headers={"Authorization": "Bearer " + os.environ["MODELDock_API_KEY"]},\n    timeout=120,\n)\nresponse = client.post("/v1/tasks", json={\n    "model": "mock-image", "capability": "image",\n    "parameters": {"prompt": "a blue planet"},\n})\nresponse.raise_for_status()\ntask = response.json()\nwhile task["status"] in ("queued", "running"):\n    time.sleep(1)\n    response = client.get("/v1/tasks/" + task["id"])\n    response.raise_for_status()\n    task = response.json()\nif task["status"] != "succeeded":\n    raise RuntimeError(task["error"])\nfile = task["result"]["files"][0]\nimage = client.get(file["url"])\nimage.raise_for_status()\nopen("result.png", "wb").write(image.content)`,
  };
  const endpoints = [
    ["GET", "/v1/models", "models"],
    ["POST", "/v1/chat/completions", "chat"],
    ["POST", "/v1/images/generations", "image"],
    ["POST", "/v1/audio/speech", "tts"],
    ["POST", "/v1/audio/transcriptions", "asr"],
    ["POST", "/v1/tasks", "tasks"],
    ["GET", "/v1/tasks/{id}", "status"],
    ["POST", "/v1/files", "upload"],
    ["GET", "/v1/files/{id}/content", "download"],
  ];
  return (
    <>
      <PageHeading
        title={t("docsTitle")}
        description={t("docsSub")}
        extra={
          <a href="/docs" target="_blank" rel="noreferrer">
            <Button icon={<BookOutlined aria-hidden="true" />}>
              {t("openAPI")} ↗
            </Button>
          </a>
        }
      />
      <div className="docs-grid">
        <div>
          <Card className="docs-start">
            <div className="docs-step">
              <span>01</span>
              <div>
                <h3>{t("createKey")}</h3>
                <p>{t("grantHint")}</p>
                <Link to="/keys">
                  {t("keys")} <ArrowRightOutlined aria-hidden="true" />
                </Link>
              </div>
            </div>
            <div className="docs-step">
              <span>02</span>
              <div>
                <h3>{t("baseURL")}</h3>
                <div className="base-url">
                  <code>{origin}/v1</code>
                  <Copy text={origin + "/v1"} />
                </div>
              </div>
            </div>
          </Card>
          <Card
            title={
              <Space>
                <ApiOutlined aria-hidden="true" />
                API endpoints
              </Space>
            }
            className="endpoints"
          >
            {endpoints.map(([method, path, label]) => (
              <div className="endpoint-row" key={path}>
                <Tag color={method === "GET" ? "green" : "blue"}>{method}</Tag>
                <code>{path}</code>
                <span>{t(label)}</span>
              </div>
            ))}
          </Card>
        </div>
        <Card className="docs-code">
          <Tabs
            activeKey={tab}
            onChange={setTab}
            items={[
              { key: "python", label: "Python SDK" },
              { key: "curl", label: "cURL" },
              { key: "async", label: t("async") },
            ]}
          />
          <CodeBlock
            code={examples[tab]}
            language={tab === "curl" ? "bash" : "python"}
          />
          <Alert
            showIcon
            type="info"
            message={t("compatibility")}
            description={t("compatibilityText")}
            style={{ marginTop: 20 }}
          />
          <div className="docs-note">
            <LockOutlined aria-hidden="true" />
            <span>{t("fileAuth")}</span>
          </div>
        </Card>
      </div>
      <Card title="Adapter SDK" style={{ marginTop: 24 }}>
        <p className="muted">{t("helpText")}</p>
        <CodeBlock
          language="python"
          code={
            'from modeldock.adapters import REGISTRY\nfrom modeldock.adapters.base import BaseAdapter, InferenceResult\n\nclass CustomChatAdapter(BaseAdapter):\n    capability = "chat"\n\n    async def load(self):\n        # Load a local engine or initialize a remote client.\n        self.loaded = True\n\n    async def infer(self, payload):\n        # Translate the protocol and return typed results.\n        raise NotImplementedError("Connect your inference engine")\n\nREGISTRY["custom.chat"] = CustomChatAdapter'
          }
        />
      </Card>
    </>
  );
}
export function SettingsPage() {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  const [form] = Form.useForm();
  const change = useAction(
    (values: unknown) => api("/api/v1/auth/password", json("POST", values)),
    () => void logout().catch(() => window.location.reload()),
  );
  return (
    <>
      <PageHeading title={t("settings")} />
      <div className="settings-grid">
        <Card title={t("username")}>
          <div className="profile-card">
            <div className="profile-monogram">
              {user?.username.slice(0, 1).toUpperCase()}
            </div>
            <h2>{user?.username}</h2>
            <Tag color="purple">{t(user?.role || "user")}</Tag>
          </div>
        </Card>
        <Card title={t("changePassword")}>
          <Alert
            type="info"
            showIcon
            message={t("changePasswordHint")}
            style={{ marginBottom: 24 }}
          />
          <Form
            layout="vertical"
            form={form}
            onFinish={(values) => change.mutate(values)}
          >
            <Form.Item
              name="current_password"
              label={t("currentPassword")}
              rules={[{ required: true, message: t("required") }]}
            >
              <Input.Password autoComplete="current-password" />
            </Form.Item>
            <Form.Item
              name="new_password"
              label={t("newPassword")}
              rules={[
                { required: true, message: t("required") },
                { min: 12, message: t("passwordHint") },
              ]}
            >
              <Input.Password autoComplete="new-password" />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={change.isPending}>
              {t("changePassword")}
            </Button>
          </Form>
        </Card>
      </div>
    </>
  );
}
