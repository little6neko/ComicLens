# ComicLens 异步 OCR 与 DeepL 官方 API 设计

## 目标

将 OCR 收敛为 PaddleOCR 云端异步任务协议，升级默认模型至 `PaddleOCR-VL-1.6`，并在保留 DeepLX 的同时增加 DeepL 官方 API。新安装默认使用 DeepL 与自动源语言识别；旧安装按已有配置无损迁移。

本次不改变逐张图片处理、长图切片、译图逐张显示、单图重试、整话重新翻译或缓存上限行为。

## 服务设置

### OCR

OCR 设置只保留以下字段：

- 异步任务 URL；默认值为 `https://paddleocr.aistudio-app.com/api/v2/ocr/jobs`。
- Bearer Token；作为敏感值加密保存。
- 模型；默认值为 `PaddleOCR-VL-1.6`。
- 轮询间隔；默认值继续为 2 秒。
- 总超时；默认值继续为 180 秒。
- 并发数；默认值继续为 1。

删除 OCR 同步解析分支以及 `auto/direct/job` 模式设置。删除无认证、Basic Auth 认证选项及 Basic 用户名、密码设置。OCR 请求固定使用 `Authorization: Bearer <token>`；启动翻译时 URL 或 Token 未配置应返回明确的不可重试配置错误。

### 翻译

新增翻译服务设置 `deepl/deeplx`：

- 新安装默认 `deepl`。
- DeepL 使用加密保存的 API Key，不提供自定义 API URL。
- DeepLX 保留加密保存的服务 URL。
- 切换服务不清除另一服务的凭据，但运行时只调用当前选择的服务；请求失败不自动回退。
- 原 `deeplx_timeout_seconds` 迁移为共用的 `translation_timeout_seconds`，数值及 30 秒默认值保持不变。
- 翻译并发设置继续保留。它约束 DeepLX 的逐条请求，也约束 DeepL 批次请求。

源语言设置改为 `AUTO/EN/KO`，新安装默认 `AUTO`。目标语言在界面中固定显示为简体中文：DeepL 使用 `ZH-HANS`，DeepLX 使用其兼容值 `ZH`。

## 一次性升级迁移

新增内部元数据表及持久化设置版本标记，由服务器在 SettingsService 初始化时于单个数据库事务内执行一次。初始化前若 `app_settings` 为空，则直接写入全部新默认值并记录当前版本；若已有设置但没有当前版本标记，则视为旧安装并执行迁移。迁移必须在解密旧敏感设置后决策，不能根据掩码或密文判断。

迁移规则如下：

1. 源语言为 `KO` 时保留；`EN`、空值及其他旧版本允许但新版本不支持的值均改为 `AUTO`。已经是 `AUTO` 时保持不变。
2. OCR 模型为空或等于 `PaddleOCR-VL-1.5` 时改为 `PaddleOCR-VL-1.6`；其他自定义模型原样保留。
3. OCR URL 为空时写入新的异步任务 URL；自定义 URL原样保留。
4. 已配置非空 DeepLX URL 的旧安装选择 `deeplx`；否则选择 `deepl`。
5. 将原 DeepLX 超时值复制到共用翻译超时；若不存在则使用 30 秒。
6. 删除 `ocr_mode`、`ocr_auth_mode`、`ocr_basic_username`、`ocr_basic_password` 和 `deeplx_timeout_seconds` 等废弃记录。
7. OCR 轮询间隔、总超时、并发和用户其他设置均不改动。

全新数据库直接写入新默认值，不经过旧配置推断。迁移标记保证服务重启不会再次把用户后来手动选择的 `EN`、模型或翻译服务改回默认值。

## PaddleOCR 异步适配器

OCR 客户端只实现任务协议。每张普通图片或每个长图 OCR 分片以 multipart 表单提交：

- `file`: PNG 图片文件；
- `model`: 当前模型；
- `optionalPayload`: JSON 字符串，包含关闭文档方向分类、文档展平和图表识别的三个布尔参数。

提交响应必须包含 `data.jobId`。客户端随后按设置间隔请求 `GET {job_url}/{job_id}`：

- `pending`、`running`：继续等待；
- `done`：读取 `data.resultUrl.jsonUrl`；
- `failed`：使用 `data.errorMsg` 形成该图片的 OCR 失败；
- 未知状态：视为协议错误，而非无限轮询；
- 超过总超时：形成可重试的 OCR 超时。

下载 JSONL 结果前验证 URL 必须为 HTTP(S)。任务提交和轮询携带 OCR Bearer Token；外部结果 URL 不携带 Token，避免向对象存储泄露凭据。逐行解析 JSONL，将各条 `result.layoutParsingResults` 合并后交给现有文字框提取逻辑。空结果、缺少字段或非法 JSON均形成明确的协议错误。

请求保留现有网络错误和 429/5xx 重试策略。长图仍先按现有阈值裁切；当前原图的全部 OCR 分片完成、坐标还原并去重后，才进入该图的翻译阶段。

## DeepL 官方适配器

DeepL API Key 以 `:fx` 结尾时使用 `https://api-free.deepl.com/v2/translate`，否则使用 `https://api.deepl.com/v2/translate`。请求使用 JSON 和 `Authorization: DeepL-Auth-Key <key>`。

