# 外部请求与翻译管线安全日志实施计划

日期：2026-08-25
依据：`docs/superpowers/specs/2026-08-25-safe-external-request-and-pipeline-logging-design.md`

## 实施原则

- 保持 `httpx` / `httpcore` 为 `WARNING`，在拥有业务语义和敏感值边界的调用点显式记录日志。
- 日志只观察现有流程，不改变请求次数、重试判断、延迟、超时、并发、代理、缓存或任务状态。
- 所有业务事件都经统一辅助模块输出，不允许各客户端自行拼接 URL、异常字符串或响应正文。
- 固定前缀为 `LEVEL service event=...`；不添加时间戳或 logger 名称。
- 先建立并验证脱敏基础设施，再逐个接入客户端，最后接入任务上下文和缓存事件。
- 每阶段先补失败测试，再做最小实现；测试使用虚构凭据和 `httpx.MockTransport`。

## 阶段 1：日志格式、上下文与脱敏基础设施

修改：

- 新增 `app/observability.py`，实现单行字段编码、事件输出、日志级别选择和稳定字段顺序。
- 实现安全 endpoint：删除 userinfo、query、fragment，规范端口，支持仅 origin 模式、已知 secret
  替换和长度限制。
- 实现 request/generation/job 短引用，以及基于 `contextvars` 的嵌套异步任务上下文。
- 实现 DEBUG JSON 错误摘要：敏感键递归替换、已知 secret 与原文替换、URL 清理、1024 字符
  上限；非 JSON 正文只返回元数据。
- 调整 `app/main.py` 的应用日志格式为 `%(levelname)s %(message)s`，保留 Uvicorn formatter 和
  `httpx/httpcore=WARNING`。
- 新增 `tests/test_observability.py` 并更新 `tests/test_app.py`，覆盖格式、上下文隔离、引用、URL、
  JSON 脱敏、控制字符、截断和 logger 级别。

验证：

- `.venv/bin/pytest tests/test_observability.py tests/test_app.py`
- `.venv/bin/ruff check app/observability.py app/main.py tests/test_observability.py tests/test_app.py`

## 阶段 2：漫画来源请求日志

修改：

- 在 `app/sources/manga18fx.py` 的业务入口向请求层传递
  `home/search/category/ranking/detail/chapter_manifest/fetch_media` 操作名。
- 在每次实际 GET 前后记录 request/response，包含 request_ref、方法、安全 endpoint、route、
  attempt、状态、耗时、大小和 content type。
- 对安全重定向、429/5xx、网络异常和最终失败记录 response、retry 或 failed，复用现有重试
  决策和延迟。
- configured proxy 只记录 `route=configured_proxy`；其余记录
  `route=environment_or_direct`，不读取或输出代理环境变量、代理 URL 和凭据。
- 扩展 `tests/test_manga18fx_source.py`，覆盖直连/环境边界、应用代理、媒体、重定向、重试、
  最终失败、query/userinfo 脱敏和请求次数不变。

验证：

- `.venv/bin/pytest tests/test_manga18fx_source.py tests/test_observability.py`
- `.venv/bin/ruff check app/sources/manga18fx.py tests/test_manga18fx_source.py`

## 阶段 3：OCR 请求、轮询与响应日志

修改：

- 在 `app/translation/ocr.py` 为 direct analyze、job submit、poll、result download 传递明确 operation
  和安全元数据。
- 记录图片字节数、payload 大小、protocol、auth 枚举、模型、状态、耗时、响应大小和 layout
  result 数量，不记录图片/Base64、Token 或 Basic 凭据。
- poll 的每次 request/response 只在 DEBUG；在解析状态后仅对状态变化输出 INFO，并使用 job_ref
  关联恢复或新建任务。
- 结果下载 endpoint 只保留 origin；重试和最终失败沿用现有逻辑并增加安全事件。
- 对非 2xx 和协议解析失败在 DEBUG 输出允许的脱敏 JSON 摘要，成功响应永不输出正文。
- 扩展 `tests/test_translation_algorithms.py`，覆盖 direct/job、poll 降噪、恢复 job、重试、失败
  摘要、结果 URL 和凭据/Base64/正文不泄漏。

