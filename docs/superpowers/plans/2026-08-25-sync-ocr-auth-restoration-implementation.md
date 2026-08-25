# OCR 同步/异步适配与鉴权恢复实施计划

日期：2026-08-25  
依据：`docs/superpowers/specs/2026-08-25-sync-ocr-auth-restoration-design.md`

## 实施原则

- 在当前 `OCRClient` 中增加一个窄同步分支，不回退或复制当前异步状态机。
- 先建立设置与迁移，再接入运行时，保证代码读取字段时数据库一定具备对应值。
- 自动模式只解析为 `direct` 或 `job`；pipeline 使用 generation 中固定的解析结果。
- 凭据不进入语义指纹、日志、测试输出或 Git；自动化测试只使用虚构值和
  `httpx.MockTransport`。
- 每阶段先补回归测试，再做最小实现，聚焦验证后再进入下一阶段。

## 阶段 1：设置模型、默认值与迁移

修改：

- 在 `app/application/settings.py` 将 schema 升级到 v4，恢复 `ocr_mode`、
  `ocr_auth_mode`、`ocr_basic_username` 和加密的 `ocr_basic_password`。
- 将新安装 OCR URL 默认值改为 `http://example.com/layout-parsing`。
- 实现 v3 数据库的 Token 推断，以及更早数据库合法模式和 Basic 凭据保留规则。
- 在 `app/domain/settings.py` 恢复 API DTO、校验范围和敏感密码更新协议。
- 在 `app/config.py` 恢复两个 Basic 环境变量种子。
- 更新 `tests/test_settings_auth.py` 和 `tests/test_app.py`，覆盖默认值、迁移幂等、旧字段
  保留、掩码及 `keep/replace/clear`。

验证：

- `pytest tests/test_settings_auth.py tests/test_app.py`
- `ruff check app/application/settings.py app/domain/settings.py app/config.py tests/test_settings_auth.py tests/test_app.py`

## 阶段 2：OCR 协议与鉴权适配

修改：

- 在 `app/translation/ocr.py` 增加纯协议解析函数，支持显式 `direct/job` 及最小 URL 自动
  判断。
- 扩展 `OCRClient` 构造参数，按 `none/bearer/basic` 构建 OCR API 鉴权。
- 增加同步 Base64 JSON POST，并将非法 JSON 或缺少 `result` 统一转换为
  `OCRProtocolError`。
- 保持异步提交、轮询、job 恢复及 JSONL 合并代码不变，只把认证构建接入现有请求点。
- 下载结果时只向同源 URL携带 Basic；Bearer 不发送给任何结果 URL。
- 扩展 `tests/test_translation_algorithms.py`，覆盖协议解析、同步请求、三种认证、同源判断、
  job 回归、回调边界及重试。

验证：

- `pytest tests/test_translation_algorithms.py`
- `ruff check app/translation/ocr.py tests/test_translation_algorithms.py`

## 阶段 3：Manager、pipeline 与 generation 接入

修改：

- 在 `app/translation/manager.py` 按鉴权模式校验必需凭据，并将解析后的
  `ocrProtocol` 写入 generation 语义及指纹。
- 构建 OCR pipeline 时使用 generation 固定协议；旧 generation 缺失字段时按 `job`。
- 保留 `app/translation/pipeline.py` 和 `app/translation/segment_runner.py` 的现有 job 参数
  调用边界，让同步客户端自行忽略 job ID 和观察回调。
- 更新 manager harness 与 `tests/test_translation_manager.py`，验证 auto/显式等价、协议切换
  新建 generation、旧异步 generation 恢复，以及按模式配置错误。

验证：

- `pytest tests/test_translation_manager.py tests/test_translation_algorithms.py`
- `ruff check app/translation app/domain tests/test_translation_manager.py`

## 阶段 4：设置界面与客户端类型

修改：

- 在 `web/src/domain/api.ts` 恢复 OCR 模式、鉴权模式、Basic 用户名和敏感密码类型。
- 在 `web/src/routes/_app/settings.tsx` 增加协议及鉴权选择器，将 URL 标签改为通用名称。
- `bearer` 只显示 Token，`basic` 只显示用户名和密码，`none` 隐藏凭据；隐藏敏感值继续
  保持 `keep`。
- 保留模型、轮询、超时、并发和分片设置的现有位置与行为。

验证：

- `npm run fmt:check`
- `npm run lint`
- `npm run build`

## 阶段 5：部署示例与用户文档

修改：

- 更新 `.env.example`、Compose 环境传递和 README，恢复 Basic 环境变量。
- 将 OCR URL 示例改为 `http://example.com/layout-parsing`，说明同步、异步和 auto 判断。
- 说明三种鉴权及异步结果 URL 的同源 Basic 限制。
- 检查所有改动，确保不含临时真实服务地址、端口、用户名、密码或响应内容。

验证：

- 运行配置相关测试。
- 对项目跟踪文件执行敏感信息扫描和 `git diff --check`。

## 阶段 6：全量验收与临时联调

执行：

- `pytest`
- `ruff check app tests`
- 在 `web` 中运行 `npm run fmt:check`、`npm run lint`、`npm run build`
- 使用进程内临时配置或独立命令请求用户提供的同步服务，验证 Basic Auth、同步 OCR 响应和
  当前文字框提取；不创建 fixture、不写配置文件、不输出凭据。
- 检查当前异步单元测试及 job 恢复测试全部通过。
- 检查 `git status`、提交内容和敏感信息扫描结果，确认只交付本任务文件。

## 回滚与数据安全

- 设置迁移继续在单个 SQLite 事务中执行，失败时不得留下部分 v4 数据。
- 不删除 translation generation、segment、OCR 缓存或媒体缓存。
- 不改动用户本地数据库和部署配置；测试使用临时数据库。
- 不使用破坏性 Git 命令覆盖工作区；发现与用户改动冲突时停止并报告。
