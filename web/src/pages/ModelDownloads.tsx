import { useState } from "react";
import { Alert, Button, Card, Drawer, Progress, Space, Tag } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import { api, json } from "../api";
import { useAction, useRows } from "../hooks";
import { QueryError, Status } from "../components";

interface Preset {
  id: string;
  name: string;
  repo: string;
  capability: string;
}
interface Download {
  id: string;
  preset: string;
  status: string;
  downloaded_bytes: number;
  total_bytes: number;
  current_file: string | null;
  revision: string | null;
  error_code: string | null;
}
const size = (bytes: number) => (bytes / 1024 / 1024).toFixed(1) + " MB";

function DownloadPanel() {
  const { t } = useTranslation();
  const catalog = useRows<Preset>("/api/v1/model-downloads/catalog", {}, 0);
  const downloads = useRows<Download>("/api/v1/model-downloads", {}, 1000);
  const start = useAction((preset: string) =>
    api("/api/v1/model-downloads", json("POST", { preset })),
  );
  const action = useAction(
    ({ id, operation }: { id: string; operation: string }) =>
      api(`/api/v1/model-downloads/${id}/${operation}`, json("POST")),
  );
  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Alert type="info" showIcon message={t("downloadHint")} />
      <QueryError error={catalog.error || downloads.error} />
      {catalog.data?.data.map((preset) => (
        <Card
          key={preset.id}
          size="small"
          title={preset.name}
          extra={<Tag>{t(preset.capability)}</Tag>}
        >
          <p>
            <a
              href={"https://huggingface.co/" + preset.repo}
              target="_blank"
              rel="noreferrer"
            >
              {preset.repo}
            </a>
          </p>
          <Button
            onClick={() => start.mutate(preset.id)}
            loading={start.isPending}
            disabled={downloads.data?.data.some(
              (d) =>
                d.preset === preset.id &&
                ["queued", "running"].includes(d.status),
            )}
          >
            {t("downloadModel")}
          </Button>
        </Card>
      ))}
      <h3>{t("downloadHistory")}</h3>
      {downloads.data?.data.length === 0 && <p>{t("noData")}</p>}
      {downloads.data?.data.map((download) => (
        <Card
          key={download.id}
          size="small"
          title={
            catalog.data?.data.find((p) => p.id === download.preset)?.name ||
            download.preset
          }
          extra={<Status value={download.status} />}
        >
          <Progress
            percent={
              download.total_bytes
                ? Math.floor(
                    (download.downloaded_bytes / download.total_bytes) * 100,
                  )
                : 0
            }
            status={
              download.status === "failed"
                ? "exception"
                : download.status === "succeeded"
                  ? "success"
                  : download.status === "running"
                    ? "active"
                    : "normal"
            }
          />
          <p>
            {size(download.downloaded_bytes)} /{" "}
            {download.total_bytes ? size(download.total_bytes) : "…"}
          </p>
          {download.current_file && (
            <p>
              <code>{download.current_file}</code>
            </p>
          )}
          {download.revision && (
            <p className="small muted">
              {t("modelRevision")}: {download.revision.slice(0, 12)}
            </p>
          )}
          {download.error_code && (
            <Alert
              type="error"
              message={t("downloadErrors." + download.error_code, {
                defaultValue: t("downloadErrors.download_failed"),
              })}
            />
          )}
          <Space style={{ marginTop: 12 }}>
            {["queued", "running"].includes(download.status) && (
              <Button
                loading={action.isPending}
                onClick={() =>
                  action.mutate({ id: download.id, operation: "cancel" })
                }
              >
                {t("cancel")}
              </Button>
            )}
            {["failed", "cancelled"].includes(download.status) && (
              <Button
                loading={start.isPending}
                onClick={() => start.mutate(download.preset)}
              >
                {t("retry")}
              </Button>
            )}
            {download.status === "succeeded" && (
              <Button
                type="primary"
                loading={action.isPending}
                onClick={() =>
                  action.mutate({ id: download.id, operation: "register" })
                }
              >
                {t("registerDownloadedModel")}
              </Button>
            )}
          </Space>
        </Card>
      ))}
    </Space>
  );
}

export function ModelDownloads() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        icon={<DownloadOutlined aria-hidden="true" />}
        onClick={() => setOpen(true)}
      >
        {t("modelDownloads")}
      </Button>
      <Drawer
        aria-label={t("modelDownloads")}
        title={t("modelDownloads")}
        open={open}
        width={640}
        onClose={() => setOpen(false)}
        destroyOnClose
      >
        {open && <DownloadPanel />}
      </Drawer>
    </>
  );
}
