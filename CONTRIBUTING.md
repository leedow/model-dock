# 开发规范

## 目录与职责

- `backend/modeldock/api/`：HTTP 协议、输入输出与权限依赖，不持有模型实例。
- `services/`：任务提交、所有权校验、文件与日志等应用规则。
- `entities.py`、`schemas.py`：数据库领域对象与公开 Pydantic 合约。
- `adapters/`：能力转换与引擎实现，不能绕过统一鉴权和文件服务。
- `infrastructure/`：Redis 通信、资源提供方和文件存储。
- `worker.py`：模型实例的唯一所有者，控制生命周期与执行。
- `web/src/`：页面、通用组件、API 客户端、语言资源及主题样式。

## 代码约定

Python 使用类型标注、async/await、Ruff 格式与 mypy 检查。每个并发任务使用自己的 SQLAlchemy AsyncSession，不共享事务。同步引擎或阻塞 I/O 必须移入线程或专用进程，避免阻塞 Worker 心跳和取消。

TypeScript 使用严格模式。远程状态交给 TanStack Query；Key 原文与会话凭据不得写入 localStorage、日志或查询缓存。localStorage 仅保存语言、主题等非敏感偏好。所有界面文案同时提供中英文；图标按钮具备可访问名称。查询显示加载、空数据和错误状态，具有副作用的操作显示进度或确认。

公开参数和响应显式建模。未知字段、未知能力和未实现 Adapter 明确报错；不要静默忽略。错误统一通过 DockError；用稳定 `code` 表达机器可读原因。禁止把原始引擎 traceback、请求正文或上游凭据发给客户端。

## 数据库变更

使用 `uv run alembic revision --autogenerate -m '说明'`，审核生成文件的 upgrade/downgrade，再执行 `alembic upgrade head` 与 `alembic check`。迁移不得依赖运行时元数据去创建整个 schema。配置升级必须保持已有用户、授权、Key 和任务可读取。

## 提交与验收

分支使用 `codex/` 前缀。保持变更围绕一个行为，PR 说明问题、变更后行为和验证证据。

必须通过 README 中的静态检查、测试与构建。涉及持久化、锁、取消、恢复或权限时，增加可观察行为的测试，并在真实 PostgreSQL/Redis 下验证；界面关键流程加入 Playwright 测试。不要仅复制实现写断言。

任何新 Adapter 都需要配置校验、加载失败回收、超时、取消、并发及卸载测试。协议变更同步更新 OpenAPI、示例和 `docs/API.md`。依赖变更同步更新 `uv.lock` 或 `web/package-lock.json`。

## 本地安全边界

测试只能使用临时数据库。运行状态使用显式模拟标签；不把虚拟显存称为真实 GPU 监控。上传文件使用服务生成的 UUID 路径，原始文件名仅作展示。文件访问始终检查用户所有权，管理员也不通过普通文件接口绕过隔离。
