# 架构与关键语义

## 控制面与执行面

FastAPI 是无模型实例的协议入口，提供管理会话、业务 Key、授权检查和标准化接口。Runtime Worker 是单机部署中的模型实例唯一所有者；独立异步协程处理命令、推理、心跳和维护，因此长调用不会阻塞 API 或资源状态查询。

PostgreSQL 保存用户、模型、授权、会话摘要、Key 摘要、任务、Outbox、文件元数据和调用元数据。文件内容在共享卷。Redis 保存有时限的租约、资源快照、速率计数、命令流和任务增量流，不承担唯一的任务持久化。

## 提交、消费与恢复

1. API 在一个事务中创建 queued 任务与 Outbox。
2. Worker 将未投递 Outbox 发往 Redis Streams，再提交 delivered 标记。中途崩溃允许重复投递。
3. Worker 通过数据库条件更新 `queued → running` 原子领取任务；重复消息无法重复执行已领取或终态任务。
4. 结果写入数据库，并通过 Redis 将文本增量发给 API。同步调用轮询同一任务结果，异步调用返回任务 ID。
5. 重启时 Worker 获取唯一租约，将前任 running 标为 `worker_lost`；queued 任务重新写入 Outbox。生成任务不会自动重试，避免重复外部副作用。

Redis 命令流有界保留；数据库 queued 扫描负责重启恢复。文本事件流保留一小时、限制长度，用于在线增量读取，不是聊天历史接口。每个任务独立事件流避免跨用户串流，业务客户端只能经所有权校验后的 HTTP 接口访问。

租约默认 20 秒、每秒续约。续约失败达到半个租约周期后 Worker 停止接单并协作取消本地调用。终态更新校验任务 Worker ID，避免已失联执行者覆盖新状态。v1 不支持不可取消的阻塞引擎；接入此类引擎须使用可终止的专用进程并实现设备级 fencing。

## 模型状态与资源

启用状态决定是否接受新调用，运行状态决定是否已有实例。加载、资源预算和卸载受统一锁保护；每个模型使用独立信号量限制推理并发。加载请求共享已经加载的实例。活跃计数包含正在加载的执行请求，避免加载期间被卸载。

虚拟 GPU 默认 24576 MB。运行在 cpu 的模型不计入虚拟显存。资源不足时按最近使用时间释放无在途请求的模型；不足仍返回 `resource_exhausted`。维护循环仅回收空闲、无活跃请求的实例。

停用立即拒绝新提交，Worker 排空在途请求再卸载；已接收但尚未开始的调用可能因停用失败。修改运行配置要求模型未加载，且没有 queued/running 任务，防止任务悄然切换配置。

## 取消与保留

排队取消用数据库条件更新抢占 Worker 领取；执行中取消先置 cancel_requested，由 Worker 取消协程并调用 Adapter.cancel。只有停止后才进入 cancelled。同步超时和客户端中断文本流同样请求取消。

任务与文件保留期默认 7 天，日志 30 天，每 30 秒维护一次。queued/running 输入文件不会被物理清理或手动删除；过期文件不再对新请求开放。任务结果文件已过期或手动删除时，历史任务仍可能包含旧引用，下载返回 404。

运行任务所需输入保存在 Task.payload，和日志分离；终态任务到期后清理输入。调用日志只记录元数据，不保存完整文本或音频。Worker 重启会补齐缺失的终态日志，任务 ID 作为日志主键防止重复记录。

## 扩展方向

- 新模型：注册 Adapter，并提供能力与配置 schema。
- 真实 GPU：替换 ResourceProvider，增加设备发现、实测用量和引擎释放验证。
- 对象存储：替换 FileStorage，保留 HTTP 所有权校验，不能直接暴露公共对象 URL。
- 多 Worker：增加任务路由、模型放置、设备租约和远端文件存储；不能直接将当前 Worker 副本数加大。
- 远程转发：Adapter 使用有超时的 HTTP 客户端，按明确协议转换错误及流式事件；远程模型不能宣称已释放外部 GPU。


## 共享厂商连接 / Shared provider connections

厂商连接是部署级管理员配置；模型保存可空连接外键，授权仍以模型为单位。连接密钥以 Fernet 密文独立存储，不进入模型 config、Task.payload、Outbox、Redis 事件或调用日志。API 可以加密写入，但不为校验发起上游网络请求；Worker 解析非敏感配置并将解密凭据注入客户端私有状态。

连接修改、模型绑定及任务提交先锁连接行（多个连接按 ID 排序），再锁模型行；模型绑定在等待锁期间发生变化时返回冲突，调用方刷新后重试。加载命令提交时将模型置为 loading，覆盖命令已投递但尚未执行的窗口。Worker 加载时重新读取并锁定配置。关联模型非 unloaded、有 queued/running 任务或有未投递加载命令时，禁止修改连接。Worker 重启标记历史控制 Outbox 已处理，只恢复推理任务。

Connections are administrator-managed deployment configuration. Model grants remain the authorization boundary. Credentials are encrypted separately and enter only private Worker client state. Locks are acquired in connection-ID order before model locks; stale bindings fail with a conflict. A loading reservation covers queued control commands. Restart discards historical controls while retaining existing task recovery semantics.
