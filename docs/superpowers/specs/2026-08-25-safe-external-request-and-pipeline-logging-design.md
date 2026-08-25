# 外部请求与翻译管线安全日志设计

## 背景

ComicLens 当前主要依赖 Uvicorn 的入站访问日志。漫画来源、OCR、DeepL 和 DeepLX 的外部
HTTP 请求都使用 httpx，但项目主动把 `httpx` 和 `httpcore` 的日志级别提高到 `WARNING`，
因为它们的 INFO 日志可能输出包含凭据的完整 URL。翻译 Manager 目前只记录少量任务异常，
无法从 Docker 日志判断一次漫画获取、OCR 或翻译请求何时发出、收到什么状态、是否重试，
也无法区分缓存命中与真正调用外部服务。

本次增加业务层显式日志：由各客户端在掌握业务语义和敏感数据边界的位置记录安全摘要，
继续禁止 httpx 自动输出完整请求。日志用于 `docker logs` 排查网络、协议、缓存和任务阶段问题，
不改变任何请求、重试、并发、缓存或错误处理行为。

## 已确认行为

- 使用业务层显式埋点，不使用 httpx Event Hooks 或自定义 Transport 作为主要方案。
- 现有 `COMICLENS_LOG_LEVEL` 控制详细度，不新增数据库设置、Web 设置或环境变量。
- INFO 默认记录外部请求安全摘要、任务阶段、缓存命中和异步 OCR 状态变化。
- DEBUG 增加每次异步 OCR 轮询，以及失败响应的脱敏、截断 JSON 片段。
- 异步 OCR 轮询状态没有变化时不重复输出 INFO。
- 记录漫画来源、OCR、DeepL、DeepLX、翻译任务和相关缓存决策。
- 日志不显示时间戳或 logger 名称；Uvicorn 原有访问日志保持原样。
- 日志固定以 `级别 服务 event=事件` 开头，服务名为 `manga`、`ocr`、`deepl`、
  `deeplx` 或 `task`。
- 不打印请求或成功响应全文，不打印图片 Base64、原文、译文、认证 Header、Cookie 或代理
  地址及凭据。
- README 说明 INFO/DEBUG 差异、查看命令及 Compose 已有的日志轮转。

## 方案选择

采用业务层显式日志。漫画来源、OCR 客户端、翻译客户端和任务调度器分别知道请求的实际
语义、重试边界、敏感值和解析结果，因此可以记录 `chapter_manifest`、`submit`、
`translate_batch`、`cache_hit` 等可操作信息。

不采用以下方案：

- httpx Event Hooks：虽然集中，但无法可靠表达业务操作、逻辑重试、缓存和任务阶段，临时
  漫画代理客户端也需要单独接入。
- 自定义 Transport：会侵入共享客户端、代理客户端和 MockTransport 测试，仍然无法提供任务
  语义。
- 直接恢复 httpx INFO：会重新引入完整 URL 和潜在凭据泄漏。

## 日志基础设施

新增独立的 observability 模块，职责限定为：

1. 生成安全的单行 `key=value` 事件消息；
2. 规范 URL、异常、字段值和失败响应摘要；
3. 提供异步安全的任务上下文；
4. 生成短请求引用、generation 引用和 OCR job 引用；
5. 根据 INFO 或 DEBUG 级别发出事件。

业务代码只传入结构化字段，不自行拼接 URL、凭据或响应正文。辅助模块对字段执行稳定排序，
但只把以下前缀视为对外格式契约：

```text
INFO manga event=request ...
INFO ocr event=response ...
WARNING deepl event=retry ...
ERROR task event=stage_failed ...
```

应用根日志格式调整为 `%(levelname)s %(message)s`，不包含时间戳和 `%(name)s`。Uvicorn 自己的
logger 和 formatter 不作修改，因此现有入站访问日志保持其原格式。`httpx` 与 `httpcore`
继续固定为 `WARNING`。

所有值必须是单行。换行、制表符和其他控制字符转换为空格，字符串按上限截断；含空格或特殊
字符的值使用安全引用形式，避免一个用户输入伪造新的日志行。

## 关联上下文

