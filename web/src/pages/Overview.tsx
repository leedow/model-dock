import { Alert, Button, Card, Progress, Space, Table, Tag } from "antd";
import {
  ArrowRightOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloudServerOutlined,
  LineChartOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import { useAuth, useRows } from "../hooks";
import { CapabilityIcon, PageHeading, QueryError, Status } from "../components";
import type { Model, Overview } from "../types";

export function OverviewPage() {
  const { t } = useTranslation();
  const { user } = useAuth();
  const overview = useQuery<Overview>({
    queryKey: ["overview"],
    queryFn: () => api("/api/v1/overview"),
    refetchInterval: 3000,
  });
  const models = useRows<Model>("/api/v1/models");
  const data = overview.data;
  const device = data?.runtime.devices[0];
  const metrics = [
    {
      key: "todayCalls",
      value: data?.calls_today ?? "—",
      icon: <LineChartOutlined aria-hidden="true" />,
      color: "purple",
      unit: "requests",
    },
    {
      key: "successRate",
      value: data ? data.success_rate + "%" : "—",
      icon: <CheckCircleOutlined aria-hidden="true" />,
      color: "green",
      unit: "success",
    },
    {
      key: "averageLatency",
      value: data?.average_ms ?? "—",
      icon: <ClockCircleOutlined aria-hidden="true" />,
      color: "blue",
      unit: "ms",
    },
    {
      key: "pendingTasks",
      value: data?.pending_tasks ?? "—",
      icon: <ThunderboltOutlined aria-hidden="true" />,
      color: "orange",
      unit: "tasks",
    },
  ];
  return (
    <>
      <PageHeading
        title={t("welcome")}
        description={t("overviewSub")}
        extra={
          <Space>
            {user?.role === "admin" && (
              <Link to="/models?create=1">
                <Button icon={<CloudServerOutlined aria-hidden="true" />}>
                  {t("newModel")}
                </Button>
              </Link>
            )}
            <Link to="/playground">
              <Button
                type="primary"
                icon={<ThunderboltOutlined aria-hidden="true" />}
              >
                {t("tryModel")}
              </Button>
            </Link>
          </Space>
        }
      />
      <QueryError error={overview.error} />
      <div className="environment-banner">
        <div className="banner-symbol">
          <CloudServerOutlined aria-hidden="true" />
        </div>
        <div>
          <strong>{t("mockNotice")}</strong>
          <p>{t("mockDescription")}</p>
        </div>
        <Tag bordered={false}>DEVELOPMENT</Tag>
      </div>
      <div className="metrics-grid">
        {metrics.map((metric) => (
          <Card key={metric.key} className="metric-card">
            <div className="metric-top">
              <span>{t(metric.key)}</span>
              <span className={"metric-icon " + metric.color}>
                {metric.icon}
              </span>
            </div>
            <div className="metric-value">
              {metric.value}
              <small>{metric.key === "averageLatency" ? "ms" : ""}</small>
            </div>
            <div className="metric-bottom">
              <span className="metric-dash" />{" "}
              <span>{metric.unit.toUpperCase()}</span>
              <div className={"tiny-bars " + metric.color}>
                {(data?.activity || Array(10).fill(0)).map((count, i) => (
                  <i
                    key={i}
                    style={{
                      height: count
                        ? Math.max(
                            3,
                            (count / Math.max(...(data?.activity || [1]))) * 20,
                          )
                        : 2,
                    }}
                  />
                ))}
              </div>
            </div>
          </Card>
        ))}
      </div>
      <div className="overview-grid">
        <Card
          className="fleet-card"
          title={
            <Space>
              <CloudServerOutlined aria-hidden="true" />
              {t("modelFleet")}
              <span className="count-pill">{data?.models_total || 0}</span>
            </Space>
          }
          extra={
            <Link to="/models">
              {t("viewAll")} <ArrowRightOutlined aria-hidden="true" />
            </Link>
          }
        >
          <QueryError error={models.error} />
          <Table
            size="middle"
            rowKey="id"
            loading={models.isLoading}
            dataSource={models.data?.data.slice(0, 5)}
            pagination={false}
            scroll={{ x: 560 }}
            columns={[
              {
                title: t("model"),
                key: "model",
                render: (_, m: Model) => (
                  <div className="model-cell">
                    <CapabilityIcon capability={m.capability} />
                    <div>
                      <strong>{m.name}</strong>
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
                dataIndex: "state",
                render: (value) => <Status value={value} />,
              },
              {
                title: t("actions"),
                key: "action",
                render: (_, m: Model) => (
                  <Link to={"/playground?model=" + m.name}>
                    <Button
                      type="text"
                      size="small"
                      icon={<ArrowRightOutlined aria-hidden="true" />}
                      aria-label={t("tryModel")}
                    />
                  </Link>
                ),
              },
            ]}
          />
        </Card>
        <Card
          className="resource-card"
          title={t("resources")}
          extra={
            <Tag color="purple" bordered={false}>
              {t("mock")}
            </Tag>
          }
        >
          <div className="gpu-heading">
            <div className="gpu-chip">GPU</div>
            <div>
              <strong>{t("virtualGPU")} 0</strong>
              <p>
                {device
                  ? `${(device.total_mb / 1024).toFixed(0)} GB · SIMULATED`
                  : "—"}
              </p>
            </div>
          </div>
          <div className="resource-value">
            <span>{t("allocated")}</span>
            <strong>
              {device ? (device.used_mb / 1024).toFixed(1) : "0"}{" "}
              <small>
                / {device ? (device.total_mb / 1024).toFixed(0) : "—"} GB
              </small>
            </strong>
          </div>
          <Progress
            percent={
              device ? Math.round((device.used_mb / device.total_mb) * 100) : 0
            }
            showInfo={false}
            strokeColor="#8663f5"
            trailColor="var(--progress-track)"
          />
          <div className="resource-stats">
            <div>
              <span>{t("available")}</span>
              <strong>
                {device ? (device.free_mb / 1024).toFixed(1) : "—"} GB
              </strong>
            </div>
            <div>
              <span>{t("loadedModels")}</span>
              <strong>
                {data?.models_loaded || 0}{" "}
                <small>/ {data?.models_total || 0}</small>
              </strong>
            </div>
          </div>
          <div
            className={
              "runtime-indicator " + (!data?.runtime.online ? "offline" : "")
            }
          >
            <span className="green-dot" />
            {t(data?.runtime.online ? "runtimeOnline" : "runtimeOffline")}
            <span className="runtime-pulse" />
          </div>
        </Card>
      </div>
      <div className="overview-bottom">
        <Card className="quickstart">
          <div className="quickstart-copy">
            <span className="eyebrow">BUILD WITH MODELDOCK</span>
            <h3>{t("docsTitle")}</h3>
            <p>{t("docsSub")}</p>
            <Link to="/documentation">
              {t("goDocs")} <ArrowRightOutlined aria-hidden="true" />
            </Link>
          </div>
          <pre>
            <span className="code-purple">client</span> = OpenAI(
            <br /> base_url=
            <span className="code-green">"{window.location.origin}/v1"</span>,
            <br /> api_key=<span className="code-green">"mdk_..."</span>
            <br />)
          </pre>
        </Card>
        <Card className="health-card" title={t("health")}>
          {["database", "queue", "worker"].map((key) => (
            <div className="health-row" key={key}>
              <span>{t(key)}</span>
              <span
                className={
                  "health-status " + (!data?.health[key] ? "offline" : "")
                }
              >
                <span className="green-dot" />
                {t(data?.health[key] ? "online" : "offline")}
              </span>
            </div>
          ))}
        </Card>
      </div>
      {!data?.runtime.online && data && (
        <Alert
          style={{ marginTop: 16 }}
          type="warning"
          showIcon
          message={t("runtimeOffline")}
        />
      )}
    </>
  );
}
