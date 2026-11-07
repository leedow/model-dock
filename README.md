# ModelDock

本地部署的多模态模型 API 服务中心。统一管理模型、鉴权、推理任务、文件和资源，通过可扩展 Adapter 为多个业务系统提供服务。

语音扩展支持 Luna-Aster 的 SenseVoice、Paraformer、Faster-Whisper、Kokoro、ZipVoice 与 Edge 对接。管理员可在 **模型管理 → 主模型下载** 下载四类本地主模型，查看进度、取消和创建配置；权重不提交仓库。安装可选依赖、目录及边界见 [语音模型指南](docs/SPEECH.md)。

已提供厂商连接管理，支持 OpenAI、Claude、Gemini、DeepSeek、通义千问、豆包、Kimi、智谱与自定义 OpenAI 兼容文本服务。管理员在界面保存加密密钥，同一连接可供多个模型复用；部署时需配置 `MODELDock_PROVIDER_ENCRYPTION_KEY`，步骤见 [第三方服务接入](docs/PROVIDERS.md)。

另提供可选的 RunningHub、ComfyUI、火山引擎 TTS 和 WhisperX 字幕 Adapter，支持视频与图像编辑。配置与边界见 [第三方服务接入](docs/PROVIDERS.md)；默认仍只运行 Mock，不导入凭据或模型/工作流预设。

**首版是完整可运行的模拟环境**：包含文本流式对话、PNG 生图、WAV 提示音、WAV 上传转写、异步任务、用户隔离及 Web 工作台。无需 GPU、模型权重或外部模型 API Key。GPU 指标为明确标注的虚拟资源，TTS 是提示音，ASR 为固定模拟转写，不代表真实推理能力。

![ModelDock 工作台](docs/screenshots/overview.png)

[深色英文界面](docs/screenshots/documentation-dark.png) · [移动端界面](docs/screenshots/mobile.png)

## 快速启动

需要 Docker Engine 和 Docker Compose v2。

```bash
cp .env.example .env
# 编辑 .env，设置 MODELDock_ADMIN_PASSWORD（至少 12 个字符）。
# 也建议修改 POSTGRES_PASSWORD；使用 URL 安全字符，如十六进制随机值。
docker compose up --build -d
```

