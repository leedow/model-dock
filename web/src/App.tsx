import { ProvidersPage } from "./pages/Providers";
import { useEffect, useState } from "react";
import {
  BrowserRouter,
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import {
  Alert,
  Avatar,
  Button,
  ConfigProvider,
  Dropdown,
  Form,
  Input,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  theme,
} from "antd";
import zhCN from "antd/locale/zh_CN";
import enUS from "antd/locale/en_US";
import {
  ApiOutlined,
  AppstoreOutlined,
  AudioOutlined,
  BookOutlined,
  CloudServerOutlined,
  CodeOutlined,
  DatabaseOutlined,
  FileOutlined,
  GlobalOutlined,
  KeyOutlined,
  LogoutOutlined,
  MenuOutlined,
  SettingOutlined,
  SunOutlined,
  MoonOutlined,
  TeamOutlined,
  ThunderboltOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import { useTranslation } from "react-i18next";
import { useAuth } from "./hooks";
import { OverviewPage } from "./pages/Overview";
import { ModelsPage } from "./pages/Models";
import { PlaygroundPage } from "./pages/Playground";
import {
  FilesPage,
  KeysPage,
  LogsPage,
  TasksPage,
  UsersPage,
} from "./pages/Operations";
import { DocsPage, SettingsPage } from "./pages/Documentation";

const navigation = [
  { key: "overview", path: "/", icon: <AppstoreOutlined aria-hidden="true" /> },
  {
    key: "models",
    path: "/models",
    icon: <CloudServerOutlined aria-hidden="true" />,
  },
  {
    key: "playground",
    path: "/playground",
    icon: <CodeOutlined aria-hidden="true" />,
  },
  {
    key: "tasks",
    path: "/tasks",
    icon: <UnorderedListOutlined aria-hidden="true" />,
  },
  { key: "files", path: "/files", icon: <FileOutlined aria-hidden="true" /> },
  { key: "keys", path: "/keys", icon: <KeyOutlined aria-hidden="true" /> },
  { key: "logs", path: "/logs", icon: <DatabaseOutlined aria-hidden="true" /> },
  {
    key: "providers",
    path: "/providers",
    icon: <ApiOutlined aria-hidden="true" />,
  },
  { key: "users", path: "/users", icon: <TeamOutlined aria-hidden="true" /> },
  {
    key: "docs",
    path: "/documentation",
    icon: <BookOutlined aria-hidden="true" />,
  },
];
function Brand() {
  return (
    <Link to="/" className="brand">
      <span className="brand-mark">
        <ApiOutlined aria-hidden="true" />
      </span>
      <span>
        Model<span className="brand-light">Dock</span>
        <sup>LOCAL</sup>
      </span>
    </Link>
  );
}
function Login() {
  const { t } = useTranslation();
  const { login } = useAuth();
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <div className="login">
      <section className="login-story">
        <Brand />
        <div className="login-pitch">
          <Tag color="purple">SELF-HOSTED AI INFRASTRUCTURE</Tag>
          <h1>{t("loginTitle")}</h1>
          <p>{t("loginSub")}</p>
          <div className="model-orbit">
            <div className="orbit-ring r1" />
            <div className="orbit-ring r2" />
            <div className="orbit-core">
              <ApiOutlined aria-hidden="true" />
            </div>
            <span className="orbit-node n1">
              <CodeOutlined aria-hidden="true" />
            </span>
            <span className="orbit-node n2">
              <AudioOutlined aria-hidden="true" />
            </span>
            <span className="orbit-node n3">
              <ThunderboltOutlined aria-hidden="true" />
            </span>
          </div>
          <div className="story-features">
            <span>OpenAI compatible</span>
            <span>Adapter powered</span>
            <span>Locally owned</span>
          </div>
        </div>
        <small>MODE L D O C K / YOUR MODELS. YOUR INFRASTRUCTURE.</small>
      </section>
      <section className="login-form">
        <div className="login-card">
          <span className="eyebrow">WELCOME TO YOUR WORKSPACE</span>
          <h2>{t("loginWelcome")}</h2>
          <p>{t("loginCaption")}</p>
          {error && <Alert showIcon type="error" message={error} />}
          <Form
            layout="vertical"
            onFinish={async (values) => {
              setBusy(true);
              setError("");
              try {
                await login(values.username, values.password);
              } catch (error) {
                setError((error as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <Form.Item
              name="username"
              label={t("username")}
              rules={[{ required: true, message: t("required") }]}
            >
              <Input size="large" autoComplete="username" placeholder="admin" />
            </Form.Item>
            <Form.Item
              name="password"
              label={t("password")}
              rules={[{ required: true, message: t("required") }]}
            >
              <Input.Password size="large" autoComplete="current-password" />
            </Form.Item>
            <Button
              type="primary"
              size="large"
              block
              htmlType="submit"
              loading={busy}
            >
              {t("signIn")} →
            </Button>
          </Form>
          <p className="login-help">{t("loginHelp")}</p>
          <div className="secure-label">
            <span className="green-dot" /> LOCAL DEPLOYMENT · PRIVATE BY DESIGN
          </div>
        </div>
      </section>
    </div>
  );
}
function Shell() {
  const { user, logout } = useAuth();
  const { t } = useTranslation();
  const [mobile, setMobile] = useState(false);
  const location = useLocation();
  useEffect(() => {
    setMobile(false);
  }, [location.pathname]);
  const current = navigation.find((n) => n.path === location.pathname);
  return (
    <div className="app-shell">
      <aside className={"sidebar " + (mobile ? "mobile-open" : "")}>
        <Brand />
        <div className="workspace-switch">
          <span className="workspace-avatar">M</span>
          <div>
            <strong>ModelDock</strong>
            <span>{t("workspace")}</span>
          </div>
          <Tag>LOCAL</Tag>
        </div>
        <div className="nav-label">{t("workspace")}</div>
        <nav>
          {navigation
            .filter(
              (n) =>
                !["users", "providers"].includes(n.key) ||
                user?.role === "admin",
            )
            .map((item, index) => (
              <div key={item.key}>
                {index === 5 && (
                  <div className="nav-label manage-label">{t("manage")}</div>
                )}
                <NavLink
                  to={item.path}
                  aria-label={t(item.key)}
                  end={item.path === "/"}
                  className={({ isActive }) =>
                    "nav-item " + (isActive ? "selected" : "")
                  }
                >
                  {item.icon}
                  <span>{t(item.key)}</span>
                  {item.key === "playground" && (
                    <span className="nav-new">LAB</span>
                  )}
                </NavLink>
              </div>
            ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-help">
            <ThunderboltOutlined aria-hidden="true" />
            <strong>{t("help")}</strong>
            <p>{t("helpText")}</p>
            <Link to="/documentation">{t("goDocs")} ↗</Link>
          </div>
          <NavLink to="/settings" className="nav-item">
            <SettingOutlined aria-hidden="true" />
            {t("settings")}
          </NavLink>
          <div className="sidebar-footer">
            <span className="green-dot" />
            {t("build")}
          </div>
        </div>
      </aside>
      {mobile && (
        <div className="sidebar-shade" onClick={() => setMobile(false)} />
      )}
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumbs">
            <Button
              className="mobile-menu"
              aria-label={t("manage")}
              type="text"
              icon={<MenuOutlined aria-hidden="true" />}
              onClick={() => setMobile(!mobile)}
            />
            <span>ModelDock</span>
            <span className="separator">/</span>
            <strong>{t(current?.key || "settings")}</strong>
          </div>
          <Space size={16}>
            <span className="local-pill">
              <span className="green-dot" />
              Self-hosted
            </span>
            <Dropdown
              menu={{
                items: [
                  {
                    key: "settings",
                    label: <Link to="/settings">{t("settings")}</Link>,
                    icon: <SettingOutlined aria-hidden="true" />,
                  },
                  {
                    key: "logout",
                    label: t("logout"),
                    icon: <LogoutOutlined aria-hidden="true" />,
                    onClick: () => void logout(),
                  },
                ],
              }}
            >
              <button className="profile-button">
                <Avatar
                  size={30}
                  style={{ background: "#ede7ff", color: "#7047eb" }}
                >
                  {user?.username.slice(0, 1).toUpperCase()}
                </Avatar>
                <span>{user?.username}</span>
              </button>
            </Dropdown>
          </Space>
        </header>
        <main className="page-content">
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route
              path="/providers"
              element={
                user?.role === "admin" ? (
                  <ProvidersPage />
                ) : (
                  <Navigate to="/" replace />
                )
              }
            />
            <Route path="/models" element={<ModelsPage />} />
            <Route path="/playground" element={<PlaygroundPage />} />
            <Route path="/tasks" element={<TasksPage />} />
            <Route path="/files" element={<FilesPage />} />
            <Route path="/keys" element={<KeysPage />} />
            <Route path="/logs" element={<LogsPage />} />
            <Route
              path="/users"
              element={
                user?.role === "admin" ? (
                  <UsersPage />
                ) : (
                  <Navigate to="/" replace />
                )
              }
            />
            <Route path="/documentation" element={<DocsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          <footer className="page-footer">
            <span>
              ModelDock <span className="muted">/</span> Open infrastructure for
              every model.
            </span>
            <span>{t("retention")}</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
export function App() {
  const { loading, user } = useAuth();
  const { t, i18n } = useTranslation();
  const [appearance, setAppearance] = useState(
    localStorage.getItem("modeldock.theme") || "light",
  );
  const dark = appearance === "dark";
  useEffect(() => {
    document.documentElement.dataset.theme = appearance;
    localStorage.setItem("modeldock.theme", appearance);
  }, [appearance]);
  return (
    <ConfigProvider
      button={{ autoInsertSpace: false }}
      locale={i18n.language === "en" ? enUS : zhCN}
      theme={{
        algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
        token: {
          colorPrimary: "#7654ed",
          borderRadius: 9,
          fontFamily:
            'Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
          colorBgLayout: dark ? "#111827" : "#f6f7fb",
          fontSize: 13,
        },
        components: {
          Table: {
            headerBg: dark ? "#1d2637" : "#fafbfe",
            headerColor: dark ? "#aeb9cc" : "#7d8799",
            cellPaddingBlock: 16,
          },
          Button: { primaryShadow: "0 3px 7px #7654ed20" },
        },
      }}
    >
      <BrowserRouter>
        <div className="global-controls">
          <Tooltip title={t("language")}>
            <Select
              aria-label={t("language")}
              size="small"
              value={i18n.language}
              suffixIcon={<GlobalOutlined aria-hidden="true" />}
              options={[
                { value: "zh", label: "中文" },
                { value: "en", label: "EN" },
              ]}
              onChange={(value) => {
                localStorage.setItem("modeldock.language", value);
                void i18n.changeLanguage(value);
              }}
            />
          </Tooltip>
          <Tooltip title={t("theme")}>
            <Button
              aria-label={t("theme")}
              size="small"
              type="text"
              icon={
                dark ? (
                  <SunOutlined aria-hidden="true" />
                ) : (
                  <MoonOutlined aria-hidden="true" />
                )
              }
              onClick={() => setAppearance(dark ? "light" : "dark")}
            />
          </Tooltip>
        </div>
        {loading ? (
          <div className="boot">
            <Spin size="large" />
          </div>
        ) : user ? (
          <Shell />
        ) : (
          <Login />
        )}
      </BrowserRouter>
    </ConfigProvider>
  );
}
