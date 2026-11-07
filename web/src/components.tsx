import { useEffect, useState, type ReactNode } from "react";
import { Alert, Button, Empty, Space, Tag, Tooltip, Typography } from "antd";
import {
  CopyOutlined,
  DownloadOutlined,
  FileImageOutlined,
  AudioOutlined,
  MessageOutlined,
  SoundOutlined,
  VideoCameraOutlined,
  FileTextOutlined,
} from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import type { Capability, StoredFile } from "./types";

export const capIcons = {
  video: <VideoCameraOutlined aria-hidden="true" />,
  subtitle: <FileTextOutlined aria-hidden="true" />,
  chat: <MessageOutlined aria-hidden="true" />,
  image: <FileImageOutlined aria-hidden="true" />,
  tts: <SoundOutlined aria-hidden="true" />,
  asr: <AudioOutlined aria-hidden="true" />,
};
export function CapabilityIcon({ capability }: { capability: Capability }) {
  return (
    <span className={"cap-icon " + capability}>{capIcons[capability]}</span>
  );
}
export function Status({ value }: { value: string }) {
  const { t } = useTranslation();
  const colors: Record<string, string> = {
    ready: "green",
    succeeded: "green",
    enabled: "green",
    running: "blue",
    loading: "blue",
    unloading: "orange",
    queued: "gold",
    failed: "red",
    error: "red",
    disabled: "default",
    cancelled: "default",
    unloaded: "default",
    rejected: "orange",
  };
  return (
    <Tag className="status-tag" color={colors[value]}>
      <span className={"status-dot " + value} />
      {t(value)}
    </Tag>
  );
}
export function PageHeading({
  title,
  description,
  extra,
}: {
  title: string;
  description?: string;
  extra?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {extra && <div>{extra}</div>}
    </div>
  );
}
export function QueryError({ error }: { error: Error | null }) {
  return error ? (
    <Alert
      type="error"
      showIcon
      message={error.message}
      className="query-error"
    />
  ) : null;
}
export function Blank({ text }: { text?: string }) {
  const { t } = useTranslation();
  return (
    <Empty
      image={Empty.PRESENTED_IMAGE_SIMPLE}
      description={text || t("noData")}
    />
  );
}
export function Copy({ text }: { text: string }) {
  const { t } = useTranslation();
  return (
    <Tooltip title={t("copy")}>
      <Typography.Text
        copyable={{
          text,
          icon: [
            <CopyOutlined aria-hidden="true" key="a" />,
            <CopyOutlined aria-hidden="true" key="b" />,
          ],
        }}
      />
    </Tooltip>
  );
}
export function FilePreview({
  file,
  compact = false,
}: {
  file: StoredFile;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState("");
  useEffect(() => {
    if (file.media_type.startsWith("text/")) {
      void fetch(file.url, { credentials: "same-origin" })
        .then((response) => {
          if (!response.ok) throw new Error();
          return response.text();
        })
        .then((value) => setText(value))
        .catch(() => setText(t("error")));
    }
  }, [file.url, file.media_type, t]);
  return (
    <div className={"file-preview " + (compact ? "compact" : "")}>
      {file.media_type.startsWith("image/") ? (
        <img src={file.url} alt={file.filename} />
      ) : file.media_type.startsWith("video/") ? (
        <video controls src={file.url} style={{ width: "100%" }} />
      ) : file.media_type.startsWith("text/") ? (
        <pre style={{ whiteSpace: "pre-wrap" }}>{text}</pre>
      ) : file.media_type.startsWith("audio/") ? (
        <>
          <div className="audio-art">
            <SoundOutlined aria-hidden="true" />
          </div>
          <audio controls src={file.url} />
        </>
      ) : (
        <Blank />
      )}
      <Space className="file-caption">
        <span>{file.filename}</span>
        <a href={file.url} download={file.filename}>
          <Button size="small" icon={<DownloadOutlined aria-hidden="true" />}>
            {t("download")}
          </Button>
        </a>
      </Space>
    </div>
  );
}
export function CodeBlock({
  code,
  language = "python",
}: {
  code: string;
  language?: string;
}) {
  return (
    <div className="code-block">
      <div className="code-top">
        <span>{language}</span>
        <Copy text={code} />
      </div>
      <pre>{code}</pre>
    </div>
  );
}
