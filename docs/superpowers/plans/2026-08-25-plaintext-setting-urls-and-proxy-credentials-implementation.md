# 设置 URL 明文与独立代理凭据实施计划

日期：2026-08-25
依据：`docs/superpowers/specs/2026-08-25-plaintext-setting-urls-and-proxy-credentials-design.md`

## 实施原则

- 复用设置表的通用事务重建流程，把旧密文 URL 解密后按普通字段原样写回，不增加专用迁移
  分支，也不解析、拆分或规范化代理 URL。
- 独立代理凭据只在漫画请求发生前组合为内存中的运行时 URL；数据库、设置 API 和前端始终保留
  三个独立字段。
- OCR 与翻译客户端不读取漫画代理账号密码，漫画来源未设置代理 URL 时继续使用 httpx 默认的
  环境变量或直连行为。
- 代理密码及其他 secret 继续使用现有加密、掩码和 `keep / replace / clear` 协议；URL 与账号
  改用普通字符串协议。
- 每阶段先补对应回归测试，再做最小实现，并在阶段结束后运行聚焦检查。

## 阶段 1：设置定义、API 模型与数据库升级

修改：

- 在 `app/application/settings.py` 将设置 schema 从 v6 升至 v7。
- 将 `ocr_api_url` 与 `proxy_url` 的定义改为普通字段，新增普通字段 `proxy_username` 和 secret
  字段 `proxy_password`，两者默认均为空字符串。
- 在 `app/domain/settings.py` 将两个 URL 和代理账号改为字符串响应及 PATCH 字段，并仅为代理
  密码保留 `SensitiveSettingState` / `SensitiveSettingPatch`。
- 扩展 `tests/test_settings_auth.py`，覆盖全新数据库记录属性、GET/PATCH 行为、密码三种操作，
  以及模拟 schema 6 升级后 URL 精确保留、其他字段保留和新字段初始化。

验证：

- `pytest tests/test_settings_auth.py tests/test_app.py`
- `ruff check app/application/settings.py app/domain/settings.py tests/test_settings_auth.py tests/test_app.py`

## 阶段 2：漫画代理运行时凭据覆盖

修改：

- 在应用组装漫画来源时提供一个按请求读取最新 `proxy_url`、`proxy_username` 和
  `proxy_password` 的 provider。
- 代理 URL 为空时返回空值；独立账号密码均为空时原样使用 URL；任一非空时用 `httpx.URL`
  的内存副本覆盖两项 userinfo，不写回设置。
- 将运行时代理地址获取和解析纳入漫画来源的现有安全错误边界，确保无效 URL 或请求失败时不
  暴露 URL 与凭据。
- 扩展 `tests/test_manga18fx_source.py` 和设置集成测试，覆盖 URL 原样路径、账号或密码单独存在、
  两者共同覆盖、热更新、环境代理边界及错误脱敏。

验证：

- `pytest tests/test_manga18fx_source.py tests/test_settings_auth.py`
- `ruff check app/main.py app/sources/manga18fx.py tests/test_manga18fx_source.py tests/test_settings_auth.py`

## 阶段 3：前端类型、草稿与设置页布局

修改：

- 在 `web/src/domain/api.ts` 将 OCR API URL、漫画代理 URL 和代理账号建模为普通字符串，新增
  掩码代理密码字段，并更新 PATCH 类型。
- 在 `web/src/routes/_app/settings.tsx` 从 secret 草稿中移除两个 URL，新增代理账号和代理密码
  草稿，确保普通字段直接提交、密码仍提交敏感操作对象。
- 把 OCR API URL 改为普通输入框，并在两列网格中排列到“OCR 鉴权”左侧。
- 把代理区改为 URL、账号、密码三个输入框；URL 与账号直接回显，密码继续使用
  `SecretField`，并说明独立凭据会覆盖 URL 自带凭据。

验证：

- 在 `web` 中运行 `npm run fmt:check`
- 在 `web` 中运行 `npm run lint`
- 在 `web` 中运行 `npm run build`

## 阶段 4：文档与数据安全说明

修改：

- 更新 README 中设置、安全和数据归属说明，明确两个 URL 会以明文进入数据库、API 响应和
  浏览器，独立代理密码仍加密。
- 更新代理设置说明，明确该代理仅用于漫画接口、独立字段覆盖 URL userinfo、未配置时继续由
  标准代理环境变量或直连决定。
- 检查示例与测试只使用虚构地址和凭据，不添加用户的真实 OCR 地址或认证信息。

验证：

- `git diff --check`
- 对跟踪改动执行真实服务地址与凭据扫描。

## 阶段 5：全量验收与临时 UI 服务

执行：

- `pytest`
- `ruff check app tests`
- 在 `web` 中运行 `npm run fmt:check`、`npm run lint` 和 `npm run build`
- 使用隔离临时数据库启动应用，验证 `/api/settings` 返回完整 URL/账号但只掩码密码，并检查
  SQLite 中 `is_secret` 与存储内容。
- 在 `0.0.0.0` 启动使用新构建资源的临时测试服务，报告访问地址与进程状态，供用户检查 UI。
- 检查提交范围、工作区状态和敏感信息扫描结果，确认没有写入真实 OCR 地址、Basic Auth 或
  代理凭据。

## 回滚与数据安全

- schema v7 重建继续运行在单个 SQLite 事务中，失败时不得留下部分迁移结果。
- 不修改或删除翻译任务、OCR 缓存、漫画媒体缓存及用户部署数据库；测试只使用临时数据库。
- 代理运行时组合结果不得进入日志、异常详情、API 响应或持久化数据。
- 不使用破坏性 Git 命令；发现与用户现有改动重叠时停止并报告。
