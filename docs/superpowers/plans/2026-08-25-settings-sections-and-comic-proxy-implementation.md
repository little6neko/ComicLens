# 设置分区与漫画代理实施计划

依据：`docs/superpowers/specs/2026-08-25-settings-sections-and-comic-proxy-design.md`

## 阶段 1：设置结构版本 5 与代理字段改名

涉及文件：

- `app/application/settings.py`
- `app/domain/settings.py`
- `app/config.py`
- `tests/test_settings_auth.py`
- `tests/test_app.py`

步骤：

1. 先增加失败测试，覆盖：
   - 新安装的设置 API 包含未配置的 `proxyUrl`，不包含 `fallbackProxyUrl`；
   - `proxyUrl` 的替换、掩码、保留和清除仍使用敏感字段协议；
   - 设置结构版本 4 升级时，即使旧 `fallback_proxy_url` 是非空密文，也会删除旧键并生成空的 `proxy_url`，不复制旧值；
   - 升级时若提供 `initial_settings["proxy_url"]`，新字段采用该初值；
   - `COMICLENS_PROXY_URL` 映射到 `initial_settings["proxy_url"]`。
2. 将设置结构版本提升到 5，把设置定义、Pydantic 响应和 Patch 字段统一改为 `proxy_url`。
3. 不增加旧值映射分支；让现有 `_migrate_values` 和 `_replace_settings` 按新定义重建并自然丢弃旧键。
4. 将 `AppConfig.from_env` 的初始设置键改为 `proxy_url`，环境变量名仍为 `COMICLENS_PROXY_URL`。
5. 确认敏感值不会出现在 API 明文、数据库字节或验证错误中。
6. 运行：
   - `.venv/bin/pytest -q tests/test_settings_auth.py tests/test_app.py`
   - `.venv/bin/ruff check app/application/settings.py app/domain/settings.py app/config.py tests/test_settings_auth.py tests/test_app.py`

本阶段先保持调用方可编译；若字段改名使应用装配必须同步更新，则与阶段 2 的后端改动一起提交。

## 阶段 2：漫画请求改为单线路代理选择

涉及文件：

- `app/sources/manga18fx.py`
- `app/main.py`
- `tests/test_manga18fx_source.py`
- 视测试装配需要更新 `tests/conftest.py` 或相关应用测试

步骤：

1. 将原有“直连失败后使用回退代理”的测试替换为以下失败测试：
   - 配置显式漫画代理时，成功请求只调用代理客户端，常驻客户端调用次数为零；
   - 显式代理发生可重试失败时，全部内部重试仍使用代理客户端，最后不会调用常驻客户端；
   - 显式代理同样覆盖 `fetch_media` 源图请求；
   - 显式代理下的安全重定向继续使用同一客户端；
   - 未配置应用代理时使用常驻客户端；默认构造的常驻客户端启用 `trust_env`。
2. 将构造参数和内部提供器从 `fallback_proxy_provider` / `_fallback_proxy_url` 改为 `proxy_provider` / `_proxy_url`，删除所有 `fallback` 命名。
3. 调整常驻漫画客户端：
   - 保持超时、请求头和安全重定向策略；
   - 启用 httpx 默认环境代理读取；
   - 不读取或解析任何代理环境变量。
4. 调整 `_request`：
   - 请求开始时读取一次数据库代理设置；
   - 非空时只创建并使用 `proxy=proxy_url, trust_env=False` 的显式代理客户端；
   - 空值时只使用启用环境代理的常驻客户端；
   - 两条路径都复用现有 `_request_with_retries`，不在失败后切换客户端。
5. 删除仅用于判断是否触发回退的 `_is_retryable`；保留仍被错误分类使用的辅助函数。
6. 在应用装配中提供 `proxy_url`，确保设置保存后新漫画请求立即读取新值。
7. 运行：
   - `.venv/bin/pytest -q tests/test_manga18fx_source.py tests/test_settings_auth.py tests/test_app.py`
   - `.venv/bin/ruff check app/sources/manga18fx.py app/main.py tests/test_manga18fx_source.py`
8. 提交后端设置与漫画代理改动，例如：`feat: route comic requests through configured proxy`。

## 阶段 3：锁定 OCR 与翻译的环境代理边界

涉及文件：

- `app/translation/manager.py`（仅在需要显式表达默认值时修改）
- `tests/test_translation_manager.py`
- 可能补充 `tests/test_catalog_api.py`

步骤：

