# Adapter 开发指南

本地语音 Adapter、子进程生命周期、可选依赖和主模型下载见 [语音模型指南](SPEECH.md)。

模型通过 `adapter` 名称关联注册表，`capability` 为 `chat`、`image`、`tts`、`asr`、`video`、`subtitle` 之一。API、任务系统、鉴权和文件元数据不应感知具体推理框架。

## 必须实现的能力

- 配置校验：定义 Pydantic 配置，拒绝未知字段；在注册表校验入口使用对应 schema。
- `load()` / `unload()`：初始化与彻底释放引擎；加载失败必须释放已分配资源。禁止模块导入时加载权重。
- `health()`：反映引擎就绪状态。
- `infer(payload)`：返回 `InferenceResult(data=..., artifacts=[Artifact(...)])`。图片和音频用 Artifact 交给统一文件服务。
- `stream(payload)`：聊天 Adapter 按 AsyncIterator[str] 产生内容增量；`usage(payload, text)` 返回真实计数或明确说明的估算。
- `cancel(task_id)`：终止对应上游操作。每个取消只影响自身请求，不能卸载仍被其他请求使用的共享实例。

`templates.py` 提供本地模型与远程服务骨架，故意抛出未实现错误；界面只允许将其保存为停用草稿。Mock 实现是测试与集成示范，不是真实模型引擎。

```python
from modeldock.adapters import REGISTRY
from modeldock.adapters.base import BaseAdapter, InferenceResult

class CustomAdapter(BaseAdapter):
    capability = "chat"

    async def load(self):
        # 加载本地模型，或创建异步远程客户端。
        self.loaded = True

    async def infer(self, payload):
        # 转换 payload，再调用你的引擎；这里只展示协议形状。
        return InferenceResult({"text": "your engine response", "usage": {
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
        }})

REGISTRY["custom.chat"] = CustomAdapter
```

可在 Adapter 上声明 `config_model: type[BaseModel] = YourConfig`，注册表会自动使用它校验配置，并在 `/api/v1/adapters` 返回对应 JSON Schema；无需改动 API 路由。默认四个 Mock 使用 delay_ms、load_delay_ms、fail 配置。

## 本地加载

用只读模型目录挂载权重。把预计显存配置为保守值，使用 cpu 或真实资源提供方支持的设备 ID。当前 API 仅允许 cpu、virtual:0；加入真实 GPU 时应同步扩展设备校验。卸载须清空引擎引用并验证设备资源释放。

## 远程转发

使用异步 HTTP 客户端、固定连接/读取超时及受信任的端点配置。凭据应引用环境变量或使用厂商连接的加密凭据注入，不进入可展示的模型配置、调用日志或错误。明确上游兼容字段和 SSE 转换；不要默认把所有参数透传。

## 测试清单

配置无效、能力不匹配、并发首次加载、显存不足、请求超时、流式中断、取消后资源释放、加载失败回收和卸载幂等。真实引擎测试应独立标记，普通 CI 无需下载权重。

## 请求上下文与远程实现

`context()` 返回请求独立的 ExecutionContext：任务 ID、已授权 InputFile、元数据更新回调、临时产物清单及流式 usage/finish_reason。ContextVar 避免并发串用；无需把文件正文写进 Task.payload。`validate_payload` 在提交前校验供应商支持范围。远程实现和配置详见 [PROVIDERS.md](PROVIDERS.md)。

Artifact 可使用 bytes 或临时 path；Worker 将产物转入统一存储，失败清理已保存的部分产物和临时文件。stream 可返回字符串或 StreamEvent，后者携带内容、usage 及结束原因。cancel 必须按任务取消，不能影响共享实例其他请求。


`openai_compatible.chat` 和 `anthropic.chat` 可由 `provider_connection_id` 绑定管理员连接；构造及 validate_payload 只使用非敏感配置，Worker 调用 `bind_credentials` 注入私有凭据。不要把解密密钥添加到 Pydantic config 或序列化任务中。连接型模型仅保存 model/token_parameter；原有独立 Adapter 的 api_key_env 方式继续支持。
