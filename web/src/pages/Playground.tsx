import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  Alert,
  Avatar,
  Button,
  Card,
  Input,
  InputNumber,
  Select,
  Slider,
  Space,
  Spin,
  Switch,
  Tag,
  Upload,
  message,
} from "antd";
import {
  ArrowUpOutlined,
  ClearOutlined,
  CodeOutlined,
  InboxOutlined,
  RobotOutlined,
  StopOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import { api, APIError, consumeSSE, json, raw } from "../api";
import {
  CapabilityIcon,
  FilePreview,
  PageHeading,
  QueryError,
} from "../components";
import { useRows } from "../hooks";
import type { Capability, Model, StoredFile, Task } from "../types";

import { RemotePlayground } from "./RemotePlayground";
import { SpeechPlayground } from "./SpeechPlayground";

export function PlaygroundPage() {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const models = useRows<Model>("/api/v1/models");
  const [cap, setCap] = useState<Capability>("chat"),
    [model, setModel] = useState("");
  const [streaming, setStreaming] = useState(true);
  const [providerDefaults, setProviderDefaults] = useState(true);
  const [input, setInput] = useState(""),
    [temperature, setTemperature] = useState(0.7),
    [maxTokens, setMaxTokens] = useState(256),
    [size, setSize] = useState("512x512"),
    [voice, setVoice] = useState("alloy"),
    [speed, setSpeed] = useState(1),
    [asynchronous, setAsynchronous] = useState(false),
    [file, setFile] = useState<File | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [history, setHistory] = useState<
      { role: "user" | "assistant"; content: string }[]
    >([]),
    [output, setOutput] = useState<StoredFile[]>([]),
    [transcript, setTranscript] = useState(""),
    [task, setTask] = useState<Task | null>(null);
  const controller = useRef<AbortController | null>(null),
    bottom = useRef<HTMLDivElement>(null);
  const available =
    models.data?.data.filter((m) => m.enabled && m.capability === cap) || [];
  useEffect(() => {
    if (busy) return;
    const requestedName = searchParams.get("model");
    if (requestedName) {
      const requested = models.data?.data.find(
        (m) => m.name === requestedName && m.enabled,
      );
      if (requested) {
        setCap(requested.capability);
        setModel(requested.name);
      } else {
        setModel("");
      }
      return;
    }
    const available =
      models.data?.data.filter((m) => m.enabled && m.capability === cap) || [];
    if (!available.some((m) => m.name === model))
      setModel(available[0]?.name || "");
  }, [cap, models.data?.data, model, searchParams, busy]);
  useEffect(() => {
    const panel = bottom.current?.parentElement;
    if (history.length && panel)
      panel.scrollTo({ top: panel.scrollHeight, behavior: "smooth" });
  }, [history]);
  useEffect(() => () => controller.current?.abort(), []);
  const selected = available.find((m) => m.name === model);
  const reset = () => {
    setHistory([]);
    setOutput([]);
    setTranscript("");
    setTask(null);
    setError("");
  };
  const run = async () => {
    if (!selected || busy) return;
    setBusy(true);
    setError("");
    setOutput([]);
    setTranscript("");
    setTask(null);
    controller.current = new AbortController();
    const signal = controller.current.signal;
    try {
      if (cap === "chat") {
        if (!input.trim()) return;
        const messages = [
          ...history,
          { role: "user" as const, content: input },
        ];
        setHistory([...messages, { role: "assistant", content: "" }]);
        setInput("");
        const response = await raw(
          "/v1/chat/completions",
          {
            model,
            messages,
            ...(!providerDefaults ? { temperature } : {}),
            max_tokens: maxTokens,
            stream: streaming,
          },
          signal,
        );
        if (!streaming) {
          const data = await response.json();
          setHistory([
            ...messages,
            { role: "assistant", content: data.choices[0].message.content },
          ]);
        } else {
          const reader = response.body!.getReader();
          await consumeSSE(reader, (text) =>
            setHistory((old) => {
              const next = [...old];
              next[next.length - 1] = {
                ...next[next.length - 1],
                content: next[next.length - 1].content + text,
              };
              return next;
            }),
          );
        }
      } else {
        let parameters: Record<string, unknown> = {};
        if (cap === "image") parameters = { prompt: input, size, n: 1 };
        if (cap === "tts")
          parameters = { input, voice, speed, response_format: "wav" };
        if (cap === "asr") {
          if (!file) throw new Error(t("uploadHint"));
          if (asynchronous) {
            const form = new FormData();
            form.append("file", file);
            const uploaded = await api<StoredFile>("/v1/files", {
              method: "POST",
              body: form,
            });
            parameters = { file_id: uploaded.id };
          }
        }
        if (asynchronous) {
          setTask(
            await api<Task>(
              "/v1/tasks",
              json("POST", { model, capability: cap, parameters }),
            ),
          );
          void message.success(t("taskSubmitted"));
        } else if (cap === "image") {
          const response = await api(
            "/v1/images/generations",
            json("POST", { model, ...parameters }),
          );
          setOutput(
            await Promise.all(
              response.data.map((item: { file_id: string }) =>
                api<StoredFile>("/v1/files/" + item.file_id),
              ),
            ),
          );
        } else if (cap === "tts") {
          const response = await raw(
            "/v1/audio/speech",
            { model, ...parameters },
            signal,
          );
          await response.arrayBuffer();
          setOutput([
            await api<StoredFile>(
              "/v1/files/" + response.headers.get("x-file-id"),
            ),
          ]);
        } else {
          const form = new FormData();
          form.append("model", model);
          form.append("file", file!);
          const response = await api("/v1/audio/transcriptions", {
            method: "POST",
            body: form,
            signal,
          });
          setTranscript(response.text);
        }
      }
    } catch (error) {
      if ((error as Error).name !== "AbortError")
        setError(
          error instanceof APIError
            ? t(`providerError_${error.code}`, { defaultValue: error.message })
            : (error as Error).message,
        );
    } finally {
      setBusy(false);
      controller.current = null;
    }
  };
  return (
    <>
      <PageHeading
        title={t("playground")}
        description={t("playgroundDescription")}
      />
      <QueryError error={models.error} />
      <div className="capability-tabs">
        {(
          ["chat", "image", "tts", "asr", "video", "subtitle"] as Capability[]
        ).map((value) => (
          <button
            key={value}
            disabled={busy}
            onClick={() => {
              setSearchParams({});
              setCap(value);
              reset();
              setInput("");
            }}
            className={cap === value ? "selected" : ""}
          >
            <CapabilityIcon capability={value} />
            <span>{t(value)}</span>
            <span className="tab-arrow">↗</span>
          </button>
        ))}
      </div>
      {selected &&
      [
        "local.sensevoice.asr",
        "local.paraformer.asr",
        "local.faster_whisper.asr",
        "local.kokoro.tts",
        "zipvoice.tts",
        "edge.tts",
      ].includes(selected.adapter) ? (
        <SpeechPlayground
          key={selected.id}
          model={selected}
          models={available}
          onModelChange={(name) => {
            setSearchParams({});
            setModel(name);
          }}
        />
      ) : selected &&
        (selected.adapter.startsWith("runninghub.") ||
          selected.adapter.startsWith("comfyui.") ||
          ["volcengine.tts", "whisperx.subtitle"].includes(selected.adapter)) &&
        cap !== "chat" ? (
        <RemotePlayground
          key={selected.id}
          model={selected}
          models={available}
          onModelChange={(name) => {
            setSearchParams({});
            setModel(name);
          }}
        />
      ) : (
        <div className="playground-grid">
          <Card
            className="parameter-panel"
            title={
              <Space>
                <CodeOutlined aria-hidden="true" />
                {t("config").split(" (")[0]}
              </Space>
            }
          >
            <label className="field-label">{t("selectModel")}</label>
            <Select
              style={{ width: "100%" }}
              value={model || undefined}
              placeholder={t("selectModel")}
              disabled={busy}
              onChange={(value) => {
                setSearchParams({});
                setModel(value);
                reset();
              }}
              options={available.map((m) => ({ value: m.name, label: m.name }))}
            />
            {!model && (
              <Alert
                type="info"
                message={t("modelEmpty")}
                style={{ marginTop: 16 }}
              />
            )}
            {selected && (
              <div className="model-summary">
                <Tag color="purple">{selected.adapter}</Tag>
                <div>
                  <span>{t("state")}</span>
                  <span>{t(selected.state)}</span>
                </div>
                <div>
                  <span>{t("device")}</span>
                  <code>{selected.device}</code>
                </div>
              </div>
            )}
            {cap === "chat" ? (
              <>
                <div className="mode-switch">
                  <span>{t("streamingOutput")}</span>
                  <Switch
                    aria-label={t("streamingOutput")}
                    checked={streaming}
                    onChange={setStreaming}
                    disabled={busy}
                  />
                </div>
                <div className="mode-switch">
                  <span>{t("providerSamplingDefaults")}</span>
                  <Switch
                    aria-label={t("providerSamplingDefaults")}
                    checked={providerDefaults}
                    onChange={setProviderDefaults}
                    disabled={busy}
                  />
                </div>
                <label className="field-label">
                  {t("temperature")}
                  <strong>{temperature}</strong>
                </label>
                <Slider
                  value={temperature}
                  min={0}
                  max={2}
                  step={0.1}
                  onChange={setTemperature}
                  disabled={busy || providerDefaults}
                />
                <label className="field-label">{t("maxTokens")}</label>
                <InputNumber
                  style={{ width: "100%" }}
                  value={maxTokens}
                  min={1}
                  max={8192}
                  onChange={(value) => setMaxTokens(value || 256)}
                  disabled={busy}
                />
              </>
            ) : (
              <>
                <div className="mode-switch">
                  <span>{t("async")}</span>
                  <Switch
                    checked={asynchronous}
                    onChange={setAsynchronous}
                    disabled={busy}
                  />
                </div>
                {cap === "image" && (
                  <>
                    <label className="field-label">{t("imageSize")}</label>
                    <Select
                      value={size}
                      style={{ width: "100%" }}
                      onChange={setSize}
                      disabled={busy}
                      options={["256x256", "512x512", "1024x1024"].map(
                        (value) => ({ value, label: value }),
                      )}
                    />
                  </>
                )}
                {cap === "tts" && (
                  <>
                    <label className="field-label">{t("voice")}</label>
                    <Select
                      value={voice}
                      onChange={setVoice}
                      style={{ width: "100%" }}
                      disabled={busy}
                      options={["alloy", "echo", "nova"].map((value) => ({
                        value,
                        label: value,
                      }))}
                    />
                    <label className="field-label">
                      {t("speed")}
                      <strong>{speed}×</strong>
                    </label>
                    <Slider
                      value={speed}
                      min={0.25}
                      max={4}
                      step={0.25}
                      onChange={setSpeed}
                      disabled={busy}
                    />
                    <Alert
                      type="info"
                      message={t("mockAudio")}
                      style={{ marginTop: 16 }}
                    />
                  </>
                )}
              </>
            )}
            <div className="panel-tip">
              <ThunderboltOutlined aria-hidden="true" />
              <p>{t("grantHint")}</p>
            </div>
          </Card>
          <Card
            className="output-panel"
            title={
              <Space>
                <span className="green-dot" />
                {model || t("output")}
                {selected?.adapter.startsWith("mock.") && (
                  <Tag bordered={false}>{t("mockOutput")}</Tag>
                )}
              </Space>
            }
            extra={
              <Button
                type="text"
                icon={<ClearOutlined aria-hidden="true" />}
                onClick={reset}
                disabled={busy}
              >
                {t("clear")}
              </Button>
            }
          >
            <div
              className={"output-body " + (cap === "chat" ? "chat-output" : "")}
            >
              {cap === "chat" ? (
                history.length ? (
                  history.map((msg, i) => (
                    <div key={i} className={"chat-message " + msg.role}>
                      <Avatar
                        size={30}
                        icon={
                          msg.role === "user" ? (
                            <UserOutlined aria-hidden="true" />
                          ) : (
                            <RobotOutlined aria-hidden="true" />
                          )
                        }
                      />
                      <div>
                        <strong>
                          {msg.role === "user" ? "You" : "ModelDock"}
                          {msg.role === "assistant" &&
                            selected?.adapter.startsWith("mock.") && (
                              <Tag bordered={false}>{t("mock")}</Tag>
                            )}
                        </strong>
                        <div className="chat-text">
                          {msg.content || <Spin size="small" />}
                        </div>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="playground-empty">
                    <div className="empty-logo">
                      <ThunderboltOutlined aria-hidden="true" />
                    </div>
                    <h3>{t("testHint")}</h3>
                    <button
                      className="sample-prompt"
                      onClick={() => setInput(t("samplePrompt"))}
                    >
                      {t("samplePrompt")} <ArrowUpOutlined aria-hidden="true" />
                    </button>
                  </div>
                )
              ) : output.length ? (
                <div className="outputs">
                  {output.map((f) => (
                    <FilePreview key={f.id} file={f} />
                  ))}
                </div>
              ) : transcript ? (
                <div className="transcript">
                  <Tag color="purple">MOCK ASR</Tag>
                  <p>{transcript}</p>
                </div>
              ) : task ? (
                <div className="task-submitted">
                  <ThunderboltOutlined aria-hidden="true" />
                  <h3>{t("taskSubmitted")}</h3>
                  <code>{task.id}</code>
                  <Link to="/tasks">
                    <Button type="primary">{t("tasks")} →</Button>
                  </Link>
                </div>
              ) : (
                <div className="playground-empty">
                  <CapabilityIcon capability={cap} />
                  <h3>{t("testHint")}</h3>
                </div>
              )}
              <div ref={bottom} />
            </div>
            <div className="composer">
              {error && (
                <Alert
                  type="error"
                  showIcon
                  closable
                  onClose={() => setError("")}
                  message={error}
                  style={{ marginBottom: 12 }}
                />
              )}
              {cap === "asr" ? (
                <Upload.Dragger
                  accept=".wav,audio/wav"
                  maxCount={1}
                  disabled={busy}
                  beforeUpload={(value) => {
                    setFile(value);
                    return false;
                  }}
                  onRemove={() => setFile(null)}
                >
                  <InboxOutlined
                    aria-hidden="true"
                    style={{ fontSize: 28, color: "#8663f5" }}
                  />
                  <p>{t("uploadHint")}</p>
                </Upload.Dragger>
              ) : (
                <Input.TextArea
                  aria-label={t("prompt")}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder={t(
                    cap === "image"
                      ? "imagePrompt"
                      : cap === "tts"
                        ? "ttsPrompt"
                        : "promptPlaceholder",
                  )}
                  autoSize={{ minRows: 2, maxRows: 6 }}
                  disabled={busy}
                  onKeyDown={(e) => {
                    if (
                      e.key === "Enter" &&
                      !e.shiftKey &&
                      cap === "chat" &&
                      !e.nativeEvent.isComposing
                    ) {
                      e.preventDefault();
                      void run();
                    }
                  }}
                />
              )}
              <div className="composer-footer">
                <span>
                  {cap === "chat"
                    ? "↵ Enter · ⇧ Shift + Enter"
                    : t(asynchronous ? "async" : "sync")}
                </span>
                {busy && cap === "chat" ? (
                  <Button
                    icon={<StopOutlined aria-hidden="true" />}
                    onClick={() => controller.current?.abort()}
                  >
                    {t("stop")}
                  </Button>
                ) : (
                  <Button
                    type="primary"
                    icon={<ArrowUpOutlined aria-hidden="true" />}
                    loading={busy}
                    disabled={
                      !selected || (cap === "asr" ? !file : !input.trim())
                    }
                    onClick={() => void run()}
                  >
                    {t(asynchronous && cap !== "chat" ? "submitTask" : "send")}
                  </Button>
                )}
              </div>
            </div>
          </Card>
        </div>
      )}
    </>
  );
}
