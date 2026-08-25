# OCR 同步/异步适配与鉴权恢复设计

## 背景与目标

ComicLens 当前只支持 PaddleOCR 云端异步任务协议，并固定使用 Bearer Token。较早版本曾
支持同步 OCR、按 URL 自动识别同步或异步协议，以及无鉴权、Bearer Token、Basic Auth
三种鉴权方式，后来这些能力被删除。

本次在当前逐图、分片、任务恢复和动态并发实现之上恢复上述能力，不回退当前异步任务
实现。用户只需配置一个 OCR URL，并可显式选择协议，也可由 `auto` 在同步与异步任务
协议之间做最小判断。

目标包括：

- 恢复 `auto/direct/job` 三种 OCR 模式；
- 恢复 `none/bearer/basic` 三种鉴权模式；
- 支持 PaddleX 服务化部署提供的同步 `/layout-parsing` 请求与响应；
- 保持当前异步任务提交、任务 ID 持久化、轮询、恢复、结果下载和手动重试行为不变；
- 安全处理异步结果地址，避免向外部地址泄露 OCR 凭据；
- 无损迁移当前安装，并尽可能保留真正旧版本数据库中仍存在的设置。

## 总体方案

继续使用单个 `OCRClient`。`analyze_image` 在进入现有动态并发限制后解析本次使用的协议，
随后只进入同步或异步其中一条路径：

- `direct` 调用同步 JSON 接口并直接返回 OCR 结果；
- `job` 沿用当前 multipart 提交、轮询及 JSONL 下载流程；
- `auto` 只根据 URL 判断上述两种协议，不探测接口、不先发试探请求，也不增加第三种
  协议适配器。

保留当前 `analyze_image(image_bytes, job_id, on_job_submitted)` 调用边界。同步分支忽略
`job_id`，且不调用 `on_job_submitted`；异步分支继续使用两者，因此当前分片任务恢复和
OCR job 持久化代码无需分叉。

动态 OCR 并发限制继续覆盖一次完整 OCR 生命周期：同步模式下覆盖完整 POST，请求完成后
释放；异步模式下覆盖提交、轮询及结果下载全过程。并发默认值及运行时动态调整行为不变。

## 协议选择

设置 `ocr_mode` 接受：

- `auto`：默认值；
- `direct`：强制同步接口；
- `job`：强制异步任务接口。

`auto` 使用规范化后的 OCR URL 判断。路径以 `/ocr/jobs` 结尾时解析为 `job`；其余所有
URL 均解析为 `direct`。query 和尾部斜杠不影响判断。PaddleX 同步接口通常以
`/layout-parsing` 结尾，但客户端不会自动追加该后缀。

自动模式不识别或特殊处理 `/v1`，也不会因 URL 看起来不像 `/layout-parsing` 而提示、
拒绝或改写请求。未命中异步规则的地址一律按同步接口正常尝试，服务端返回的 HTTP 或
协议错误再按统一错误流程处理。

显式模式始终优先于 URL。`auto` 和显式模式若最终解析为同一协议，应具有相同的运行行为
及语义指纹，不因设置文字不同创建重复 generation。

## 同步 OCR 请求与响应

同步分支向配置的 OCR URL 发送 `POST application/json`。请求体为：

```json
{
  "file": "<图片字节的 Base64>",
  "fileType": 1,
  "useDocOrientationClassify": false,
  "useDocUnwarping": false,
  "useChartRecognition": false,
  "visualize": false
}
```

同步请求不发送异步协议使用的 `model`、`optionalPayload` 或 multipart 文件。响应必须是
JSON 对象并包含 `result`；否则抛出 `OCRProtocolError`。合法响应直接交给当前
`extract_text_blocks`，继续解析 `result.layoutParsingResults`，不新增第二套文字框提取
逻辑。

同步调用不会创建、保存或恢复 OCR job。同步阶段失败后由现有单图/分片失败状态承接；
用户手动重试会重新发送完整同步请求。

## 异步 OCR 兼容边界

`job` 分支保持当前实现：

1. multipart 提交 PNG、模型和 `optionalPayload`；
2. 读取并通过回调持久化 `data.jobId`；
3. 进程恢复时优先轮询已保存的 job，而不是重复提交；
4. 处理 `pending`、`running`、`done`、`failed` 和异常状态；
5. 校验并下载 HTTP(S) JSONL 结果，合并多条 `layoutParsingResults`；
6. 保留当前总超时、轮询间隔、429/5xx 与网络错误重试策略；
7. OCR 阶段失败后的手动重试仍清除旧 job ID 并创建新任务。

