# 验证记录

日期：2026-11-06。以下区分已经执行的检查与尚未完成的环境验收，不将配置文件存在视为部署成功。

## 本次语音移植与主模型下载

以下为本次新增范围的实际验证；后面的表格保留首次 Mock 交付记录。

| 检查 | 结果 |
|---|---|
| Ruff check / format、mypy、uv lock --check | 通过 |
| 完整后端回归（临时 SQLite + fakeredis，临时 NumPy/SciPy） | 75 项通过 |
| 新增语音与模型下载：专用 PostgreSQL + Redis | 30 项通过 |
| 空 PostgreSQL 数据库 Alembic upgrade head / check | 通过，包含 `model_downloads` 迁移，无结构差异 |
| 前端 lint / Vitest / build / format:check | 通过；Vitest 4 项；构建仍有已有 UI 分块体积提示 |
| 完整 Playwright | 4 项通过：控制台全流程、移动端、第三方媒体、语音下载及播放 |
| 模型源元数据 | 已请求并核对四个官方仓库的文件名、版本、大小与哈希字段，未下载真实主权重 |
| 本地资产复制 | 三个 Silero VAD ONNX，4,897,522 字节，与源 SHA-256 一致；Git 忽略验证通过 |
| 实际 ASR/TTS 主模型推理、在线 Edge/ZipVoice、GPU | 未执行；测试使用明确的子进程/HTTP/浏览器夹具 |
| Docker 配置与权重卷 | Compose config 通过；本次未重新执行镜像构建或全栈容器验收 |

PostgreSQL 使用专用、可丢弃数据库 `modeldock_speech_test`，Redis 使用专用测试容器 DB 14；迁移使用独立空库 `modeldock_speech_migrate`，未接触业务数据库。数值测试通过临时安装在 `/tmp/modeldock-speech-test-deps` 的 NumPy/SciPy 运行；普通仅 dev 依赖环境会跳过该可选数值测试模块，其他语音/下载测试仍执行。

新增测试覆盖真实子进程的加载失败、取消/超时终止、并发等待与重新加载；PCM 位深、下混与重采样；Paraformer 精确分块边界及请求缓存隔离；Kokoro 多段音频拼接；ZipVoice 二进制/JSON 协议及错误；Edge 参数映射；主模型下载校验、清理、重复提交、取消、重启、已有目录保护、发布前 Worker 所有权及权限隔离。浏览器使用隔离临时模型目录与假模型源，真实页面完成下载、创建停用配置、启用、播放音频和取消下载，不能将这些夹具结果解释为真实模型推理验收。

