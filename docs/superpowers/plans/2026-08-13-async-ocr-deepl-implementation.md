# ComicLens 异步 OCR 与 DeepL 实施计划

日期：2026-08-13  
依据：`docs/superpowers/specs/2026-08-13-async-ocr-deepl-design.md`

## 实施原则

- 先建立可重复的一次性设置迁移，再让新代码读取新字段，确保当前 `data/comiclens.db` 可以直接升级。
- PaddleOCR、DeepL 和 DeepLX 各自保持独立适配器；管线只依赖统一翻译协议。
- 不引入 DeepL SDK，继续使用现有 HTTPX、重试与并发设施。
- 敏感值不进入日志、测试输出、语义指纹或 Git；测试只使用虚构凭据和 MockTransport。
- 每个可独立验证的阶段提交一次，提交者保持 `little6neko <little6carbon@163.com>`。

## 阶段 1：设置模式与迁移

目标提交：`feat: migrate translation service settings`

修改：

- 新增内部设置元数据 migration，用于记录设置模式版本。
- 将设置定义更新为异步 OCR、`AUTO/EN/KO`、翻译服务、DeepL Key和共用翻译超时。
- 在 `SettingsService` 中区分全新数据库和旧数据库，按设计执行一次性事务迁移。
- 更新 Pydantic 设置 DTO、环境变量种子和公开掩码响应。
- 扩展设置测试，覆盖全新默认、旧 DeepLX 保留、DeepL 默认、模型/URL/语言迁移、自定义值保留及幂等性。

验证：

- `pytest tests/test_settings_auth.py`
- `ruff check app tests/test_settings_auth.py`

## 阶段 2：异步 OCR 与翻译适配器

目标提交：`feat: add async OCR and DeepL adapters`

修改：

- 删除 OCR 同步和模式判断，只保留 multipart 提交、轮询和 JSONL 下载。
- 固定 OCR Bearer Token，并补充未知状态、协议错误和对象存储鉴权隔离。
- 实现 DeepL Free/Pro 自动主机、认证、语言映射、最多 50 条及低于 128 KiB 的稳定分批。
- 保留 DeepLX 单条协议，增加 `AUTO` 映射并使用共用翻译超时。
- 为 DeepL 响应、批次排序和 HTTP 状态定义可分类异常。
- 重写适配器测试，覆盖所有协议和边界。

验证：

- `pytest tests/test_translation_algorithms.py`
- `ruff check app/translation tests/test_translation_algorithms.py`

## 阶段 3：管线与任务管理器接入

目标提交：`feat: select translation backends`

修改：

- 扩展翻译协议以支持按页文本块批量翻译；DeepLX 内部仍可逐条并发。
- 管理器按设置选择唯一翻译适配器，不做自动回退。
- 校验当前服务所需凭据，更新错误提示与 HTTP/配额分类。
- 将翻译服务、源语言和模型写入语义指纹，移除 OCR 模式。
- 更新 manager harness 与任务测试，验证逐图执行、缓存代次和配置切换。

验证：

- `pytest tests/test_translation_manager.py tests/test_translation_algorithms.py`
- `ruff check app tests`

## 阶段 4：设置界面与文档

目标提交：`feat(web): configure DeepL translation`

修改：

- 更新前端 `ServerSettings` 和 `SettingsPatch` 类型。
- 删除 OCR 模式、认证模式和 Basic Auth 控件。
- 加入源语言选项、翻译服务选择、DeepL Key条件字段、DeepLX URL条件字段及共用超时。
- 默认/提示使用 PaddleOCR 异步 URL和模型 1.6；隐藏服务凭据保持 `keep`。
- 更新 `.env.example`、README、Compose 环境传递和第三方说明中相关文字。

验证：

- `npm run fmt:check`
- `npm run lint`
- `npm run build`
- 配置与部署文件的后端测试。

## 阶段 5：全量验收与部署

目标提交：按发现的问题使用聚焦的 `fix:` 或 `test:` 提交；若无问题则不新增提交。

执行：

- `pytest`
- `ruff check app tests`
- `npm run fmt:check && npm run lint && npm run build`
- 在当前数据目录的备份副本上先演练迁移，再重启正式本地服务。
- 检查设置响应：源语言、模型、OCR URL、翻译服务和敏感掩码符合迁移规则。
- 检查首页及 Manga18fx feed 保持 200，服务继续监听 `0.0.0.0:8233`。
- 确认工作区干净、提交身份正确后推送 `origin/main`。

## 回滚与数据安全

- 正式迁移前保留 SQLite 一致性备份以及匹配的 `secrets.key`；两者必须成对保存。
- migration 和设置升级均在事务中执行，失败时不能留下半迁移状态。
- 不删除翻译 generation、媒体缓存或用户资料；废弃设置只从 `app_settings` 中移除。
- 若运行验收失败，保持服务停止或恢复代码与成对数据备份，不用破坏性 Git 命令覆盖用户数据。