验证：

- `.venv/bin/pytest tests/test_translation_algorithms.py tests/test_observability.py`
- `.venv/bin/ruff check app/translation/ocr.py tests/test_translation_algorithms.py`

## 阶段 4：DeepL 与 DeepLX 请求日志

修改：

- 在 `app/translation/translator.py` 为 DeepL batch 和 DeepLX 单文本请求增加业务日志。
- DeepL 记录 texts、source_chars、payload_bytes、语言、返回数量、状态和耗时；DeepLX 记录单次
  chars、payload_bytes、状态和耗时。
- 接入现有 429/5xx、网络错误、DeepL 鉴权/配额错误和最终失败分支，不改变异常映射。
- DEBUG 失败摘要必须清除 API Key、DeepLX URL query、原文和译文；成功响应不打印正文。
- 扩展 `tests/test_translation_algorithms.py`，覆盖两种服务的成功、并发、重试、认证/配额、协议
  异常和敏感信息扫描。

验证：

- `.venv/bin/pytest tests/test_translation_algorithms.py tests/test_observability.py`
- `.venv/bin/ruff check app/translation/translator.py tests/test_translation_algorithms.py`

## 阶段 5：任务阶段、缓存与关联上下文

修改：

- 在 `app/translation/manager.py` 的 generation、页和分片边界设置 observability context，确保
  OCR 预取和翻译并发任务继承正确上下文。
- 记录任务开始、暂停、恢复、完成、取消、失败及批量失败重试摘要，覆盖 progressive 与旧版
  generation 路径。
- 在 `app/translation/segment_runner.py` 和旧页级执行路径记录 ocr/translation/render 的
  stage_start、stage_complete 和 stage_failed。
- 只在决定复用或重新生成 OCR、blocks、translations、translated image 的分支记录 cache_hit
  或 cache_miss，不给 MediaCache 底层统一加 INFO。
- 完成事件记录 blocks、translated、输入/输出字节数和 duration；失败只记录安全错误类别或应用
  错误码，不直接记录异常字符串。
- 扩展 `tests/test_translation_manager.py`，覆盖上下文隔离、缓存、阶段、暂停/恢复、完成/失败、
  OCR 预取并发及请求行为不变。

验证：

- `.venv/bin/pytest tests/test_translation_manager.py tests/test_translation_algorithms.py`
- `.venv/bin/ruff check app/translation/manager.py app/translation/segment_runner.py tests/test_translation_manager.py`

## 阶段 6：文档、全量验收与 Docker 日志预览

修改与执行：

- 更新 README 的日志说明，增加 INFO/DEBUG 示例、`docker logs -f comiclens`、Compose 查看命令、
  成功正文永不输出和现有 `10 MB × 3` 轮转说明。
- 检查所有示例只使用虚构服务、标识和凭据，不写入用户真实 OCR 地址或认证信息。
- `.venv/bin/pytest`
- `.venv/bin/ruff check app tests`
- 在 `web` 中运行 `npm run fmt:check`、`npm run lint` 和 `npm run build`，确认后端改动不影响发布
  构建。
- 使用隔离临时数据库与 MockTransport 场景生成 INFO 和 DEBUG 日志预览，验证固定格式、poll
  降噪、阶段上下文和重试事件。
- 重启当前 `0.0.0.0:8233` 临时测试服务到新提交，验证 Docker 等价 stdout 输出和健康状态；
  不接入真实 OCR、翻译或代理凭据。
- 执行 `git diff --check`、状态检查和真实服务地址/凭据扫描。

## 回滚与安全

- 不修改设置 schema、用户数据库结构或缓存格式，回滚代码不需要数据迁移。
- 不删除用户任务、缓存、数据库或现有临时服务数据；自动化测试使用临时目录。
- 日志测试中的 canary secret 必须断言不出现在消息、formatter 输出和错误摘要中。
- 任何无法证明安全的响应内容都省略，不以“方便排查”为由放宽正文或凭据边界。
- 不使用破坏性 Git 命令；发现用户改动重叠时停止并报告。
