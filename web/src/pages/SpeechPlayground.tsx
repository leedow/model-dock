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
import { useTranslation } from "react-i18next";
import { api, json } from "../api";
import { FilePreview, Status } from "../components";
import type { Model, StoredFile, Task } from "../types";

export function SpeechPlayground({
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
  const [file, setFile] = useState<File | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const active = task && ["queued", "running"].includes(task.status);
  const local = model.adapter.startsWith("local.");
  const zip = model.adapter === "zipvoice.tts";
  const configuredVoices = model.config.voices;
  const voices = Array.isArray(configuredVoices)
    ? (configuredVoices as string[])
    : configuredVoices && typeof configuredVoices === "object"
      ? Object.keys(configuredVoices)
      : model.adapter === "edge.tts"
        ? ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural"]
        : zip
          ? ["alloy"]
          : ["zf_001"];
  useEffect(() => {
    if (!task || !["queued", "running"].includes(task.status)) return;
    let disposed = false;
    const timer = setInterval(() => {
      void api<Task>("/v1/tasks/" + task.id)
        .then((value) => {
          if (!disposed) setTask(value);
        })
        .catch((e) => {
          if (!disposed) setError(e.message);
        });
    }, 1000);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [task]);
  const submit = async (values: Record<string, unknown>) => {
    setBusy(true);
    setError("");
    setTask(null);
    try {
      let parameters = values;
      if (model.capability === "asr") {
        if (!file) throw new Error(t("uploadHint"));
        const body = new FormData();
        body.append("file", file);
        const uploaded = await api<StoredFile>("/v1/files", {
          method: "POST",
          body,
        });
        parameters = { ...values, file_id: uploaded.id };
      }
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
  return (
    <div className="playground-grid">
      <Card title={t("config")}>
        <label className="field-label">{t("selectModel")}</label>
        <Select
          aria-label={t("selectModel")}
          value={model.name}
          disabled={busy || !!active}
          onChange={onModelChange}
          style={{ width: "100%", marginBottom: 20 }}
          options={models.map((m) => ({ value: m.name, label: m.name }))}
        />
        <Form
          name={"speech-playground-" + model.id}
          form={form}
          layout="vertical"
          onFinish={submit}
          disabled={busy || !!active}
        >
          {model.capability === "asr" ? (
            <>
              <Upload
                accept=".wav,audio/wav"
                maxCount={1}
                beforeUpload={(value) => {
                  setFile(value);
                  return false;
                }}
                onRemove={() => setFile(null)}
              >
                <Button>{t("uploadHint")}</Button>
              </Upload>
              <Form.Item name="language" label={t("language")}>
                <Select
                  allowClear
                  options={(model.adapter.includes("paraformer")
                    ? ["zh"]
                    : model.adapter.includes("sensevoice")
                      ? ["auto", "zh", "en", "ja", "ko", "yue"]
                      : ["zh", "en", "ja", "ko", "yue"]
                  ).map((value) => ({ value, label: value }))}
                />
              </Form.Item>
            </>
          ) : (
            <>
              <Form.Item
                name="input"
                label={t("speechText")}
                rules={[{ required: true }]}
              >
                <Input.TextArea maxLength={4096} rows={5} />
              </Form.Item>
              <Form.Item
                name="voice"
                label={t("voice")}
                initialValue={voices[0]}
              >
                <Select
                  options={voices.map((value) => ({
                    value,
                    label: zip ? t("referenceVoice") : value,
                  }))}
                />
              </Form.Item>
              <Form.Item
                name="response_format"
                label={t("responseFormat")}
                initialValue="wav"
              >
                <Select
                  options={(model.adapter === "edge.tts"
                    ? ["wav", "mp3"]
                    : ["wav"]
                  ).map((value) => ({ value, label: value }))}
                />
              </Form.Item>
              {!zip && (
                <Form.Item name="speed" label={t("speed")} initialValue={1}>
                  <InputNumber min={0.25} max={4} step={0.25} />
                </Form.Item>
              )}
            </>
          )}
          <Button
            type="primary"
            htmlType="submit"
            loading={busy}
            disabled={!!active || (model.capability === "asr" && !file)}
          >
            {t("submitTask")}
          </Button>
        </Form>
      </Card>
      <Card title={model.name}>
        <Alert
          type="info"
          message={t(
            local
              ? "localSpeechNotice"
              : model.adapter === "edge.tts"
                ? "edgeSpeechNotice"
                : "zipSpeechNotice",
          )}
        />
        {error && <Alert type="error" message={error} />}
        {task && (
          <Space direction="vertical" style={{ width: "100%", marginTop: 16 }}>
            <Status value={task.status} />
            <code>{task.id}</code>
            {task.error && <Alert type="error" message={task.error.message} />}
            {typeof task.result?.text === "string" && (
              <p className="transcript">{task.result.text}</p>
            )}
            {((task.result?.files as StoredFile[]) || []).map((output) => (
              <FilePreview key={output.id} file={output} />
            ))}
            {active && (
              <Button
                onClick={() => {
                  void api<Task>(`/v1/tasks/${task.id}/cancel`, json("POST"))
                    .then(setTask)
                    .catch((e) => setError(e.message));
                }}
              >
                {t("cancel")}
              </Button>
            )}
          </Space>
        )}
      </Card>
    </div>
  );
}
