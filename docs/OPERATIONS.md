# 配置与运维

本地语音、主模型下载、模型目录、可选依赖和 Docker 模型卷配置见 [语音模型指南](SPEECH.md)。默认不下载模型；后台下载须由管理员显式发起。

## 配置

所有配置以 `MODELDock_` 为前缀，从环境变量和 `.env` 读取，环境变量优先。不得提交 `.env`。Compose 覆盖数据库/Redis 主机为服务名，并把文件目录设置为 `/data`；本机开发可使用 localhost 和 `./data`。

| 变量 | 默认 / 说明 |
|---|---|
| ADMIN_USERNAME | admin |
| ADMIN_PASSWORD | 无；首次初始化至少 12 字符，已有管理员不会被启动配置重置 |
| DATABASE_URL | PostgreSQL asyncpg URL |
| REDIS_URL | Redis URL |
| DATA_DIR | data；Compose 为 /data |
| STATIC_DIR | web/dist |
| SECURE_COOKIE | false；HTTPS 时设 true |
| SESSION_HOURS | 12 |
| FILE_RETENTION_DAYS | 7；也控制任务保留期 |
| LOG_RETENTION_DAYS | 30 |
| VIRTUAL_GPU_MB | 24576；明确为模拟资源 |
| UPLOAD_MAX_MB | 25 |
| WORKER_LEASE_SECONDS | 20；续约每秒一次 |
| PORT | 8000 |

Compose 额外读取 `POSTGRES_PASSWORD`、`MODELDock_BIND_HOST`（默认 127.0.0.1）和宿主端口 `MODELDock_PORT`。数据库密码用于 URL，因此建议使用 URL 安全的随机十六进制字符串。数据库与 Redis 不暴露宿主端口，只有 API 对外。

镜像使用固定版本基础镜像与锁文件，PostgreSQL/Redis 使用明确摘要。镜像内以 UID 10001 非 root 用户运行 API/Worker，共享文件卷继承镜像目录权限；挂载自建宿主目录时确保该 UID 可读写。

## 启动与升级

```bash
docker compose up --build -d
docker compose ps
docker compose logs --tail=100 api worker migrate
curl -f http://localhost:8000/health/ready
```

migrate 单独运行 Alembic 和幂等初始化；成功后 Worker 启动，Worker 健康后 API 启动。多个 API 进程可共享数据和 Runtime，但不能复制当前 Worker。API 存活检查 `/health/live`；就绪检查同时验证 PostgreSQL、Redis 与 Worker 快照。

更新前备份。部署升级必须运行 migrate，不能仅替换静态目录。`docker compose down` 保留 named volumes；不要添加 `-v`，除非确实要清空部署数据。

## 备份恢复

建议在没有运行任务时暂停 API/Worker，使数据库和文件备份一致：

```bash
mkdir -p backup
docker compose stop api worker
docker compose exec -T postgres pg_dump -U modeldock -Fc modeldock > backup/database.dump
docker compose run --rm --no-deps --entrypoint tar api czf - -C /data . > backup/files.tar.gz
docker compose up -d worker api
```

恢复会替换当前数据库，请在新部署或已备份的目标执行：

```bash
docker compose stop api worker
docker compose exec -T postgres pg_restore -U modeldock -d modeldock --clean --if-exists < backup/database.dump
docker compose run --rm --no-deps --entrypoint tar api xzf - -C /data < backup/files.tar.gz
docker compose run --rm migrate
docker compose up -d worker api
```

数据库保存任务和授权，文件卷保存内容，两者均需备份。Redis 不作为唯一任务源；重启会从数据库恢复 queued。备份可能包含任务输入与用户密码摘要，按业务要求保护备份文件。

## 故障排查

- **首次启动失败**：检查 ADMIN_PASSWORD 至少 12 字符；检查 migrate 日志，不要靠删除卷解决迁移错误。
- **Runtime 离线**：查看 Worker 日志、Redis 连接及唯一租约；正常租约约 20 秒过期。不要手工清除仍活跃 Worker 的锁。
- **模型无法编辑**：先完成/取消任务并卸载模型；运行参数不支持热修改。
- **显存不足**：调整模拟 memory_mb 或停用空闲模型；页面不是实际 GPU 监控。
- **文件无法播放/转写**：首版上传仅接受 PCM WAV；TTS 仅返回示例提示音。
- **Key 429**：RPM 也包含模型列表与任务轮询，可降低轮询频率或调整 Key 限制。
- **登录后写操作 403**：需要当前会话 CSRF，旧会话 token 无效。
- **HTTPS 登录问题**：SECURE_COOKIE=true 只能通过 HTTPS 发送；本机 HTTP 使用 false。
- **镜像拉取失败**：检查宿主 Docker daemon 代理与 registry 镜像配置。客户端 npm/pip 配置不会修复 daemon 的失效代理。不要为此自动修改其他项目使用的 Docker 配置。

日志为 JSON，通过 request_id/task_id 定位，不含提示词、响应或 Key 原文。保留配置改变仅影响后续创建的任务/文件，已有记录的 expires_at 不自动改写。

## 可选第三方推理服务

参考 [PROVIDERS.md](PROVIDERS.md)。凭据通过 Worker 环境变量注入；宿主开发需 export 变量并安装 ffmpeg/ffprobe，Docker 镜像已包含。ComfyUI/WhisperX 是外部服务，容器中的 localhost 不指向宿主；使用实际可达的主机地址。端点、模型/工作流、音色及输出 CDN 由管理员配置，默认不启用远程模型。迁移前备份数据库，新迁移为任务增加 execution_metadata JSON 列。


## 厂商密钥主密钥 / Provider encryption key

使用界面保存厂商 API Key 前，为 API 与 Worker 配置同一个 `MODELDock_PROVIDER_ENCRYPTION_KEY`（Fernet.generate_key() 生成）。主密钥不得写入数据库、日志或版本库；Compose 的 `.env` 注入已覆盖两进程。本次增加数据库迁移，升级先执行 `alembic upgrade head`。

备份数据库时，另行安全备份主密钥。恢复时先注入原主密钥，再启动 API/Worker。缺失或错误主密钥不会退回明文，也不会生成替代密钥；Mock 与环境变量方式仍可用。界面只支持替换各连接的 API Key；本版不自动轮换服务器主密钥。必须更换主密钥时，先停止任务并卸载全部关联模型，保留旧主密钥与数据库备份，在 API/Worker 切换后逐一重新填写各连接的厂商 API Key，再恢复调用。

Connections use an authenticated Fernet ciphertext and a persistent environment master key shared by API and Worker. Back up that key separately. Missing or incorrect keys fail closed for stored credentials; legacy environment adapters and Mock remain available. Master-key rotation is manual and requires re-entering connection credentials after draining and unloading models. Provider calls are never made automatically to validate credentials.