翻译任务使用 `contextvars` 建立异步上下文。Manager 在 generation 边界设置章节上下文，
在页或分片任务边界增加索引；OCR 和翻译客户端自动继承当前上下文，不要求在每一层方法签名
重复传递字段。

可用上下文字段包括：

- `generation_ref`：generation ID 的稳定短摘要，不记录完整 ID；
- `comic`、`chapter`：当前漫画和章节标识；
- `page_index`、`segment_index`：代码中使用的零基索引；
- `global_index`：存在时记录全局分片序号。

每个逻辑外部请求生成短 `request_ref`。同一请求的多个重试共享该引用，并通过 `attempt`
区分；并发请求由引用和任务上下文关联。OCR job ID 使用稳定短摘要 `job_ref`，完整 job ID
不进入日志。

## 外部请求事件

### 漫画来源

Manga18fxSource 为调用链传递明确操作名：

- `home`、`search`、`category`、`ranking`；
- `detail`、`chapter_manifest`；
- `fetch_media`。

每次实际网络尝试记录 `event=request` 和 `event=response`，包含操作、方法、安全 endpoint、
route、attempt、状态、耗时、响应大小及内容类型。发生安全重定向时记录原响应状态和下一目标的
安全 endpoint。

设置了应用漫画代理时 route 为 `configured_proxy`；未设置时为
`environment_or_direct`。代码不读取标准代理环境变量来推断实际线路，也从不记录代理 URL、
账号或密码。

### OCR

OCRClient 区分以下操作：

- 同步请求：`analyze`；
- 异步提交：`submit`；
- 异步状态：`poll`；
- 异步结果：`download_result`。

请求摘要可记录 protocol、auth 类型、模型、图片字节数和序列化 payload 大小，但不得记录
图片、Base64、Token、Basic 用户名或密码。响应摘要可记录 HTTP 状态、耗时、响应字节数、
异步 state 和解析到的 layout result 数量。

每次 poll 请求及响应只在 DEBUG 输出。OCRClient 跟踪上一次已记录状态，只有
`pending → running → done/failed` 等状态变化才输出 INFO。恢复已持久化 job 时仍使用 job ID
短摘要关联，不输出完整值。结果下载地址只记录 origin，不记录可能带签名信息的路径、query
或 fragment。

### DeepL 与 DeepLX

DeepL 以 batch 为单位记录 `translate_batch`，字段包含文本条数、总字符数、payload 字节数、
源语言、目标语言、状态、耗时、响应字节数和成功返回条数。

DeepLX 当前每个非空文本发送一次请求，因此以 `translate_text` 记录单次字符数和 payload
大小；任务阶段的完成摘要再记录该分片总文本块数与成功翻译数。日志不记录任何原文或译文。

## 重试与失败

外部请求的现有重试循环保持原样，仅在既有决策点发出事件：

- 每个收到的 HTTP 响应：INFO `event=response`，包括非 2xx 状态；poll 响应仍遵循只在 DEBUG
  记录每次请求、INFO 只记录状态变化的例外规则；
- 即将重试：WARNING `event=retry`，包含安全的状态或异常类别、当前和下一 attempt、delay；
- 不可重试或重试耗尽：ERROR `event=failed`，包含异常类别、HTTP 状态（若有）、attempts 和
  总耗时。

不直接记录 `str(exception)`，因为 httpx 异常可能包含完整 URL。业务协议错误只记录稳定的
错误类别或应用错误码。外部请求的最终失败和任务的 `stage_failed` 是两个不同范围的事件，
允许各记录一次：前者说明网络调用，后者提供页和分片上下文。

既有 AppError、OCR/翻译异常类型、重试次数、延迟、超时和用户可见错误响应完全不变。

## 任务阶段与缓存

TranslationManager 记录章节任务级事件：

- `task_started`、`task_paused`、`task_resumed`；
- `task_completed`、`task_cancelled`、`task_failed`；
- `retry_failed_requested`，包含加入队列的失败项数量。

新旧 translation generation 路径都必须覆盖，不能只覆盖 progressive 分片流程。

SegmentRunner 和旧页级执行路径记录 `ocr`、`translation`、`render` 的 `stage_start`、
`stage_complete` 和 `stage_failed`。完成摘要记录适用的 blocks、translated、输入/输出字节数和
duration，不记录内容。

