# API 合约

## 本地语音与主模型下载

语音协议继续使用 `/v1/audio/transcriptions`、`/v1/audio/speech` 和 `/v1/tasks`。新增 Adapter 为 `local.sensevoice.asr`、`local.paraformer.asr`、`local.faster_whisper.asr`、`local.kokoro.tts`、`zipvoice.tts`、`edge.tts`，配置示例由 `/api/v1/adapters` 返回。`local_speech=true` 表示当前须配置 `device=cpu` 的本地语音引擎。PCM WAV 输入、格式/音色限制和安装见 [语音指南](SPEECH.md)。真实语音返回 `mock=false`，不使用 Mock 标签。

以下接口只允许管理员 Web 会话，写操作要求 CSRF，API Key 无权调用：

| 方法与路径 | 行为 |
|---|---|
| `GET /api/v1/model-downloads/catalog` | 四个主模型预设及 Adapter、配置、仓库 |
| `GET /api/v1/model-downloads` | 当前管理员最近 100 个下载记录 |
| `POST /api/v1/model-downloads` | `{"preset":"sensevoice"}`，可选 `paraformer`、`faster-whisper`、`kokoro`；返回 202 |
| `POST /api/v1/model-downloads/{id}/cancel` | 取消当前管理员的排队/执行中下载，返回 202；轮询确认终态 |
| `POST /api/v1/model-downloads/{id}/register` | 已成功下载创建停用 CPU 模型，返回 201；同名冲突返回 409 |

下载记录包含 `id`、`preset`、`status`（queued/running/succeeded/failed/cancelled）、`downloaded_bytes`、`total_bytes`、`current_file`、`revision`、`error_code`、`cancel_requested`、时间戳。不暴露用户磁盘绝对路径、签名下载 URL 或凭据。未知预设/额外字段返回 422，已有活动下载返回 409 `download_busy`，跨用户记录返回 404。失败代码包括 `download_failed`、`download_timeout`、`download_checksum_failed`、`download_disk_full`、`download_size_limit`、`download_manifest_invalid`、`download_redirect_invalid`、`model_path_exists`、`worker_lost`。

English: Speech uses the existing synchronous and task APIs. Download endpoints require an administrator session and CSRF for writes, reject API keys, and scope records to the creating user. Only four curated presets are accepted. Downloads expose persisted byte progress and a pinned revision; successful snapshots can be registered as disabled CPU models. See [speech configuration and limits](SPEECH.md).

版本前缀为 `/v1`（业务）和 `/api/v1`（管理）。Swagger `/docs`、OpenAPI `/openapi.json` 可在本地访问。业务接口使用 `Authorization: Bearer mdk_...`，也可使用 Web 会话。管理接口只接受 Web 会话，API Key 即使属于管理员也不能调用。

响应都包含 `X-Request-ID`。请求可提供 1–80 字符的安全请求 ID，服务会校验或重新生成。JSON 错误统一：

```json
{"error":{"message":"Please sign in","type":"authentication_error","param":null,"code":"unauthenticated"}}
```

## 文本

`POST /v1/chat/completions` 支持 model、messages（system/developer/user/assistant，纯文本）、stream、temperature（0–2）、top_p（0–1）、max_tokens 或 max_completion_tokens（互斥，1–8192）、stop（最多四项）、n=1 和 stream_options.include_usage。

普通结果包含 id、object、created、model、choices、usage。SSE 使用 `data: JSON\n\n`，依次包含 role 增量、content 增量、finish_reason=stop，可选 usage 空 choices 块，最后 `data: [DONE]`。流已经开始后发生错误，会发送统一 error 数据块并关闭连接，不伪造成功结束。

当前 Chat Completions 兼容范围依据 [OpenAI 官方协议](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)。不支持 tools、tool_calls、response_format、视觉消息、音频消息、多个候选、Responses API 等，未知字段返回 422。Mock 的 temperature、top_p 接受并校验但不改变确定性回复；max_tokens 与 stop 影响模拟输出，usage 为字符长度估算。

`GET /v1/models` 返回当前凭据有权调用且已启用的模型，包含 capability 扩展字段。

## 媒体

| 接口 | 参数 | 结果 |
|---|---|---|
| `POST /v1/images/generations` | model、prompt、size、n、response_format | created、data、mock；每项包含 file_id 及 url 或 b64_json |
| `POST /v1/audio/speech` | model、input、voice、speed、response_format=wav | audio/wav，`X-File-ID` 指向统一文件记录 |
| `POST /v1/audio/transcriptions` | multipart：model、file、language、response_format | JSON `{text,mock}` 或 text/plain |

生图尺寸支持 256x256、512x512、1024x1024，n 为 1–4。TTS voice 为 alloy/echo/nova，speed 为 0.25–4；首版 voice 仅作协议演示，实际生成 440Hz 提示音。ASR 上传仅接受有效 PCM WAV，默认上限 25 MB；language 不改变模拟转写。

