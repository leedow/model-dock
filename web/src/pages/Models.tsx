import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useSearchParams } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
} from "antd";
import {
  EditOutlined,
  PlusOutlined,
  PoweroffOutlined,
  ReloadOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import { api, formatDate, json } from "../api";
import { CapabilityIcon, PageHeading, QueryError, Status } from "../components";
import { useAction, useAuth, useRows } from "../hooks";
import type { Model, ProviderConnection } from "../types";
import { ModelDownloads } from "./ModelDownloads";

export function ModelsPage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const admin = user?.role === "admin";
  const connections = useQuery<{ data: ProviderConnection[] }>({
    queryKey: ["/api/v1/provider-connections"],
    queryFn: () => api("/api/v1/provider-connections"),
    enabled: admin,
  });
  const [searchParams, setSearchParams] = useSearchParams();
  const query = useRows<Model>("/api/v1/models", {}, 2500);
  const adapters = useRows<{
    name: string;
    capability: string | null;
    implemented: boolean;
    remote: boolean;
    local_speech: boolean;
    config_schema: Record<string, unknown>;
    config_example: Record<string, unknown>;
  }>("/api/v1/adapters", {}, 0);
  const [search, setSearch] = useState(""),
    [capability, setCapability] = useState(""),
    [open, setOpen] = useState(false),
    [editing, setEditing] = useState<Model | null>(null),
    [detail, setDetail] = useState<Model | null>(null);
  const [form] = Form.useForm();
  const selectedConnection = Form.useWatch("provider_connection_id", form);
  const selectedCap = Form.useWatch("capability", form) || "chat";
  const selectedAdapter = Form.useWatch("adapter", form) || "mock.chat";
  const [formError, setFormError] = useState("");
  const showForm = (model: Model | null) => {
    setEditing(model);
    setFormError("");
    form.resetFields();
    form.setFieldsValue(
      model
        ? {
            ...model,
            upstream_model: model.config.model,
            token_parameter: model.config.token_parameter || "auto",
            config: JSON.stringify(model.config, null, 2),
          }
        : {
            name: "",
            provider_connection_id: null,
            token_parameter: "auto",
            capability: "chat",
            adapter: "mock.chat",
            enabled: true,
            device: "virtual:0",
            memory_mb: 1024,
            concurrency: 1,
            timeout_seconds: 120,
            idle_seconds: 600,
            config:
              '{\n  "delay_ms": 80,\n  "load_delay_ms": 150,\n  "fail": false\n}',
          },
    );
    setOpen(true);
  };
  useEffect(() => {
    if (searchParams.has("create") && admin) {
      showForm(null);
      setSearchParams({}, { replace: true });
    }
  }, [searchParams, admin]);
  const save = useAction(
    async (values: any) => {
      const {
        upstream_model,
        token_parameter,
        provider_connection_id,
        ...fields
      } = values;
      const config = provider_connection_id
        ? {
            model: upstream_model,
            ...(fields.adapter !== "anthropic.chat" &&
            token_parameter &&
            token_parameter !== "auto"
              ? { token_parameter }
              : {}),
          }
        : JSON.parse(values.config);
      if (!config || typeof config !== "object" || Array.isArray(config))
        throw new Error(t("invalidJSON"));
      return api(
        editing ? "/api/v1/models/" + editing.id : "/api/v1/models",
        json(editing ? "PUT" : "POST", {
          ...fields,
          provider_connection_id: provider_connection_id || null,
          config,
        }),
      );
    },
    () => setOpen(false),
  );
  const action = useAction(({ id, action }: { id: string; action: string }) =>
    api(`/api/v1/models/${id}/${action}`, json("POST")),
  );
  const rows = query.data?.data.filter(
    (m) =>
      (!search || m.name.toLowerCase().includes(search.toLowerCase())) &&
      (!capability || m.capability === capability),
  );
  return (
    <>
      <PageHeading
        title={t("models")}
        description={t("mockDescription")}
        extra={
          admin && (
            <Space wrap>
              <ModelDownloads />
              <Button
                type="primary"
                icon={<PlusOutlined aria-hidden="true" />}
                onClick={() => showForm(null)}
              >
                {t("newModel")}
              </Button>
            </Space>
          )
        }
      />
      <QueryError error={query.error} />
      <Card className="table-card">
        <div className="table-toolbar">
          <Space wrap>
            <Input
              allowClear
              prefix={<SearchOutlined aria-hidden="true" />}
              placeholder={t("search")}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ width: 250 }}
            />
            <Select
              value={capability}
              style={{ width: 150 }}
              onChange={setCapability}
              options={[
                { value: "", label: t("all") },
                ...["chat", "image", "tts", "asr", "video", "subtitle"].map(
                  (value) => ({
                    value,
                    label: t(value),
                  }),
                ),
              ]}
            />
          </Space>
          <Button
            type="text"
            icon={<ReloadOutlined aria-hidden="true" />}
            onClick={() => void query.refetch()}
          >
            {t("refresh")}
          </Button>
        </div>
        <Table
          rowKey="id"
          dataSource={rows}
          loading={query.isLoading}
          scroll={{ x: 1000 }}
          columns={[
            {
              title: t("model"),
              render: (_, m: Model) => (
                <div className="model-cell">
                  <CapabilityIcon capability={m.capability} />
                  <div>
                    <a onClick={() => setDetail(m)}>
                      <strong>{m.name}</strong>
                    </a>
                    <span>{m.adapter}</span>
                  </div>
                </div>
              ),
            },
            {
              title: t("capability"),
              dataIndex: "capability",
              render: (value) => <Tag bordered={false}>{t(value)}</Tag>,
            },
            {
              title: t("state"),
              render: (_, m: Model) => (
                <Space direction="vertical" size={4}>
                  <Status value={m.state} />
                  <span className="muted small">
                    {t(m.enabled ? "enabled" : "disabled")} · {m.active}{" "}
                    {t("activeRequests")}
                  </span>
                </Space>
              ),
            },
            {
              title: t("device"),
              render: (_, m: Model) => (
                <div>
                  <code>{m.device}</code>
                  <div className="muted small">
                    {(m.memory_mb / 1024).toFixed(1)} GB
                  </div>
                </div>
              ),
            },
            {
              title: t("idle"),
              dataIndex: "idle_seconds",
              render: (value) => value + " s",
            },
            {
              title: t("actions"),
              render: (_, m: Model) =>
                admin ? (
                  <Space size={4}>
                    <Tooltip
                      title={m.state !== "unloaded" ? t("busyEdit") : t("edit")}
                    >
                      <Button
                        size="small"
                        type="text"
                        icon={<EditOutlined aria-hidden="true" />}
                        aria-label={t("edit")}
                        disabled={m.state !== "unloaded"}
                        onClick={() => showForm(m)}
                      />
                    </Tooltip>
                    <Button
                      size="small"
                      type="text"
                      loading={action.isPending}
                      disabled={
                        !m.enabled ||
                        m.active > 0 ||
                        ["loading", "unloading"].includes(m.state)
                      }
                      onClick={() =>
                        action.mutate({
                          id: m.id,
                          action: m.state === "ready" ? "unload" : "load",
                        })
                      }
                    >
                      {t(m.state === "ready" ? "unload" : "load")}
                    </Button>
                    <Popconfirm
                      title={t("confirmAction")}
                      onConfirm={() =>
                        action.mutate({
                          id: m.id,
                          action: m.enabled ? "disable" : "enable",
                        })
                      }
                    >
                      <Button
                        size="small"
                        type="text"
                        danger={m.enabled}
                        icon={<PoweroffOutlined aria-hidden="true" />}
                      >
                        {t(m.enabled ? "disable" : "enable")}
                      </Button>
                    </Popconfirm>
                  </Space>
                ) : (
                  <Button type="link" onClick={() => setDetail(m)}>
                    {t("details")}
                  </Button>
                ),
            },
          ]}
        />
      </Card>
      <Drawer
        open={open}
        onClose={() => setOpen(false)}
        width={600}
        title={t(editing ? "edit" : "newModel")}
        destroyOnClose
        extra={
          <Button
            type="primary"
            loading={save.isPending}
            onClick={() => form.submit()}
          >
            {t("save")}
          </Button>
        }
      >
        <Form
          name="model-editor"
          form={form}
          layout="vertical"
          onFinish={(values) => {
            setFormError("");
            try {
              JSON.parse(values.config);
              save.mutate(values);
            } catch {
              setFormError(t("invalidJSON"));
            }
          }}
        >
          <Form.Item
            name="name"
            label={t("name")}
            rules={[
              { required: true, message: t("required") },
              {
                pattern: /^[a-zA-Z0-9][a-zA-Z0-9._/-]*$/,
                message: "a-z A-Z 0-9 . _ / -",
              },
            ]}
            extra={t("modelNameHint")}
          >
            <Input />
          </Form.Item>
          {selectedCap === "chat" && (
            <>
              <QueryError error={connections.error} />
              <Form.Item
                name="provider_connection_id"
                label={t("providerConnection")}
                extra={<Link to="/providers">{t("manageConnections")}</Link>}
              >
                <Select
                  allowClear
                  placeholder={t("legacyAdapterConfig")}
                  loading={connections.isPending}
                  options={connections.data?.data.map((c) => ({
                    value: c.id,
                    label: c.name,
                  }))}
                  onChange={(id) => {
                    const connection = connections.data?.data.find(
                      (c) => c.id === id,
                    );
                    form.setFieldsValue({
                      adapter:
                        connection?.provider === "anthropic"
                          ? "anthropic.chat"
                          : "openai_compatible.chat",
                      enabled: false,
                      device: "cpu",
                      memory_mb: 0,
                      config: JSON.stringify(
                        {
                          base_url: "https://api.openai.com/v1",
                          api_key_env: "MODELDock_CHAT_KEY",
                          model: "YOUR_UPSTREAM_MODEL",
                        },
                        null,
                        2,
                      ),
                    });
                  }}
                />
              </Form.Item>
              {selectedConnection && (
                <>
                  <Form.Item
                    name="upstream_model"
                    label={t("upstreamModel")}
                    rules={[{ required: true, whitespace: true }]}
                    extra={t("upstreamModelHint")}
                  >
                    <Input maxLength={200} />
                  </Form.Item>
                  <Form.Item
                    name="token_parameter"
                    label={t("tokenParameter")}
                    hidden={selectedAdapter === "anthropic.chat"}
                  >
                    <Select
                      options={[
                        { value: "auto", label: t("providerDefault") },
                        { value: "max_tokens", label: "max_tokens" },
                        {
                          value: "max_completion_tokens",
                          label: "max_completion_tokens",
                        },
                      ]}
                    />
                  </Form.Item>
                </>
              )}
            </>
          )}
          <div className="form-grid">
            <Form.Item
              name="capability"
              label={t("capability")}
              rules={[{ required: true }]}
            >
              <Select
                onChange={(value) => {
                  const name =
                    value === "video"
                      ? "comfyui.video"
                      : value === "subtitle"
                        ? "whisperx.subtitle"
                        : "mock." + value;
                  const descriptor = adapters.data?.data.find(
                    (a) => a.name === name,
                  );
                  form.setFieldsValue({
                    provider_connection_id: null,
                    adapter: name,
                    config: JSON.stringify(
                      descriptor?.config_example || {},
                      null,
                      2,
                    ),
                    enabled: !descriptor?.remote,
                    device: descriptor?.remote ? "cpu" : "virtual:0",
                    memory_mb: descriptor?.remote ? 0 : 1024,
                  });
                }}
                options={[
                  "chat",
                  "image",
                  "tts",
                  "asr",
                  "video",
                  "subtitle",
                ].map((value) => ({
                  value,
                  label: t(value),
                }))}
              />
            </Form.Item>
            <Form.Item
              name="adapter"
              hidden={!!selectedConnection}
              label={t("adapter")}
              rules={[{ required: true }]}
            >
              <Select
                onChange={(value) => {
                  const descriptor = adapters.data?.data.find(
                    (a) => a.name === value,
                  );
                  form.setFieldsValue({
                    config: JSON.stringify(
                      descriptor?.config_example || {},
                      null,
                      2,
                    ),
                    enabled: false,
                    device:
                      descriptor?.remote || descriptor?.local_speech
                        ? "cpu"
                        : "virtual:0",
                    memory_mb:
                      descriptor?.remote || descriptor?.local_speech ? 0 : 1024,
                  });
                }}
                options={adapters.data?.data
                  .filter((a) => !a.capability || a.capability === selectedCap)
                  .map((a) => ({
                    value: a.name,
                    label:
                      a.name +
                      (!a.implemented ? " · " + t("unimplemented") : ""),
                  }))}
              />
            </Form.Item>
          </div>
          {selectedAdapter.startsWith("template.") && (
            <Alert
              type="info"
              showIcon
              message={t("notImplementedHint")}
              style={{ marginBottom: 16 }}
            />
          )}
          <div className="form-grid">
            <Form.Item name="device" label={t("device")}>
              <Select
                options={[
                  { value: "virtual:0", label: "virtual:0 · " + t("mock") },
                  { value: "cpu", label: "CPU" },
                ]}
              />
            </Form.Item>
            <Form.Item name="memory_mb" label={t("memory")}>
              <InputNumber min={0} max={1048576} />
            </Form.Item>
            <Form.Item name="concurrency" label={t("concurrency")}>
              <InputNumber min={1} max={32} />
            </Form.Item>
            <Form.Item name="timeout_seconds" label={t("timeout")}>
              <InputNumber min={1} max={3600} />
            </Form.Item>
            <Form.Item name="idle_seconds" label={t("idle")}>
              <InputNumber min={1} max={86400} />
            </Form.Item>
            <Form.Item
              name="enabled"
              label={t("enabled")}
              valuePropName="checked"
            >
              <Switch disabled={selectedAdapter.startsWith("template.")} />
            </Form.Item>
          </div>
          <Form.Item
            name="config"
            hidden={!!selectedConnection}
            label={t("config")}
            rules={[{ required: true }]}
          >
            <Input.TextArea rows={7} className="mono" />
          </Form.Item>
          {!selectedConnection && !selectedAdapter.startsWith("mock.") && (
            <>
              <Alert type="info" message={t("providerConfigHint")} />
              <details>
                <summary>{t("configSchema")}</summary>
                <pre>
                  {JSON.stringify(
                    adapters.data?.data.find((a) => a.name === selectedAdapter)
                      ?.config_schema || {},
                    null,
                    2,
                  )}
                </pre>
              </details>
            </>
          )}
          {formError && <Alert type="error" message={formError} />}
        </Form>
      </Drawer>
      <Modal
        open={!!detail}
        title={detail?.name}
        onCancel={() => setDetail(null)}
        footer={null}
      >
        {detail && (
          <div className="detail-list">
            {[
              ["adapter", detail.adapter],
              ["state", t(detail.state)],
              ["device", detail.device],
              ["activeRequests", detail.active],
              ["lastUsed", formatDate(detail.last_used)],
              ["lastError", detail.last_error || t("noError")],
            ].map(([key, value]) => (
              <div key={key}>
                <span>{t(String(key))}</span>
                <strong>{value}</strong>
              </div>
            ))}
            <pre>{JSON.stringify(detail.config, null, 2)}</pre>
          </div>
        )}
      </Modal>
    </>
  );
}
