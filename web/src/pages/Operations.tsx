import { useState } from "react";
import {
  Alert,
  Button,
  Card,
  DatePicker,
  Drawer,
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
  Tooltip,
  Typography,
  Upload,
} from "antd";
import {
  DeleteOutlined,
  DownloadOutlined,
  EyeOutlined,
  KeyOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import { api, formatDate, json } from "../api";
import {
  CapabilityIcon,
  Copy,
  FilePreview,
  PageHeading,
  QueryError,
  Status,
} from "../components";
import { useAction, useRows } from "../hooks";
import type { CallLog, Key, Model, StoredFile, Task, User } from "../types";

export function KeysPage() {
  const { t } = useTranslation();
  const query = useRows<Key>("/api/v1/keys");
  const models = useRows<Model>("/api/v1/models");
  const [open, setOpen] = useState(false),
    [secret, setSecret] = useState("");
  const [form] = Form.useForm();
  const save = useAction(
    async (values: any) => {
      const result = await api<Key>(
        "/api/v1/keys",
        json("POST", {
          name: values.name,
          model_ids: values.model_ids,
          rpm: values.rpm,
          expires_at: values.expires_at ? values.expires_at.unix() : null,
        }),
      );
      setSecret(result.secret!);
      // Keep the one-time secret out of the mutation result cache.
      return undefined;
    },
    () => {
      setOpen(false);
      form.resetFields();
    },
  );
  const revoke = useAction((id: string) =>
    api("/api/v1/keys/" + id, json("DELETE")),
  );
  return (
    <>
      <PageHeading
        title={t("keys")}
        description={t("grantHint")}
        extra={
          <Button
            type="primary"
            icon={<PlusOutlined aria-hidden="true" />}
            onClick={() => setOpen(true)}
          >
            {t("createKey")}
          </Button>
        }
      />
      <QueryError error={query.error} />
      <Card className="table-card">
        <Table
          rowKey="id"
          dataSource={query.data?.data}
          loading={query.isLoading}
          scroll={{ x: 850 }}
          columns={[
            {
              title: t("name"),
              render: (_, key: Key) => (
                <Space>
                  <span className="table-key-icon">
                    <KeyOutlined aria-hidden="true" />
                  </span>
                  <strong>{key.name}</strong>
                </Space>
              ),
            },
            {
              title: t("keyPrefix"),
              dataIndex: "prefix",
              render: (value) => <code>{value}••••••</code>,
            },
            {
              title: t("modelAccess"),
              dataIndex: "model_ids",
              render: (ids: string[]) =>
                ids.map((id) => (
                  <Tag key={id}>
                    {models.data?.data.find((m) => m.id === id)?.name ||
                      id.slice(0, 8)}
                  </Tag>
                )),
            },
            { title: t("rpm"), dataIndex: "rpm" },
            {
              title: t("status"),
              render: (_, key: Key) => (
                <Tag
                  color={
                    key.revoked ||
                    (key.expires_at && key.expires_at < Date.now() / 1000)
                      ? "default"
                      : "green"
                  }
                >
                  {t(
                    key.revoked
                      ? "revoked"
                      : key.expires_at && key.expires_at < Date.now() / 1000
                        ? "disabled"
                        : "active",
                  )}
                </Tag>
              ),
            },
            {
              title: t("expires"),
              dataIndex: "expires_at",
              render: (value) => (value ? formatDate(value) : t("never")),
            },
            {
              title: t("actions"),
              render: (_, key: Key) => (
                <Popconfirm
                  title={t("confirmAction")}
                  onConfirm={() => revoke.mutate(key.id)}
                >
                  <Button type="text" danger disabled={key.revoked}>
                    {t("revoke")}
                  </Button>
                </Popconfirm>
              ),
            },
          ]}
        />
      </Card>
      <Modal
        title={t("createKey")}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={save.isPending}
        destroyOnClose
      >
        <Form
          layout="vertical"
          name="key-create"
          form={form}
          initialValues={{ rpm: 60 }}
          onFinish={(values) => save.mutate(values)}
        >
          <Form.Item
            name="name"
            label={t("keyName")}
            rules={[{ required: true, message: t("required") }]}
          >
            <Input maxLength={80} />
          </Form.Item>
          <Form.Item
            name="model_ids"
            label={t("modelAccess")}
            rules={[{ required: true, message: t("required") }]}
          >
            <Select
              mode="multiple"
              options={models.data?.data.map((m) => ({
                value: m.id,
                label: m.name,
              }))}
            />
          </Form.Item>
          <Form.Item name="rpm" label={t("rpm")}>
            <InputNumber min={1} max={10000} />
          </Form.Item>
          <Form.Item name="expires_at" label={t("expires")}>
            <DatePicker showTime placeholder={t("never")} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        open={!!secret}
        title={t("secretTitle")}
        onCancel={() => setSecret("")}
        footer={
          <Button type="primary" onClick={() => setSecret("")}>
            {t("close")}
          </Button>
        }
      >
        <Alert type="warning" showIcon message={t("secretHint")} />
        <Typography.Paragraph
          className="secret-key"
          copyable={{ text: secret }}
        >
          {secret}
        </Typography.Paragraph>
      </Modal>
    </>
  );
}

export function TasksPage() {
  const { t } = useTranslation();
  const [status, setStatus] = useState(""),
    [page, setPage] = useState(1),
    [detail, setDetail] = useState<Task | null>(null);
  const query = useRows<Task>(
    "/v1/tasks",
    { status, offset: (page - 1) * 20, limit: 20 },
    2000,
  );
  const action = useAction(({ id, action }: { id: string; action: string }) =>
    api(`/v1/tasks/${id}/${action}`, json("POST")),
  );
  const current = detail
    ? query.data?.data.find((task) => task.id === detail.id) || detail
    : null;
  return (
    <>
      <PageHeading title={t("tasks")} description={t("taskHint")} />
      <QueryError error={query.error} />
      <Card className="table-card">
        <div className="table-toolbar">
          <Select
            value={status}
            style={{ width: 170 }}
            onChange={(value) => {
              setStatus(value);
              setPage(1);
            }}
            options={[
              { value: "", label: t("all") },
              ...["queued", "running", "succeeded", "failed", "cancelled"].map(
                (value) => ({ value, label: t(value) }),
              ),
            ]}
          />
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
          dataSource={query.data?.data}
          loading={query.isLoading}
          scroll={{ x: 1000 }}
          pagination={{
            current: page,
            pageSize: 20,
            total: query.data?.total,
            onChange: setPage,
            showSizeChanger: false,
          }}
          columns={[
            {
              title: t("taskId"),
              dataIndex: "id",
              render: (value) => (
                <Space>
                  <code>{value.slice(0, 8)}</code>
                  <Copy text={value} />
                </Space>
              ),
            },
            {
              title: t("model"),
              render: (_, task: Task) => (
                <Space>
                  <CapabilityIcon capability={task.capability} />
                  <strong>{task.model}</strong>
                </Space>
              ),
            },
            {
              title: t("status"),
              dataIndex: "status",
              render: (value) => <Status value={value} />,
            },
            {
              title: t("progress"),
              dataIndex: "progress",
              width: 160,
              render: (value: number, task: Task) => (
                <Progress
                  size="small"
                  percent={value}
                  status={
                    task.status === "failed"
                      ? "exception"
                      : task.status === "running"
                        ? "active"
                        : "normal"
                  }
                />
              ),
            },
            { title: t("time"), dataIndex: "created_at", render: formatDate },
            {
              title: t("actions"),
              render: (_, task: Task) => (
                <Space size={0}>
                  <Button type="link" onClick={() => setDetail(task)}>
                    {t("details")}
                  </Button>
                  {["queued", "running"].includes(task.status) ? (
                    <Popconfirm
                      title={t("confirmAction")}
                      onConfirm={() =>
                        action.mutate({ id: task.id, action: "cancel" })
                      }
                    >
                      <Button
                        type="text"
                        danger
                        disabled={task.cancel_requested}
                      >
                        {t("cancelTask")}
                      </Button>
                    </Popconfirm>
                  ) : (
                    ["failed", "cancelled"].includes(task.status) && (
                      <Button
                        type="text"
                        onClick={() =>
                          action.mutate({ id: task.id, action: "retry" })
                        }
                      >
                        {t("retry")}
                      </Button>
                    )
                  )}
                </Space>
              ),
            },
          ]}
        />
      </Card>
      <Drawer
        open={!!current}
        width={650}
        title={t("taskResult")}
        onClose={() => setDetail(null)}
      >
        {current && (
          <>
            <div className="detail-list">
              <div>
                <span>{t("taskId")}</span>
                <code>{current.id}</code>
              </div>
              <div>
                <span>{t("model")}</span>
                <strong>{current.model}</strong>
              </div>
              <div>
                <span>{t("status")}</span>
                <Status value={current.status} />
              </div>
              <div>
                <span>{t("requestId")}</span>
                <Space>
                  <code>{current.request_id}</code>
                  <Copy text={current.request_id} />
                </Space>
              </div>
              <div>
                <span>{t("created")}</span>
                {formatDate(current.created_at)}
              </div>
            </div>
            {current.error && (
              <Alert
                type="error"
                showIcon
                message={current.error.message}
                description={current.error.code}
              />
            )}
            {current.execution_metadata &&
              Object.keys(current.execution_metadata).length > 0 && (
                <>
                  <Alert type="info" message={t("remoteNotice")} />
                  <pre>
                    {JSON.stringify(current.execution_metadata, null, 2)}
                  </pre>
                </>
              )}
            <div className="task-files">
              {(current.result?.files as StoredFile[] | undefined)?.map(
                (file) => <FilePreview key={file.id} file={file} />,
              )}
            </div>
            {current.result?.text && (
              <Card>
                <p className="chat-text">{current.result.text}</p>
              </Card>
            )}
            <pre className="result-json">
              {JSON.stringify(current.result || {}, null, 2)}
            </pre>
          </>
        )}
      </Drawer>
    </>
  );
}

export function FilesPage() {
  const { t } = useTranslation();
  const [type, setType] = useState(""),
    [page, setPage] = useState(1),
    [preview, setPreview] = useState<StoredFile | null>(null),
    [uploading, setUploading] = useState(false);
  const query = useRows<StoredFile>("/v1/files", {
    media_type: type,
    offset: (page - 1) * 20,
    limit: 20,
  });
  const remove = useAction((id: string) =>
    api("/v1/files/" + id, json("DELETE")),
  );
  const upload = useAction(async (file: File) => {
    setUploading(true);
    try {
      const body = new FormData();
      body.append("file", file);
      return await api("/v1/files", { method: "POST", body });
    } finally {
      setUploading(false);
    }
  });
  return (
    <>
      <PageHeading
        title={t("files")}
        description={t("fileHint")}
        extra={
          <Upload
            accept=".wav,.mp3,.flac,.png,.jpg,.jpeg,.webp,.mp4,.txt"
            showUploadList={false}
            beforeUpload={(file) => {
              upload.mutate(file);
              return false;
            }}
          >
            <Button
              type="primary"
              loading={uploading}
              icon={<UploadOutlined aria-hidden="true" />}
            >
              {t("upload")}
            </Button>
          </Upload>
        }
      />
      <QueryError error={query.error} />
      <Card className="table-card">
        <div className="table-toolbar">
          <Select
            style={{ width: 170 }}
            value={type}
            onChange={(value) => {
              setType(value);
              setPage(1);
            }}
            options={[
              { value: "", label: t("all") },
              { value: "image/png", label: "PNG" },
              { value: "audio/wav", label: "WAV" },
              { value: "audio/mpeg", label: "MP3" },
              { value: "video/mp4", label: "MP4" },
              { value: "text/x-ssa", label: "ASS" },
              { value: "image/jpeg", label: "JPEG" },
              { value: "image/webp", label: "WebP" },
              { value: "audio/flac", label: "FLAC" },
              { value: "text/plain", label: "Text" },
            ]}
          />
          <span className="muted">
            {t("total")} {query.data?.total || 0}
          </span>
        </div>
        <Table
          rowKey="id"
          dataSource={query.data?.data}
          loading={query.isLoading}
          scroll={{ x: 850 }}
          pagination={{
            current: page,
            pageSize: 20,
            total: query.data?.total,
            onChange: setPage,
            showSizeChanger: false,
          }}
          columns={[
            {
              title: t("filename"),
              render: (_, file: StoredFile) => (
                <Space>
                  <span className="file-type-icon">
                    {file.media_type.split("/")[1].toUpperCase()}
                  </span>
                  <div>
                    <a onClick={() => setPreview(file)}>
                      <strong>{file.filename}</strong>
                    </a>
                    <div className="muted small">{file.id.slice(0, 8)}</div>
                  </div>
                </Space>
              ),
            },
            {
              title: t("type"),
              dataIndex: "media_type",
              render: (value) => <Tag>{value}</Tag>,
            },
            {
              title: t("size"),
              dataIndex: "size",
              render: (value) => (value / 1024).toFixed(1) + " KB",
            },
            {
              title: t("created"),
              dataIndex: "created_at",
              render: formatDate,
            },
            {
              title: t("expires"),
              dataIndex: "expires_at",
              render: formatDate,
            },
            {
              title: t("actions"),
              render: (_, file: StoredFile) => (
                <Space size={0}>
                  <Tooltip title={t("preview")}>
                    <Button
                      type="text"
                      icon={<EyeOutlined aria-hidden="true" />}
                      onClick={() => setPreview(file)}
                    />
                  </Tooltip>
                  <a href={file.url} download={file.filename}>
                    <Button
                      type="text"
                      icon={<DownloadOutlined aria-hidden="true" />}
                      aria-label={t("download")}
                    />
                  </a>
                  <Popconfirm
                    title={t("deleteHint")}
                    onConfirm={() => remove.mutate(file.id)}
                  >
                    <Button
                      type="text"
                      danger
                      icon={<DeleteOutlined aria-hidden="true" />}
                      aria-label={t("delete")}
                    />
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>
      <Modal
        open={!!preview}
        title={preview?.filename}
        footer={null}
        onCancel={() => setPreview(null)}
        destroyOnClose
      >
        {preview && <FilePreview file={preview} />}
      </Modal>
    </>
  );
}

export function LogsPage() {
  const { t } = useTranslation();
  const [filters, setFilters] = useState<{
      model?: string;
      status?: string;
      request_id?: string;
      since?: number;
      until?: number;
    }>({}),
    [page, setPage] = useState(1);
  const models = useRows<Model>("/api/v1/models");
  const query = useRows<CallLog>("/api/v1/logs", {
    ...filters,
    offset: (page - 1) * 20,
    limit: 20,
  });
  const setFilter = (key: string, value: string | number | undefined) => {
    setFilters((old) => ({ ...old, [key]: value }));
    setPage(1);
  };
  return (
    <>
      <PageHeading title={t("logs")} description={t("logsHint")} />
      <QueryError error={query.error} />
      <Card className="table-card">
        <div className="table-toolbar">
          <Space wrap>
            <Select
              style={{ width: 180 }}
              value={filters.model || ""}
              onChange={(value) => setFilter("model", value)}
              options={[
                { value: "", label: t("all") + " · " + t("model") },
                ...(models.data?.data || []).map((m) => ({
                  value: m.name,
                  label: m.name,
                })),
              ]}
            />
            <Select
              style={{ width: 150 }}
              value={filters.status || ""}
              onChange={(value) => setFilter("status", value)}
              options={[
                { value: "", label: t("all") + " · " + t("status") },
                ...["succeeded", "failed", "cancelled", "rejected"].map(
                  (value) => ({ value, label: t(value) }),
                ),
              ]}
            />
            <Input
              allowClear
              prefix={<SearchOutlined aria-hidden="true" />}
              placeholder={t("requestId")}
              value={filters.request_id}
              onChange={(e) => setFilter("request_id", e.target.value)}
              style={{ width: 220 }}
            />
            <DatePicker.RangePicker
              showTime
              onChange={(value) => {
                setFilters((old) => ({
                  ...old,
                  since: value?.[0]?.unix(),
                  until: value?.[1]?.unix(),
                }));
                setPage(1);
              }}
            />
          </Space>
        </div>
        <Table
          rowKey="id"
          dataSource={query.data?.data}
          loading={query.isLoading}
          scroll={{ x: 1200 }}
          pagination={{
            current: page,
            pageSize: 20,
            total: query.data?.total,
            onChange: setPage,
            showSizeChanger: false,
          }}
          columns={[
            { title: t("time"), dataIndex: "created_at", render: formatDate },
            { title: t("username"), dataIndex: "username" },
            {
              title: t("model"),
              dataIndex: "model",
              render: (value) => <code>{value}</code>,
            },
            {
              title: t("status"),
              dataIndex: "status",
              render: (value) => <Status value={value} />,
            },
            {
              title: t("duration"),
              dataIndex: "duration_ms",
              render: (value) => value.toLocaleString() + " ms",
            },
            {
              title: t("usage"),
              dataIndex: "usage",
              render: (value) =>
                value.total_tokens ? value.total_tokens + " tokens" : "—",
            },
            {
              title: t("errorCode"),
              dataIndex: "error_code",
              render: (value) => (value ? <Tag color="red">{value}</Tag> : "—"),
            },
            {
              title: t("requestId"),
              dataIndex: "request_id",
              render: (value) => (
                <Space>
                  <code>{value.slice(0, 8)}</code>
                  <Copy text={value} />
                </Space>
              ),
            },
          ]}
        />
      </Card>
    </>
  );
}

export function UsersPage() {
  const { t } = useTranslation();
  const query = useRows<User>("/api/v1/users");
  const models = useRows<Model>("/api/v1/models");
  const [open, setOpen] = useState(false),
    [grantUser, setGrantUser] = useState<User | null>(null),
    [resetUser, setResetUser] = useState<User | null>(null);
  const [form] = Form.useForm(),
    [grantsForm] = Form.useForm(),
    [resetForm] = Form.useForm();
  const create = useAction(
    (values: any) => api("/api/v1/users", json("POST", values)),
    () => {
      setOpen(false);
      form.resetFields();
    },
  );
  const update = useAction(
    ({ id, body }: { id: string; body: unknown }) =>
      api("/api/v1/users/" + id, json("PATCH", body)),
    () => {
      setGrantUser(null);
      setResetUser(null);
      resetForm.resetFields();
    },
  );
  return (
    <>
      <PageHeading
        title={t("users")}
        description={t("grantHint")}
        extra={
          <Button
            type="primary"
            icon={<PlusOutlined aria-hidden="true" />}
            onClick={() => setOpen(true)}
          >
            {t("createUser")}
          </Button>
        }
      />
      <QueryError error={query.error} />
      <Card className="table-card">
        <Table
          rowKey="id"
          dataSource={query.data?.data}
          loading={query.isLoading}
          scroll={{ x: 850 }}
          columns={[
            {
              title: t("username"),
              dataIndex: "username",
              render: (value) => <strong>{value}</strong>,
            },
            {
              title: t("role"),
              dataIndex: "role",
              render: (value) => (
                <Tag color={value === "admin" ? "purple" : "default"}>
                  {t(value)}
                </Tag>
              ),
            },
            {
              title: t("status"),
              dataIndex: "enabled",
              render: (value) => (
                <Status value={value ? "enabled" : "disabled"} />
              ),
            },
            {
              title: t("modelAccess"),
              render: (_, user: User) =>
                user.role === "admin" ? (
                  <Tag color="purple">{t("all")}</Tag>
                ) : (
                  user.model_ids?.map((id) => (
                    <Tag key={id}>
                      {models.data?.data.find((m) => m.id === id)?.name ||
                        id.slice(0, 8)}
                    </Tag>
                  ))
                ),
            },
            {
              title: t("created"),
              dataIndex: "created_at",
              render: formatDate,
            },
            {
              title: t("actions"),
              render: (_, user: User) => (
                <Space size={0}>
                  <Button
                    type="text"
                    disabled={user.role === "admin"}
                    onClick={() => {
                      grantsForm.setFieldsValue({ model_ids: user.model_ids });
                      setGrantUser(user);
                    }}
                  >
                    {t("userAccess")}
                  </Button>
                  <Button type="text" onClick={() => setResetUser(user)}>
                    {t("resetPassword")}
                  </Button>
                  <Popconfirm
                    title={t("confirmAction")}
                    onConfirm={() =>
                      update.mutate({
                        id: user.id,
                        body: { enabled: !user.enabled },
                      })
                    }
                  >
                    <Button type="text" danger={user.enabled}>
                      {t(user.enabled ? "disable" : "enable")}
                    </Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>
      <Modal
        title={t("createUser")}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={create.isPending}
      >
        <Form
          layout="vertical"
          name="user-create"
          form={form}
          initialValues={{ role: "user", model_ids: [] }}
          onFinish={(values) => create.mutate(values)}
        >
          <Form.Item
            name="username"
            label={t("username")}
            rules={[{ required: true, message: t("required") }]}
          >
            <Input minLength={2} maxLength={80} autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="password"
            label={t("password")}
            rules={[
              { required: true, message: t("required") },
              { min: 12, message: t("passwordHint") },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="role" label={t("role")}>
            <Select
              options={["user", "admin"].map((value) => ({
                value,
                label: t(value),
              }))}
            />
          </Form.Item>
          <Form.Item name="model_ids" label={t("modelAccess")}>
            <Select
              mode="multiple"
              options={models.data?.data.map((m) => ({
                value: m.id,
                label: m.name,
              }))}
            />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={t("userAccess") + " · " + (grantUser?.username || "")}
        open={!!grantUser}
        onCancel={() => setGrantUser(null)}
        onOk={() => grantsForm.submit()}
        confirmLoading={update.isPending}
      >
        <Form
          name="user-grants"
          form={grantsForm}
          layout="vertical"
          onFinish={(values) =>
            update.mutate({ id: grantUser!.id, body: values })
          }
        >
          <Form.Item name="model_ids" label={t("modelAccess")}>
            <Select
              mode="multiple"
              options={models.data?.data.map((m) => ({
                value: m.id,
                label: m.name,
              }))}
            />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        title={t("resetPassword") + " · " + (resetUser?.username || "")}
        open={!!resetUser}
        onCancel={() => setResetUser(null)}
        onOk={() => resetForm.submit()}
        confirmLoading={update.isPending}
      >
        <Form
          name="user-reset"
          form={resetForm}
          layout="vertical"
          onFinish={(values) =>
            update.mutate({
              id: resetUser!.id,
              body: { password: values.password },
            })
          }
        >
          <Form.Item
            name="password"
            label={t("newPassword")}
            rules={[
              { required: true, message: t("required") },
              { min: 12, message: t("passwordHint") },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
        <Alert type="info" message={t("resetDone")} />
      </Modal>
    </>
  );
}