同步接口超时会请求取消后台执行。耗时调用推荐异步接口。Mock 媒体结果均明确标记模拟性质。

## 异步任务

```json
{
  "model":"mock-image",
  "capability":"image",
  "parameters":{"prompt":"a blue planet","size":"512x512","n":1}
}
```

`POST /v1/tasks` 接受 image、tts、asr。parameters 使用对应同步媒体参数（不需要 model）；ASR 使用 file_id、可选 language、response_format。

返回 HTTP 202 与任务对象：id、model、capability、status、progress（0–100）、result、error、cancel_requested、created_at、started_at、finished_at、expires_at、request_id。时间戳为 UTC Unix 秒，界面使用浏览器本地时区显示。

- `GET /v1/tasks?status=running&offset=0&limit=20`：只列当前用户媒体任务。
- `GET /v1/tasks/{id}`：查询状态及结果，结果文件在 `result.files`。
- `POST /v1/tasks/{id}/cancel`：取消排队任务，或协作取消运行任务。
- `POST /v1/tasks/{id}/retry`：只允许重试 failed/cancelled；创建新任务，重新校验当前授权和模型状态。

成功终态 result 随能力变化：图片/音频为文件列表，ASR 为 text。跨用户查询或任务过期返回 404。过期排队任务由 Worker 标记失败后清理。列表最大 limit=200。

## 文件

- `POST /v1/files`：multipart file，首版上传 PCM WAV，返回文件元数据。
- `GET /v1/files?media_type=audio/wav&offset=0&limit=20`：分页与类型筛选。
- `GET /v1/files/{id}`：元数据。
- `GET /v1/files/{id}/content`：内容，必须鉴权，支持浏览器预览或客户端下载。
- `DELETE /v1/files/{id}`：删除；被 queued/running 引用时返回 409。

元数据为 id、filename、media_type、size、created_at、expires_at、task_id、url。存储路径只用服务 UUID，与原始文件名无关。文件过期或不存在返回 404。管理员通过普通文件接口也只能访问自己的文件。

## Web 会话及管理

`POST /api/v1/auth/login` 接受 username/password，返回 user/csrf_token 并设置 HttpOnly、SameSite=Strict Cookie。后续写操作带 `X-CSRF-Token`；GET `/auth/me` 获取当前用户及 CSRF，POST `/auth/logout` 注销。POST `/auth/password` 验证旧密码并撤销该用户所有会话。

- `/api/v1/models`：授权模型列表；管理员 POST 创建、PUT `/{id}` 修改。
- `/api/v1/models/{id}/{enable|disable|load|unload}`：管理员控制动作；启用不意味着立即加载。
- `/api/v1/adapters`：能力、可执行状态与 Mock 配置 JSON Schema。
- `/api/v1/keys`：当前用户列表、POST 创建；DELETE `/{id}` 撤销。原文仅创建响应展示一次。
- `/api/v1/users`：管理员列表、创建；PATCH `/{id}` 管理 enabled、password、model_ids。
- `/api/v1/logs`：分页，支持 model/status/request_id/since/until；管理员看全局元数据，普通用户看自己的。
- `/api/v1/overview`：近 24 小时调用、延迟、任务、模型和模拟资源快照。

Key 的模型列表与用户当前授权始终取交集；Key 不因之后新加用户授权而自动扩大自身范围。撤销、过期或账号禁用后不再通过鉴权；禁用账号同时撤销其现有 Key，重新启用后需创建新 Key。RPM 是 Redis 原子固定分钟窗口，针对该 Key 的所有业务请求计数（包括轮询），超限返回 429 和 Retry-After。

## 常见错误

| HTTP | code | 含义 |
|---|---|---|
| 401 | unauthenticated / invalid_api_key / account_disabled | 需要登录、Key 无效或账号禁用 |
| 403 | csrf_failed / forbidden | CSRF 或权限不足 |
| 404 | not_found / model_not_found | 资源不存在或未获授权 |
| 409 | model_busy / model_disabled / file_busy | 当前状态不允许操作 |
| 413 | file_size | 空文件或超出大小限制 |
| 422 | validation_error | 字段、类型或能力参数不支持 |
| 429 | rate_limit | Key 或登录尝试超限 |
| 503 | worker_unavailable / worker_lost / resource_exhausted / mock_failure / runtime_error | Worker、资源或推理故障 |
| 504 | timeout | 同步等待超时 |

接口接受错误与任务执行错误分开：异步提交合法时返回 202，随后执行失败通过任务 error 查询。诊断使用 request_id，客户端不依赖错误文案。


## 可选第三方能力