每张原图的 OCR 文本块按原顺序分批：

- 每批最多 50 个文本；
- 序列化后的完整请求体必须低于 DeepL 的 128 KiB 限制；
- 单个文本若无法装入限制，直接形成明确的翻译输入过大错误；
- 各批可在翻译并发限制内执行，但结果必须按批次及文本原始索引稳定合并。

请求体的 `text` 为字符串数组，`target_lang` 固定为 `ZH-HANS`。源语言为 `AUTO` 时省略 `source_lang`，为 `EN` 或 `KO` 时发送对应代码。响应必须包含与输入等长、同序的 `translations` 数组，每项读取 `text`；缺失或数量不一致视为协议错误。

DeepLX 继续使用现有逐文本块请求协议。`AUTO` 映射为 DeepLX 的 `auto`，手动语言映射为 `EN/KO`，目标为 `ZH`。DeepL 与 DeepLX 共享翻译超时及并发设置，但互不回退。

DeepL 的 401/403 作为认证错误、456 作为配额错误、429 作为限流错误，其余 HTTP、网络、超时和响应格式错误保留可定位的错误类别。错误仍落到现有单图失败状态，用户可以点击单图重试或重新翻译本话。

## 逐图数据流与缓存

处理顺序维持为：下载当前原图 → 当前图 OCR（必要时切片）→ 当前图批量翻译 → 当前图渲染及缓存 → 前端显示当前译图 → 下一张图。不会等待整话 OCR 完成。

翻译服务、源语言、OCR 模型以及既有图像语义参数写入翻译 generation 的语义指纹。DeepL Key、Token 和 URL本身不写入指纹或日志；切换翻译服务、语言或模型会创建新 generation，避免复用语义不兼容的旧翻译。OCR JSON、文字框、译文和译图继续按现有缓存规则长期保留。

## 设置界面与 API

“OCR 与翻译”区域按以下顺序呈现：

1. 源语言：自动识别（默认）、英语、韩语；目标语言提示固定为简体中文。
2. OCR 异步任务 URL、OCR Token、OCR 模型、轮询间隔、总超时和并发。
3. 翻译服务：DeepL 官方 API、DeepLX。
4. 选择 DeepL 时显示 API Key，并说明 Key 以 `:fx` 结尾会自动使用 Free API，否则使用 Pro API。
5. 选择 DeepLX 时显示 DeepLX URL。
6. 共用翻译超时和并发。

OCR URL与模型在新安装中是真实默认值，输入提示也分别使用新 URL和 `PaddleOCR-VL-1.6`。删除模式、认证模式和 Basic Auth 控件。隐藏的另一翻译服务凭据继续以“保留”动作提交，不因切换而清空。

公共设置响应增加翻译服务和 DeepL Key 的掩码状态，移除废弃字段，并将翻译超时改为通用名称。DeepL Key、OCR Token、OCR URL和 DeepLX URL均不返回明文。

环境变量初始化增加 `COMICLENS_DEEPL_API_KEY`，保留 `COMICLENS_DEEPLX_URL`。OCR URL与模型的示例默认值更新；删除 Basic Auth 初始化变量。环境变量仍只填充尚未初始化的服务器设置，不能覆盖数据库中已有值。

## 验证

后端测试覆盖：

- 新数据库默认设置及所有旧设置迁移分支，并验证迁移只运行一次；
- OCR multipart 字段、Bearer 认证、任务状态轮询、失败、未知状态、超时、JSONL 合并、外部结果鉴权隔离与重试；
- DeepL Free/Pro 主机选择、认证头、`AUTO/EN/KO`、`ZH-HANS`、50 条和 128 KiB 分批边界、稳定结果映射及响应格式检查；
- DeepL 认证、配额、限流、超时和网络错误分类；
- DeepLX 的 `AUTO` 映射及共用超时；
- 设置校验、敏感值加密/掩码、翻译服务不回退和语义指纹变化。

前端验证覆盖设置项条件显示、保留隐藏凭据、默认值、迁移后显示和保存请求。最终运行完整 pytest、Ruff、前端格式检查、lint、TypeScript 检查和生产构建，再重启服务做健康与设置响应检查。

## 文档与兼容性

更新 README、`.env.example` 和部署说明，将翻译能力表述为 DeepL 官方 API（默认）或 DeepLX，并说明 PaddleOCR 仅支持异步任务接口。已有翻译缓存与数据库继续可读；废弃设置不再暴露。

官方协议依据：

- [DeepL 认证和 Free/Pro API 主机](https://developers.deepl.com/docs/getting-started/auth)
- [DeepL 文本翻译接口](https://developers.deepl.com/api-reference/translate/request-translation)
- [DeepL 请求及用量限制](https://developers.deepl.com/docs/resources/usage-limits)
- [DeepL 支持语言](https://developers.deepl.com/docs/getting-started/supported-languages)

## 不在范围内

- 不增加 DeepL 与 DeepLX 自动故障回退。
- 不增加 DeepL 自定义端点、术语表、上下文、正式程度或用量查询。
- 不调整 OCR 轮询间隔、总超时或并发的现有默认值。
- 不把 OCR 扩展为整话并行预处理；仍按原图顺序逐图完成并显示。