Edge 可选依赖使用官方当前协议修复版 7.3.1 并锁定；未调用在线合成服务。[上游发布记录](https://github.com/rany2/edge-tts/releases)说明了相关协议更新。语音运行与下载限制见 [SPEECH.md](SPEECH.md)。

## 首次 Mock 交付记录

| 检查 | 结果 |
|---|---|
| Python Ruff 检查与格式、mypy | 通过 |
| 后端：临时 SQLite + fakeredis | 13 项通过 |
| 后端：真实 PostgreSQL + Redis | 相同 13 项通过 |
| PostgreSQL 空库 Alembic upgrade / check | 通过，无结构差异 |
| API 与 Worker 独立操作系统进程 | 通过；真实 PostgreSQL/Redis、SSE、文件下载、离线 Swagger |
| Worker 强制终止后重启 | 通过；排队任务从数据库恢复并生成有效 PNG |
| 前端 ESLint、TypeScript、Prettier | 通过 |
| 前端 Vitest | 4 项通过，包含分段 SSE、流式错误、CSRF 和统一错误 |
| 前端生产构建 | 通过；UI 依赖块约 1.11 MB / gzip 349 KB，有非阻断体积提示 |
| 桌面浏览器完整流程 | 通过：模型创建/加载/卸载、四类测试、异步任务、文件、Key 撤销、用户授权、日志及文档 |
| 双语、主题与刷新持久化 | 通过 |
| 移动端导航与页面无横向溢出 | 通过 |
| Compose 配置与非默认宿主端口 | 通过；容器内部监听和健康检查仍使用 8000 |
| Docker 镜像完整构建 / 全栈容器启动 | **未完成**：宿主 daemon 的代理 `127.0.0.1:7890` 连接被拒绝，基础镜像拉取阶段即失败 |

真实 PostgreSQL/Redis 来自专用、可丢弃的 `compose.test.yaml` 容器，没有使用其他项目的业务实例。独立进程测试使用单独的空数据库；测试数据与生产数据分离。

13 项后端场景还覆盖跨用户隔离、Web CSRF、API Key 不可访问管理接口、Key 撤销与限流、用户禁用/密码重置、授权收紧、并发首次加载、空闲释放、资源不足、空闲模型淘汰、超时、取消、重复投递、Worker 丢失、Redis 短暂断连、文件过期、任务引用保护、自定义 Adapter 配置与推理扩展，以及流式传输故障的统一错误与取消。

Docker 构建失败的根因来自宿主网络配置；未修改全局 Docker、npm 或代理配置。Python 与 npm 依赖通过单次安装的临时配置完成下载。实际容器镜像构建、非 root 卷权限及全栈 Compose 健康启动，仍需在 Docker registry 可用环境执行；CI 已包含相应容器验收。

首版没有真实模型、GPU 监控、多机资源调度或付费上游调用验证。Mock ASR 仅接受 PCM WAV，TTS 为示例提示音；这些边界在 UI、README 与 API 文档中明确说明。

界面截图保存在 `docs/screenshots/`，由实际浏览器运行生成。

## 2026-11-06：aivideo 第三方推理迁移

本次实现八个可配置协议 Adapter：OpenAI-compatible chat、RunningHub 图片/标准视频/工作流视频、ComfyUI 图片/视频、火山 TTS、WhisperX 字幕。没有读取或导入 aivideo 凭据、模型/音色/工作流预设；没有调用真实付费推理，没有下载模型权重，也没有修改 aivideo。

| 检查 | 本次结果 |
|---|---|
| 完整后端测试：临时 SQLite + fakeredis | 66 项通过（包含工作区同期新增的语音/下载测试） |
| 完整后端测试：真实 PostgreSQL + Redis | 66 项通过；本次独享 modeldock_remote_test、Redis DB 14 |
| 迁移 | 独立 modeldock_migration_remote 空库 upgrade/check/downgrade/upgrade 通过；新增后续迁移后再 upgrade/check 通过 |
| 第三方及原有系统最终回归 | 45 项通过；覆盖错误、超时、取消、并发、文件引用、所有权、重启元数据和八个 Adapter |
| Ruff check/format、mypy | 通过 |
| 前端 lint、Vitest、生产构建、format:check | 通过；Vitest 4 项；构建保留已有 UI chunk 体积提示 |
| Playwright | 原有桌面/移动端 2 项及新增第三方完整流程 1 项均通过（分别运行）；独立 8012 临时服务与本地假上游 |

浏览器媒体场景覆盖真实配置表单和 schema 展示、文生图、图像编辑、图生视频、首尾帧、口型、放大、语音、字幕预览/下载、取消与显式重试、中英文切换，以及带登录鉴权的 MP4 Range 下载。使用 AV1 编码的有效 MP4 测试视频检查浏览器媒体就绪状态。随 Playwright 提供的 Chromium 对 H.264 的 canPlayType 返回空，因此本环境不声称验证了 H.264 解码；该格式仍由后端 ffprobe 校验，播放器实际支持取决于客户端浏览器。假上游仅在 tests/browser_server.py 测试入口安装，不会进入生产入口。

新增音视频格式校验依赖系统 ffprobe；Dockerfile 已加入 ffmpeg。**本次未重新构建/启动完整应用 Docker 镜像，未做真实供应商账号或生成质量联调**；专用 PostgreSQL/Redis 容器通过不等于全栈部署通过。供应商端点、账号权限、有效模型、音色、工作流图及 CDN 白名单需部署者实际配置。

复验：`npm run test:e2e:providers --prefix web`；其余命令见 README。真实依赖测试会清空指定的测试业务表及 Redis DB，只能指向专用可丢弃实例。

## 2026-11-07：多厂商大语言模型连接

实现八家厂商与自定义兼容入口、可复用连接、Fernet 加密凭据、Claude Messages 普通/流式转换及管理界面。保留默认 Mock、旧环境变量配置、单 Worker 和模型授权边界。没有读取或导入真实厂商凭据，没有下载模型权重，没有访问付费推理。

| 检查 | 本次结果 |
|---|---|
| 完整后端：临时 SQLite + fakeredis | 108 项通过，3 项跳过（两个真实行锁测试及可选语音引擎模块） |
| 完整后端：真实 PostgreSQL + Redis | 110 项通过，1 项跳过；使用独享 modeldock_connections_test 与 Redis DB 13 |
| 后续 Claude 配置收紧回归 | 连接及远程 Adapter 72 项通过，2 个 PostgreSQL 专属用例在 SQLite 下跳过 |
| Alembic | 独立 modeldock_connections_migration：upgrade/check/downgrade/upgrade/check 通过；旧用户、授权、模型、Key、任务测试数据在升级后保留 |
| Ruff check/format、mypy、uv lock --check --offline | 通过 |
| 前端 lint、Vitest、build、format:check | 通过；Vitest 4 项；保留已有 UI chunk 体积提示 |
| Playwright 联合回归 | 4 项通过：连接完整流程、桌面工作台、移动端导航、媒体服务流程 |

新增验证覆盖每个厂商预设的请求地址、鉴权和普通/流式结果；思考内容排除、累计 usage、缺失 usage、鉴权失败、限流、超时、无效结果、提前断流及无成功 DONE；密钥加密与更换、缺失/错误主密钥、只写响应、管理员限制、模型绑定、共享连接忙碌限制、加载失败释放、并发加载合并、单请求取消、重启解密与过期加载命令。PostgreSQL 用例验证任务提交等待连接密钥修改事务、连接编辑等待加载状态预留事务。

浏览器用例使用测试入口内的假上游，覆盖创建和复用连接、密钥编辑不回显、普通/流式聊天、连接忙碌错误、浏览器存储无密钥。测试同时发现并修复在线测试页 URL 指定模型与默认模型选择竞争，避免从模型链接进入后误调默认 Mock。新测试模型采用独立名称，避免匹配原有工作台用例。

可选语音引擎测试模块因本环境未安装 numpy 而跳过；本次未安装语音模型依赖。真实数据库测试通过不代表全栈 Docker 部署验证：**未重新构建或启动完整应用 Docker 镜像，未验证真实厂商账号、网络可达性、实时 API 或生成质量**。部署前需要执行迁移，并为 API/Worker 配置同一个持久化 MODELDock_PROVIDER_ENCRYPTION_KEY；详见 PROVIDERS.md 和 OPERATIONS.md。