恢复同步能力不得改变上述请求字段、状态机、异常类型、job 恢复规则或外部结果解析规则。

## 鉴权设计

设置 `ocr_auth_mode` 接受：

- `none`：不发送认证信息；新安装默认值；
- `bearer`：向 OCR API 请求发送 `Authorization: Bearer <token>`；
- `basic`：使用配置的用户名和密码发送 HTTP Basic Auth。

鉴权按模式校验：OCR URL 始终必填；`none` 不要求凭据；`bearer` 要求非空 Token；
`basic` 同时要求非空用户名和密码。缺少当前模式所需配置时，在启动翻译前返回
`OCR_AUTH_NOT_CONFIGURED`，不因隐藏的另一模式凭据存在而放行。

同步 POST 使用当前选择的鉴权。异步任务提交和轮询也使用当前选择的鉴权。下载异步
`resultUrl` 时采用更严格的规则：

- Basic Auth 仅在结果 URL 与 OCR API 同源时携带；
- 同源按 URL 的 scheme、hostname 和有效端口共同判断；
- Basic Auth 不发送给跨源结果 URL；
- Bearer Token 无论结果 URL 是否同源都不发送；
- `none` 始终不发送认证信息。

这使受 Basic Auth 保护且在同一服务内提供结果的部署可以工作，同时避免向对象存储、
CDN 或其他外部结果主机泄露用户名、密码或 Token。结果 URL 仍必须通过现有 HTTP(S)
有效性校验。

## 设置、API 与界面

恢复以下服务设置：

- `ocr_mode`：`auto/direct/job`，默认 `auto`；
- `ocr_auth_mode`：`none/bearer/basic`，默认 `none`；
- `ocr_basic_username`：普通字符串；
- `ocr_basic_password`：敏感设置，加密保存并在公共 API 中只返回掩码状态。

保留现有 OCR URL、Token、模型、轮询间隔、总超时、并发和分片设置。OCR URL 继续作为
敏感设置加密保存。新安装的 OCR URL 默认值改为
`http://example.com/layout-parsing`，不使用任何真实部署地址或凭据作为默认值。

设置界面将“OCR 异步任务 URL”改为通用“OCR API URL”，并恢复协议和鉴权下拉框：

- `none` 不显示凭据输入；
- `bearer` 显示 Token；
- `basic` 显示用户名和密码；
- 切换模式只隐藏不相关字段，不主动清空已保存的其他凭据；
- 模型、轮询间隔、总超时和并发设置继续显示，以便切回异步模式时原值仍可编辑。

敏感字段继续使用现有 `keep/replace/clear` 更新协议。前端隐藏凭据时必须提交 `keep` 或
省略字段，不能误清空 Token 或 Basic 密码。

恢复 `COMICLENS_OCR_BASIC_USERNAME` 与 `COMICLENS_OCR_BASIC_PASSWORD` 环境变量作为
初始设置来源，并同步更新 Compose、`.env.example` 与 README。它们只初始化尚未建立的
设置，不覆盖数据库，也不自动切换 `ocr_auth_mode`；鉴权模式仍由设置明确选择。

## 设置迁移

递增设置 schema 版本，在现有事务式迁移中加入以下规则：

1. 全新数据库写入 `ocr_mode=auto`、`ocr_auth_mode=none`、空凭据，以及同步示例 URL
   `http://example.com/layout-parsing`。
2. 当前 schema v3 数据库缺少已删除的字段。迁移后写入 `ocr_mode=auto`；若解密后的
   `ocr_token` 非空，则写入 `ocr_auth_mode=bearer`，否则写入 `none`。
3. 现有非空 OCR URL、Token、模型、超时、轮询和并发值原样保留。现有空 URL 才使用
   新的同步示例 URL。
4. 更早数据库若仍包含合法的 `ocr_mode`、`ocr_auth_mode`、Basic 用户名或 Basic 密码，
   则保留这些值；缺失或非法模式再按 `auto` 及“有 Token 则 bearer，否则 none”归一化。
5. 曾经升级到删除字段版本的数据库已经永久丢失 Basic 用户名和密码，迁移不能恢复；
   此类安装按当前仍存在的 Token 推断鉴权模式。
6. 迁移只执行一次，后续重启不得重新根据 Token 覆盖用户主动选择的鉴权模式。

迁移判断必须使用解密后的敏感值，不能根据公共 API 掩码或数据库密文是否非空推断。

