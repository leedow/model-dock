# ModelDock 开发约定

阅读 README.md、CONTRIBUTING.md 和相关 docs/ 后再修改。保持本地部署、单 Runtime Worker 和显式 Mock 的首版边界；不要为普通开发下载模型权重或接入付费模型。

- API 不加载模型。Adapter、资源提供方、文件存储与协议层分别扩展。
- 所有业务资源按用户所有权查询；API Key 不能访问管理接口，权限与用户当前授权取交集。
- SQL 结构变化必须提供 Alembic 迁移；每个异步任务独立 AsyncSession。
- 中英文文案同步维护，图标按钮有可访问名称，表单使用独立 name 防止重复 DOM ID。
- 不提交 .env、数据卷、生成文件或凭据，不记录完整推理输入输出。
- 检查：ruff check/format、mypy、pytest、前端 lint/test/build/format:check；关键交互运行 Playwright。
- TEST_DATABASE_URL / TEST_REDIS_URL 必须指向专用可丢弃实例，测试会清空其中数据。
- 修改后如实记录验证范围；宿主 Docker/网络故障不得表述为部署验证通过。
