import { useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  Alert,
  Button,
  Card,
  Drawer,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
} from "antd";
import { useTranslation } from "react-i18next";
import { api, APIError, json } from "../api";
import { PageHeading, QueryError } from "../components";
import { useRows } from "../hooks";
import type { ProviderConnection, ProviderPreset } from "../types";

export function ProvidersPage() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const connections = useRows<ProviderConnection>(
    "/api/v1/provider-connections",
  );
  const presets = useRows<ProviderPreset>("/api/v1/provider-presets", {}, 0);
  const [form] = Form.useForm();
  const provider = Form.useWatch("provider", form);
  const selected = presets.data?.data.find((p) => p.id === provider);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ProviderConnection | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const errorText = (err: unknown) =>
    (err instanceof APIError &&
      t(`providerError_${err.code}`, { defaultValue: "" })) ||
    t("providerOperationFailed");
  const show = (connection: ProviderConnection | null) => {
    setEditing(connection);
    setError("");
    form.resetFields();
    form.setFieldsValue(
      connection
        ? { ...connection, api_key: "" }
        : {
            name: "",
            provider: "openai",
            base_url:
              presets.data?.data.find((p) => p.id === "openai")?.base_url ||
              "https://api.openai.com/v1",
            connect_timeout: 10,
            read_timeout: 60,
          },
    );
    setOpen(true);
  };
  const close = () => {
    form.resetFields();
    setOpen(false);
  };
  return (
    <>
      <PageHeading
        title={t("providers")}
        description={t("providersDescription")}
        extra={
          <Button
            type="primary"
            onClick={() => show(null)}
            disabled={presets.isPending || !!presets.error}
          >
            {t("newConnection")}
          </Button>
        }
      />
      <QueryError error={connections.error || presets.error} />
      {!open && error && <Alert showIcon type="error" message={error} />}
      <Card>
        <Table
          rowKey="id"
          loading={connections.isPending}
          dataSource={connections.data?.data}
          scroll={{ x: 800 }}
          columns={[
            { title: t("name"), dataIndex: "name" },
            {
              title: t("provider"),
              render: (_, c: ProviderConnection) =>
                presets.data?.data.find((p) => p.id === c.provider)?.name ||
                c.provider,
            },
            { title: t("baseURL"), dataIndex: "base_url" },
            {
              title: t("providerAPIKey"),
              render: (_, c: ProviderConnection) => (
                <Tag>
                  {t(c.has_api_key ? "credentialSaved" : "credentialMissing")}
                </Tag>
              ),
            },
            {
              title: t("linkedModels"),
              render: (_, c: ProviderConnection) =>
                c.models.length ? (
                  <Space wrap>
                    {c.models.map((m) => (
                      <Link
                        key={m.id}
                        to={`/playground?model=${encodeURIComponent(m.name)}`}
                      >
                        {m.name}
                      </Link>
                    ))}
                  </Space>
                ) : (
                  t("noLinkedModels")
                ),
            },
            {
              title: t("actions"),
              render: (_, c: ProviderConnection) => (
                <Space>
                  <Button disabled={busy} onClick={() => show(c)}>
                    {t("editConnection")}
                  </Button>
                  <Popconfirm
                    title={t("deleteConnectionConfirm")}
                    onConfirm={async () => {
                      setBusy(true);
                      setError("");
                      try {
                        await api(
                          `/api/v1/provider-connections/${c.id}`,
                          json("DELETE"),
                        );
                        await qc.invalidateQueries();
                      } catch (err) {
                        setError(errorText(err));
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    <Button danger disabled={busy || c.models.length > 0}>
                      {t("delete")}
                    </Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>
      <Drawer
        title={t(editing ? "editConnection" : "newConnection")}
        open={open}
        width={560}
        destroyOnClose
        onClose={busy ? undefined : close}
        closable={!busy}
        maskClosable={!busy}
        extra={
          <Button type="primary" loading={busy} onClick={() => form.submit()}>
            {t("save")}
          </Button>
        }
      >
        <Alert
          type="info"
          showIcon
          message={t("providerCredentialHint")}
          style={{ marginBottom: 16 }}
        />
        <Form
          name="provider-connection-editor"
          form={form}
          layout="vertical"
          onFinish={async (values) => {
            setBusy(true);
            setError("");
            // Deliberately bypass TanStack mutations: credentials must not enter its cache.
            try {
              await api(
                editing
                  ? `/api/v1/provider-connections/${editing.id}`
                  : "/api/v1/provider-connections",
                json(editing ? "PUT" : "POST", values),
              );
              close();
              await qc.invalidateQueries();
            } catch (err) {
              setError(errorText(err));
            } finally {
              form.setFieldValue("api_key", "");
              setBusy(false);
            }
          }}
        >
          <Form.Item
            name="name"
            label={t("name")}
            rules={[{ required: true, whitespace: true }]}
          >
            <Input maxLength={80} />
          </Form.Item>
          <Form.Item
            name="provider"
            label={t("provider")}
            rules={[{ required: true }]}
          >
            <Select
              disabled={!!editing?.models.length}
              options={presets.data?.data.map((p) => ({
                value: p.id,
                label: p.name,
              }))}
              onChange={(value) => {
                form.setFieldsValue({
                  base_url:
                    presets.data?.data.find((p) => p.id === value)?.base_url ||
                    "",
                  api_key: "",
                });
              }}
            />
          </Form.Item>
          {selected?.docs_url && (
            <p>
              <a href={selected.docs_url} target="_blank" rel="noreferrer">
                {t("providerOfficialDocs")}
              </a>
            </p>
          )}
          {provider === "qwen" && (
            <Alert type="info" message={t("qwenEndpointHint")} />
          )}
          <Form.Item
            name="base_url"
            label={t("baseURL")}
            rules={[{ required: true }]}
          >
            <Input maxLength={500} autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="api_key"
            label={t("providerAPIKey")}
            extra={t(
              editing ? "credentialKeepHint" : "credentialWriteOnlyHint",
            )}
            rules={[{ required: !editing }]}
          >
            <Input.Password autoComplete="new-password" maxLength={4096} />
          </Form.Item>
          <div className="form-grid">
            <Form.Item
              name="connect_timeout"
              label={t("connectTimeout")}
              rules={[{ required: true }]}
            >
              <InputNumber min={0.1} max={60} />
            </Form.Item>
            <Form.Item
              name="read_timeout"
              label={t("readTimeout")}
              rules={[{ required: true }]}
            >
              <InputNumber min={0.1} max={300} />
            </Form.Item>
          </div>
          {error && <Alert type="error" showIcon message={error} />}
        </Form>
      </Drawer>
    </>
  );
}