仅在决定是否调用外部服务或重新渲染的业务分支记录 `cache_hit` / `cache_miss`，包括 OCR、
blocks、translations 和 translated image。不得给 MediaCache 每次底层读取统一加 INFO，避免
普通封面读取和轮询产生大量噪声。

## URL 与敏感信息处理

普通外部 endpoint 只保留 `scheme://host:effective-port/path`：

- 删除 userinfo；
- 删除 query 和 fragment；
- 规范默认端口；
- 替换当前客户端已知的 Token、API Key、用户名和密码字面值；
- 限制最终长度。

漫画代理 URL 永不传给 endpoint formatter。OCR 异步结果下载仅记录 origin，防止 signed path
泄漏。搜索文本位于 query，因此漫画 search 日志只显示 `/search`，不显示关键词。

请求 Header、Cookie 和 body 不进入通用事件字段。认证只记录枚举值，例如 `auth=basic`、
`auth=bearer` 或 `auth=none`。

## DEBUG 失败响应摘要

DEBUG 只为非 2xx 响应或响应协议解析失败提供摘要，成功响应不打印正文。

处理规则：

1. 最多读取并输出 1024 个字符；
2. 仅 JSON 响应可以输出片段；非 JSON 只记录 content type 和 response bytes；
3. 递归替换名称匹配以下类别的键：
   `authorization/token/key/password/secret/cookie/file/image/base64/text/content/translation/result`；
4. 再替换当前请求已知的认证值和待翻译原文字面值；
5. 对剩余字符串执行 URL 脱敏、控制字符清理和长度限制；
6. 使用 `truncated=true/false` 表示是否截断。

该摘要用于查看服务端的错误码、state、message 或 detail，不承诺展示任意原始响应。无法证明
安全的内容宁可省略。

## 配置与 Docker 行为

不新增日志开关：

- `COMICLENS_LOG_LEVEL=INFO`：默认安全摘要；
- `COMICLENS_LOG_LEVEL=DEBUG`：增加 poll 细节和安全失败摘要；
- 更高级别继续按 Python logging 语义减少输出。

Docker stdout/stderr 是唯一日志目标，不创建日志文件。Compose 当前保留 json-file
`max-size=10m`、`max-file=3`，本次不修改轮转值。README 增加 `docker logs -f comiclens`、
Compose 查看命令、INFO/DEBUG 示例，以及 DEBUG 仍不包含成功正文的说明。

## 验证

新增 observability 单元测试，验证：

- 固定前缀为 `LEVEL service event=...`，没有时间戳和 logger 名称；
- 字段单行、稳定、可引用且长度受限；
- URL userinfo、query、fragment 和已知 secret 被移除；
- generation/job/request 短引用稳定且不会暴露原值；
- JSON 键脱敏、非 JSON 正文省略和 1024 字符上限。

使用 `httpx.MockTransport` 与 `caplog` 扩展各组件测试，覆盖：

- 漫画请求、响应、代理 route、重定向、重试和最终失败；
- OCR direct、job submit、poll 状态变化、结果下载、重试和协议失败；
- DeepL batch 与 DeepLX 单文本请求的成功、重试、认证/配额错误和协议失败；
- progressive 分片与旧页级路径的阶段、缓存命中/未命中、暂停、恢复、完成和失败；
- INFO 不含 poll 重复细节，DEBUG 包含每次 poll；
- 日志不含测试 Token、API Key、Basic/代理凭据、URL query、Base64、原文或译文。

全量验证继续运行后端测试和 Ruff。该任务不修改前端代码，前端构建仅作为发布前整体验收而非
日志功能的聚焦检查。

## 非目标

- 不记录完整请求或响应 body。
- 不记录成功 OCR JSON、原文或译文，即使启用 DEBUG。
- 不恢复 httpx/httpcore INFO 日志。
- 不引入 JSON structured logging、OpenTelemetry、指标、追踪后端或外部日志服务。
- 不新增日志数据库、设置页开关或动态修改日志级别 API。
- 不改变 Docker Compose 日志驱动和轮转大小。
- 不改变外部请求协议、重试、并发、代理、缓存或任务调度行为。