## Generation 语义与任务恢复

新的 generation 语义设置记录解析后的 `ocrProtocol`，值只可能是 `direct` 或 `job`，并将
它纳入语义指纹。URL、Token、Basic 用户名和密码都不写入语义设置、指纹或日志。

因此：

- `auto` 与显式模式解析成同一协议时可复用同一 generation；
- 同步与异步之间切换会创建语义不同的 generation，避免复用不兼容的 OCR 检查点；
- 读取旧 generation 时，若缺少 `ocrProtocol`，按 `job` 解释，因为当前已发布版本只会
  创建异步任务；这保证恢复中的旧 job 不会因新默认 URL 或模式被误当成同步调用。

构建 pipeline 时优先使用 generation 中已经解析并固定的 `ocrProtocol`，而不是再次读取
可能已改变的模式和 URL 来决定正在运行 generation 的协议。

## 错误处理

沿用当前错误体系，避免为恢复功能增加平行分类：

- 配置缺失使用 `OCR_NOT_CONFIGURED` 或 `OCR_AUTH_NOT_CONFIGURED`；
- 同步响应不是 JSON 对象或缺少 `result` 使用 `OCRProtocolError`；
- 异步协议继续使用 `OCRProtocolError`、`OCRJobFailedError` 和
  `OCRJobNotFoundError`；
- HTTP 401/403 作为普通 OCR HTTP 错误，不重试；
- HTTP 429、5xx、网络错误和请求超时沿用当前最多三次及指数退避策略；
- 最终请求超时继续由 manager 映射为 OCR 阶段超时；
- URL 未命中自动异步规则但实际不是同步接口时，不预先拒绝，由真实 HTTP 或响应格式
  错误说明失败。

错误消息、请求日志和诊断信息不得包含 Token、Basic 密码、完整 Authorization 头或
用户提供的真实联调凭据。

## 验证策略

后端测试覆盖：

- `auto/direct/job` 的协议解析，包括 `/ocr/jobs` 后缀、普通 URL、尾部斜杠、query、
  `/ocr/jobs/123`、`/ocr/jobs-old` 和 `/v1` 按同步尝试；
- 同步 Base64 JSON 请求的完整字段、Content-Type、正常响应和异常响应；
- 同步分支忽略旧 `job_id`，且不调用 job 提交回调；
- `none/bearer/basic` 请求行为及各模式的配置校验；
- Basic 同源结果下载携带认证、跨源结果下载不携带认证，以及 Bearer 不发送给结果 URL；
- 当前异步 multipart 字段、提交、轮询、结果合并、已保存 job 恢复和手动重试创建新 job
  的回归测试；
- 同步与异步均继续受动态并发限制；
- 新安装默认值、v3 Token 到鉴权模式推断、真正旧字段保留、迁移只执行一次；
- Basic 密码加密与掩码、用户名公开字段、敏感字段 `keep/replace/clear`；
- 解析后的协议进入 generation 语义指纹，旧 generation 缺失字段按 `job` 恢复。

前端验证覆盖协议与鉴权条件显示、隐藏凭据保留、默认值、设置保存和 TypeScript 类型。
最终运行完整 Pytest、Ruff、前端格式检查、Lint、类型检查和生产构建。

可使用用户临时提供的同步 OCR 服务做一次手动联调，验证 Basic Auth、同步响应解析和实际
文字框提取。该服务地址、用户名、密码及返回内容不得写入源码、默认值、测试夹具、文档、
日志样例或 Git 提交；自动化测试全部使用本地 mock。

## 文档更新

README 和部署示例应说明：

- ComicLens 可连接 PaddleOCR 同步服务化接口或异步任务接口；
- `auto` 只按 URL 区分两者，其他 URL 默认按同步尝试；
- 三种出站 OCR 鉴权模式的用途及所需字段；
- Basic 凭据在异步结果 URL 上的同源限制；
- 默认 URL 只是示例，用户必须替换为自己的服务地址。

## 不在范围内

- 不为 `/v1` 或 OpenAI 兼容 VLM 接口增加专用请求/响应适配；
- 不做运行时协议探测、同步失败后自动回退异步或反向回退；
- 不新增 API Key 请求头、自定义 Header、OAuth 或其他鉴权方式；
- 不改变 ComicLens 自身入站访问密码或增加 OCR 服务端鉴权功能；
- 不改变异步任务状态机、OCR 并发模型、长图分片、翻译、渲染和缓存策略；
- 不把任何临时真实 OCR 地址或凭据纳入项目。