详见 [第三方服务](PROVIDERS.md)。默认 Mock 合约保持原行为，真实模型支持范围由 Adapter 配置校验。

- 模型列表增加 operations；/api/v1/adapters 增加 remote、operations、input_schema、config_example。
- /v1/tasks 增加 video、subtitle；image parameters 增加 operation=generate/edit 和 image_file_id。video 支持 image_to_video/start_end/lip_sync/upscale，输入分别为 image_file_id、end_image_file_id、audio_file_id、video_file_id。字幕接受 file_id 和 text 或 text_file_id（二选一）。未知字段仍拒绝。
- 图片生成增加 aspect_ratio、resolution；size 的支持范围由 Adapter 校验。TTS voice 为配置音色名，支持 response_format=wav/mp3、volume、pitch；长文本上限 100000，v1 与 Mock 仍限制 4096。同步语音按实际 MIME 和扩展名响应，X-File-ID 保留。
- mock 字段反映实际 Adapter 结果；文本 finish_reason/usage 来自上游流或普通响应。未提供计数时用零值，任务 result.usage_estimated 明确标记。
- 任务增加 execution_metadata，保存脱敏上游任务标识和 cancellation。取消接口仍返回 202，本地 cancelled 不保证远端生成或计费停止。worker_lost 不恢复上游轮询，显式重试可能再次计费。
- /v1/files 上传增加 JPEG/PNG/WebP、MP3/FLAC、MP4、UTF-8 文本；鉴权下载支持视频 Range 请求。所有输入仍受所有权、保留期与引用保护约束。
- 新错误包括 provider_credentials_missing、unsupported_parameter、invalid_media、invalid_output、invalid_output_host、upstream_error、upstream_timeout、upstream_download、upstream_stream_incomplete、media_inspector_unavailable。上游内容和凭据不回显。


## 厂商连接管理 / Provider connection management

以下接口仅接受管理员登录会话，写操作需 CSRF token；普通用户及 Bearer API Key 均不可访问。

| 接口 | 行为 |
|---|---|
| GET /api/v1/provider-presets | 返回厂商 id、显示名称、推荐 base_url、官方文档和 Adapter |
| GET /api/v1/provider-connections | 返回连接配置、has_api_key、时间戳及关联模型列表 |
| POST /api/v1/provider-connections | 创建连接，201；api_key 必填且仅写入 |
| PUT /api/v1/provider-connections/{id} | 更新连接，api_key 省略/null/空串保留原值 |
| DELETE /api/v1/provider-connections/{id} | 删除无模型引用的连接，204 |

创建/更新字段：`name`（1–80 字符）、`provider`（预设 id）、`base_url`（可信 HTTP(S)，不接受账号密码、查询或 fragment）、`connect_timeout`（默认 10 秒，>0 且 ≤60）、`read_timeout`（默认 60 秒，>0 且 ≤300）、只写 `api_key`。响应不包含 api_key 或 encrypted_api_key；读取不能用于恢复凭据。

模型创建/更新新增可空 `provider_connection_id`。绑定连接时 capability 必须为 chat，Adapter 必须匹配协议，config 仅接受 `model` 和可选 `token_parameter`（max_tokens/max_completion_tokens）。旧模型不填连接字段时继续使用原有配置。普通用户只能列出已授权模型，不可查询连接管理接口。

Chat 请求新增 `max_completion_tokens`，与 max_tokens 同时提供返回 422。远程模型不注入未提供的 temperature/top_p 或 Token 上限；Claude 因协议要求默认 max_tokens=256，Mock 仍默认 256。客户端用 ModelDock 模型名调用，Worker 替换为管理员配置的上游 ID。stream_options.include_usage 控制面向业务客户端的 usage 输出；上游缺失 usage 时零计数并在任务结果中标记 usage_estimated。

稳定错误码包括：`provider_encryption_unavailable`、`provider_decryption_failed`（503）；`provider_connection_busy`、`provider_connection_in_use`、`duplicate_connection`（409）；`provider_connection_not_found`（404）；`upstream_authentication_failed`（502）、`upstream_rate_limit`（429）、`upstream_timeout`（504）、`upstream_invalid_response`、`upstream_stream_incomplete`、`upstream_unsupported_response`（502）。HTTP 流建立后的错误以 SSE error 事件返回，不发送成功 DONE。生成不自动重试。

**English:** Provider management requires an administrator session and CSRF on writes. API keys are write-only; reads return only `has_api_key`. Models bind a reusable connection through nullable `provider_connection_id`; connected chat configuration contains only the upstream `model` and optional `token_parameter`. Legacy environment references remain supported. Chat accepts mutually exclusive `max_tokens` and `max_completion_tokens`; omitted sampling parameters are not sent upstream. Error codes are stable and sanitized. Stream errors never end with a success DONE event.