- 工作台：[http://localhost:8000](http://localhost:8000)
- Swagger：[http://localhost:8000/docs](http://localhost:8000/docs)
- OpenAPI：[http://localhost:8000/openapi.json](http://localhost:8000/openapi.json)
- 管理员用户名：`.env` 中的 `MODELDock_ADMIN_USERNAME`，默认 `admin`。
- 管理员密码：自行设置的 `MODELDock_ADMIN_PASSWORD`；项目没有固定默认密码。

迁移服务会幂等初始化管理员与 `mock-chat`、`mock-image`、`mock-tts`、`mock-asr` 四个模型。普通用户由管理员在工作台创建并授予模型访问权限。

首次登录后：**模型管理 → 在线测试 → API Key → 接入文档**。模型默认启用但未加载，首次调用自动加载，默认空闲 10 分钟卸载。在线测试使用登录会话；业务系统使用 Bearer API Key。

默认仅绑定 `127.0.0.1:8000`。需供其他机器访问时设置 `MODELDock_BIND_HOST=0.0.0.0`，按环境配置反向代理和 HTTPS；HTTPS 环境应设置 `MODELDock_SECURE_COOKIE=true`。

## 架构

```mermaid
flowchart LR
  UI[React 管理工作台] --> API[FastAPI / API 与控制面]
  Apps[业务系统 / OpenAI SDK] --> API
  API --> PG[(PostgreSQL / 配置、权限、任务与 Outbox)]
  API <-->|结果轮询 / SSE| Redis[(Redis Streams / 命令、事件和租约)]
  PG <--> Worker[独立 Runtime Worker]
  Redis <--> Worker
  Worker --> Adapter[Adapter Registry]
  Adapter --> Mock[Mock Chat / Image / TTS / ASR]
  Adapter -.扩展.-> Engine[本地模型 / 远程推理服务]
  API --> Files[共享文件存储]
  Worker --> Files
```

API 进程不加载模型。首版一个 Runtime Worker 持有模型实例、处理资源预算、并发、取消和空闲回收；Redis 租约拒绝重复 Worker。PostgreSQL 是任务状态的事实来源，Outbox 防止提交和投递之间丢任务。重启恢复排队任务，失联时正在运行的任务标为失败，需显式重试。

前端支持中英文与浅色/深色主题。模型、资源、在线测试、任务、文件、Key、调用日志和用户页面连接后端；页面刷新不影响异步任务。

## 接口示例

先在工作台创建只允许所需模型的 API Key，然后将其放入环境变量。

```bash
export MODELDock_API_KEY='替换为你创建的 Key'
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $MODELDock_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"mock-chat","messages":[{"role":"user","content":"你好"}],"stream":true}'
```

Python SDK 示例：

```python
import os
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key=os.environ["MODELDock_API_KEY"])
response = client.chat.completions.create(
    model="mock-chat", messages=[{"role": "user", "content": "你好"}]
)
print(response.choices[0].message.content)
```

完整示例见 [examples](examples/)，包括流式文本、同步媒体、异步任务和授权文件下载。详细字段、错误和兼容边界见 [API 文档](docs/API.md)。文件 URL 也需要 Bearer 鉴权或有效登录会话，并非公开链接。

## 本地开发

需要 Python 3.12、Node.js 20.19+、uv 0.9.11；部署与开发均有依赖锁文件。

```bash
uv sync --frozen --extra dev
npm ci --prefix web
# 可启动专用临时 PostgreSQL / Redis（与正式部署的卷隔离）
docker compose -f compose.test.yaml up -d --wait
# 开发数据库与会被测试重建的数据库分开
docker compose -f compose.test.yaml exec postgres createdb -U modeldock modeldock_dev
```

在开发 `.env` 中设置：

```dotenv
MODELDock_DATABASE_URL=postgresql+asyncpg://modeldock:test-only-password@127.0.0.1:55439/modeldock_dev
MODELDock_REDIS_URL=redis://127.0.0.1:56379/0
MODELDock_DATA_DIR=./data
MODELDock_ADMIN_PASSWORD=自行设置至少12字符密码
```

随后运行：

```bash
uv run alembic upgrade head
uv run modeldock-init
uv run modeldock-worker
# 另一个终端
uv run modeldock-api
# 再一个终端，Vite 代理后端并支持热更新
npm run dev --prefix web
```

工作台开发地址默认为 `http://localhost:5173`。如已有本地 PostgreSQL/Redis，直接在 `.env` 配置连接地址，无需启动临时依赖容器。端口被占用时可设置 `MODELDock_PORT`（API）和 `MODELDock_WEB_PORT`（前端），Vite 会自动代理到配置的 API 端口。生产构建 `npm run build --prefix web` 后由 API 同源托管。Swagger JS/CSS 也打包到本地，运行时无需 CDN。

## 验证

```bash
uv run ruff check backend tests
uv run ruff format --check backend tests
uv run mypy backend/modeldock
uv run pytest -q
npm run lint --prefix web
npm test --prefix web
npm run build --prefix web
npm run format:check --prefix web
```

默认后端测试使用临时 SQLite + fakeredis；实际部署固定为 PostgreSQL + Redis，不会自动降级。

```bash
TEST_DATABASE_URL=postgresql+asyncpg://modeldock:test-only-password@127.0.0.1:55439/modeldock_test \
TEST_REDIS_URL=redis://127.0.0.1:56379/15 uv run pytest -q
```

**集成测试会重建 `TEST_DATABASE_URL` 对应数据库的业务表，清空 `TEST_REDIS_URL` 对应 Redis DB；只能使用可丢弃的测试实例。**

```bash
cd web
npx playwright install --no-shell chromium
npm run test:e2e
```

浏览器测试自动启动仅用于测试的临时环境，使用测试夹具账号并在退出时清理；生产入口不包含这些固定测试凭据。CI 包含 PostgreSQL/Redis 测试、前后端检查和关键浏览器流程。

## 运维与扩展

- [架构及恢复语义](docs/ARCHITECTURE.md)
- [Adapter 扩展指南](docs/ADAPTERS.md)
- [开发规范](CONTRIBUTING.md)
- [配置、部署、备份恢复及排障](docs/OPERATIONS.md)
- [验证记录与已知边界](docs/VERIFICATION.md)

默认部署不含模型权重；可选远程 Adapter 需自行配置。首版不含真实 GPU 调度、多 Worker 调度、组织租户、计费、公开注册、视觉输入、工具调用或 Responses API。模板 Adapter 仅能保存为停用草稿；实现引擎后再注册和启用。
