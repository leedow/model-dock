import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Upload,
} from "antd";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api, json } from "../api";
import { useRows } from "../hooks";
import { FilePreview, Status } from "../components";
import type { Model, StoredFile, Task } from "../types";

function FileInput({
  value,
  onChange,
  kind,
  label,
  id,
}: {
  value?: string;
  onChange?: (id: string) => void;
  kind: string;
  label: string;
  id?: string;
}) {
  const { t } = useTranslation();
  const files = useRows<StoredFile>("/v1/files", {}, 3000);
  const [error, setError] = useState("");
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Select
        id={id}
        aria-label={label}
        value={value}
        onChange={onChange}
        style={{ width: "100%" }}
        options={files.data?.data
          .filter((f) => f.media_type.startsWith(kind + "/"))
          .map((f) => ({ value: f.id, label: f.filename }))}
      />
      <Upload
        maxCount={1}
        accept={
          kind === "image"
            ? ".png,.jpg,.jpeg,.webp"
            : kind === "video"
              ? ".mp4"
              : kind === "audio"
                ? ".wav,.mp3,.flac"
                : ".txt"
        }
        beforeUpload={async (file) => {
          const body = new FormData();
          body.append("file", file);
          try {
            const stored = await api<StoredFile>("/v1/files", {
              method: "POST",
              body,
            });
            onChange?.(stored.id);
            setError("");
          } catch (e) {
            setError((e as Error).message);
          }
          return false;
        }}
      >
        <Button>{t("upload")}</Button>
      </Upload>
      {error && <Alert type="error" message={error} />}
    </Space>
  );
}