1. 增加回归测试，确认翻译管理器共享的 httpx 客户端启用 `trust_env`。
2. 保存或清除 `proxyUrl` 后，确认 OCR、DeepL、DeepLX 使用的仍是同一个共享客户端，且管线构造不读取漫画代理设置。
3. 若当前 httpx 默认行为已满足要求，保持实现不变；只有测试无法稳定表达契约时才显式传入 `trust_env=True`。不得手动读取 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 或 `NO_PROXY`。
4. 确认源图下载仍走 `ComicSource.fetch_media`，不会误用 OCR/翻译共享客户端。
5. 运行：
   - `.venv/bin/pytest -q tests/test_translation_manager.py tests/test_catalog_api.py`
   - `.venv/bin/ruff check app/translation/manager.py tests/test_translation_manager.py tests/test_catalog_api.py`
6. 如本阶段产生代码改动，单独提交，例如：`test: lock outbound proxy boundaries`。

## 阶段 4：前端设置分区

涉及文件：

- `web/src/domain/api.ts`
- `web/src/routes/_app/settings.tsx`

步骤：

1. 将前端领域模型和敏感字段联合类型从 `fallbackProxyUrl` 改为 `proxyUrl`，不保留旧字段别名。
2. 调整 `Draft`、`SecretKey`、`secretKeys`、`toDraft` 和提交 Patch，确保代理仍支持保留、替换和清除。
3. 将原“OCR 与翻译”分区拆为：
   - “OCR”：源语言、OCR 模式/鉴权/API/模型/轮询/超时/并发；
   - 现有“OCR 长图高级设置”：紧随 OCR，继续默认折叠；
   - “翻译”：服务、DeepL/DeepLX 凭据、超时和并发；
   - “代理”：位于翻译之后、缓存之前，只包含“漫画代理 URL”。
4. 为代理字段增加短提示：设置后漫画目录、搜索、详情、章节和源图只走该代理；留空时遵循标准代理环境变量；OCR 与翻译不使用此字段。
5. 使用现有设置卡、两列网格和敏感字段组件，不新增嵌套表单或独立保存按钮。
6. 选择与现有图标体系一致的独立 OCR、翻译和代理图标，检查窄屏换行与折叠标题。
7. 运行：
   - `cd web && npm run fmt`
   - `cd web && npm run fmt:check`
   - `cd web && npm run lint`
   - `cd web && npm run build`
8. 提交前端改动，例如：`feat(web): split OCR translation and proxy settings`。

## 阶段 5：用户文档更新

涉及文件：

- `README.md`

步骤：

1. 将“设置 → OCR 与翻译”改为新的四分区说明，删除“回退代理”措辞。
2. 说明 `COMICLENS_PROXY_URL` 只为“漫画代理 URL”提供初值；配置后漫画请求只走该代理。
3. 说明未配置应用漫画代理时，httpx 自动遵循标准代理环境变量；这些环境变量也可影响 OCR 和翻译，并受 `NO_PROXY` 控制。
4. 不把任何真实代理、OCR 地址或凭据写入示例。
5. 运行 Markdown 与差异检查，并与前端提交合并或单独提交文档。

## 阶段 6：完整验证与 UI 验收

1. 运行完整后端验证：
   - `.venv/bin/pytest -q`
   - `.venv/bin/ruff check app tests`
2. 运行完整前端验证：
   - `cd web && npm run fmt:check`
   - `cd web && npm run lint`
   - `cd web && npm run build`
3. 运行：
   - `git diff --check`
   - 在 `app/`、`web/src/`、`tests/` 和 `README.md` 中搜索并确认不再存在 `fallback_proxy_url`、`fallbackProxyUrl`、`fallback_proxy_provider` 或“回退代理”；历史设计文档不做追溯改写。
4. 使用临时数据目录和最新 `web/dist` 重启 `0.0.0.0:8233` 测试服务。
5. 验证设置 API：
   - 返回 `proxyUrl`；
   - 不返回 `fallbackProxyUrl`；
   - 替换、保留、清除及掩码行为正确。
6. 在桌面和手机宽度验收设置页：
   - OCR、OCR 长图高级设置、翻译、代理顺序正确；
   - 高级设置默认折叠；
   - 条件字段与统一保存按钮正常；
   - 代理提示没有溢出或造成卡片拥挤。
7. 不调用用户的线上 OCR、翻译或代理服务，不保存真实凭据。
8. 检查提交历史和工作区状态；只提交必要收尾改动，并保持测试服务运行供用户查看。

## 完成条件

- 应用内漫画代理设置非空时，漫画目录与源图只走该代理，失败不直连。
- 应用内漫画代理为空时，漫画、OCR 和翻译按各自 httpx 客户端自然遵循环境代理或直连。
- 旧回退代理键和值被直接丢弃；后端、API 和前端只使用 `proxy_url` / `proxyUrl`。
- 设置页分区和帮助文字符合已确认布局。
- 全量后端、前端、响应式 UI 和敏感信息检查全部通过。