export function RemotePlayground({
  model,
  models,
  onModelChange,
}: {
  model: Model;
  models: Model[];
  onModelChange: (name: string) => void;
}) {
  const { t } = useTranslation();
  const [form] = Form.useForm();
  const ops = model.operations || [];
  const operation = Form.useWatch("operation", form) || ops[0];
  const [task, setTask] = useState<Task | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [textSource, setTextSource] = useState("text");
  useEffect(() => {
    form.resetFields();
    setTask(null);
    setError("");
  }, [model.id, form]);
  useEffect(() => {
    if (!task || !["queued", "running"].includes(task.status)) return;
    const timer = setInterval(() => {
      void api<Task>("/v1/tasks/" + task.id)
        .then(setTask)
        .catch((e) => setError(e.message));
    }, 1000);
    return () => clearInterval(timer);
  }, [task]);
  const submit = async (values: Record<string, unknown>) => {
    setBusy(true);
    setError("");
    setTask(null);
    const parameters = { ...values };
    if (["tts", "subtitle"].includes(model.capability))
      delete parameters.operation;
    try {
      setTask(
        await api<Task>(
          "/v1/tasks",
          json("POST", {
            model: model.name,
            capability: model.capability,
            parameters,
          }),
        ),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const file = (key: string, kind: string) => (
    <Form.Item
      key={key}
      name={key}
      label={t(key)}
      rules={[{ required: true }]}
      preserve={false}
    >
      <FileInput kind={kind} label={t(key)} />
    </Form.Item>
  );
  const number = (key: string, initial: number, min: number, max: number) => (
    <Form.Item
      key={key}
      name={key}
      label={t(key)}
      initialValue={initial}
      preserve={false}
    >
      <InputNumber min={min} max={max} step={0.1} />
    </Form.Item>
  );
  const workflow =
    model.adapter.includes("comfyui") || model.adapter.includes("workflow");
  const config = model.config;
  const bindings = (config.bindings || {}) as Record<string, unknown>;
  const needsPrompt =
    model.capability === "image" ||
    (!workflow && model.capability === "video") ||
    "prompt" in bindings;
  return (
    <div className="playground-grid">
      <Card title={t("providerParameters")}>
        <label className="field-label">{t("selectModel")}</label>
        <Select
          aria-label={t("selectModel")}
          value={model.name}
          onChange={onModelChange}
          disabled={!!task && ["queued", "running"].includes(task.status)}
          style={{ width: "100%", marginBottom: 16 }}
          options={models.map((item) => ({
            value: item.name,
            label: item.name,
          }))}
        />
        <Form
          name={"remote-playground-" + model.id}
          form={form}
          layout="vertical"
          onValuesChange={(changes) => {
            if ("operation" in changes) setTask(null);
          }}
          disabled={!!task && ["queued", "running"].includes(task.status)}
          onFinish={submit}
          initialValues={{ operation: ops[0] }}
        >
          <Form.Item name="operation" label={t("operation")}>
            <Select
              options={ops.map((value) => ({ value, label: t(value) }))}
            />
          </Form.Item>
          {((model.capability === "video" && operation !== "upscale") ||
            operation === "edit") &&
            file("image_file_id", "image")}
          {operation === "start_end" && file("end_image_file_id", "image")}
          {operation === "lip_sync" && (
            <>
              {file("audio_file_id", "audio")}
              {"audio_start" in bindings && number("audio_start", 0, 0, 100000)}
              {"audio_end" in bindings && (
                <Form.Item
                  name="audio_end"
                  label={t("audio_end")}
                  preserve={false}
                >
                  <InputNumber min={0.01} />
                </Form.Item>
              )}
            </>
          )}
          {operation === "upscale" && (
            <>
              {file("video_file_id", "video")}
              {number("scale", 2, 1, 8)}
            </>
          )}
          {needsPrompt && (
            <Form.Item
              name="prompt"
              label={t("prompt")}
              rules={[{ required: model.capability === "image" }]}
              preserve={false}
            >
              <Input.TextArea rows={4} />
            </Form.Item>
          )}
          {!workflow && ["image", "video"].includes(model.capability) && (
            <>
              {[
                "aspect_ratio",
                "resolution",
                ...(model.capability === "video" ? ["duration"] : []),
              ].map((key) => {
                const values = (config[
                  key === "aspect_ratio" ? "aspect_ratios" : key + "s"
                ] || []) as (string | number)[];
                return (
                  <Form.Item
                    key={key}
                    name={key}
                    label={t(key)}
                    preserve={false}
                  >
                    <Select
                      allowClear
                      options={values.map((value) => ({
                        value,
                        label: String(value),
                      }))}
                    />
                  </Form.Item>
                );
              })}
            </>
          )}
          {model.capability === "tts" && (
            <>
              <Form.Item
                name="input"
                label={t("providerSpeechText")}
                rules={[{ required: true }]}
                preserve={false}
              >
                <Input.TextArea rows={5} maxLength={100000} />
              </Form.Item>
              <Form.Item
                name="voice"
                label={t("voice")}
                initialValue={(config.voices as string[])?.[0]}
                preserve={false}
              >
                <Select
                  options={((config.voices as string[]) || []).map((value) => ({
                    value,
                    label: value,
                  }))}
                />
              </Form.Item>
              <Form.Item
                name="response_format"
                label={t("responseFormat")}
                initialValue={(config.formats as string[])?.[0]}
                preserve={false}
              >
                <Select
                  options={((config.formats as string[]) || []).map(
                    (value) => ({ value, label: value }),
                  )}
                />
              </Form.Item>
              {number("speed", 1, 0.5, 2)}
              {number("volume", 1, 0.5, 2)}
              {number("pitch", 0, -12, 12)}
            </>
          )}
          {model.capability === "subtitle" && (
            <>
              {file("file_id", "audio")}
              <Select
                aria-label={t("textSource")}
                value={textSource}
                onChange={setTextSource}
                options={[
                  { value: "text", label: t("subtitleText") },
                  { value: "file", label: t("selectFile") },
                ]}
              />
              {textSource === "file" ? (
                file("text_file_id", "text")
              ) : (
                <Form.Item
                  name="text"
                  label={t("subtitleText")}
                  rules={[{ required: true }]}
                  preserve={false}
                >
                  <Input.TextArea rows={5} />
                </Form.Item>
              )}
            </>
          )}
          <Button
            htmlType="submit"
            type="primary"
            loading={busy}
            disabled={!!task && ["queued", "running"].includes(task.status)}
          >
            {t("submitTask")}
          </Button>
        </Form>
      </Card>
      <Card title={model.name}>
        <Alert type="info" message={t("remoteNotice")} />
        {error && <Alert type="error" message={error} />}
        {task && (
          <Space direction="vertical" style={{ width: "100%" }}>
            <Status value={task.status} />
            <code>{task.id}</code>
            {task.error && <Alert type="error" message={task.error.message} />}
            {task.execution_metadata?.cancellation && (
              <Alert
                type="warning"
                message={t("cancelNotice")}
                description={task.execution_metadata.cancellation}
              />
            )}
            <pre>{JSON.stringify(task.execution_metadata || {}, null, 2)}</pre>
            {((task.result?.files as StoredFile[]) || []).map((f) => (
              <FilePreview key={f.id} file={f} />
            ))}
            {["queued", "running"].includes(task.status) ? (
              <Button
                onClick={() => {
                  void api<Task>(`/v1/tasks/${task.id}/cancel`, json("POST"))
                    .then(setTask)
                    .catch((e) => setError(e.message));
                }}
              >
                {t("cancel")}
              </Button>
            ) : (
              ["failed", "cancelled"].includes(task.status) && (
                <Button
                  onClick={() => {
                    void api<Task>(`/v1/tasks/${task.id}/retry`, json("POST"))
                      .then(setTask)
                      .catch((e) => setError(e.message));
                  }}
                >
                  {t("retry")}
                </Button>
              )
            )}
            <Link to="/tasks">{t("tasks")}</Link>
          </Space>
        )}
      </Card>
    </div>
  );
}
