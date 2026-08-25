# 批量重试失败项与阅读器章节标题实施计划

依据：`docs/superpowers/specs/2026-08-25-bulk-failed-retry-and-reader-title-design.md`

## 阶段 1：批量检查点恢复与 Manager 调度

- 先在 `tests/test_translation_manager.py` 增加失败测试，覆盖：
  - OCR、翻译、渲染和缓存阶段的失败项批量恢复及检查点保留；
  - `completed_with_errors`、`paused` 和可恢复的 `failed` generation 复用原 generation；
  - 重复调用第二次返回零；
  - 停止状态拒绝操作；
  - 旧版页面级失败批量恢复；
  - 当前较后分片不被取消，较早重试项随后按 `global_index` 优先后处理；
  - OCR 预取不取消已有请求。
- 将页面和分片的失败阶段清理策略提取为单片与批量入口共享的定义。
- 在 `TranslationRepository` 增加一个事务化批量恢复方法，返回重试数量和失效缓存路径；统一更新受影响页面及 generation 汇总。
- 在 `TranslationManager` 增加 `retry_failed`：使用章节操作锁、处理 generation 状态、尽力清理缓存、唤醒 streaming event 并确保 worker 存在。
- 运行：
  - `.venv/bin/pytest -q tests/test_translation_manager.py`
  - `.venv/bin/ruff check app/translation/manager.py app/repositories/translation.py tests/test_translation_manager.py`
- 提交后端核心改动。

## 阶段 2：API、响应模型与竞态错误

- 在 `app/domain/translation.py` 增加批量重试响应模型，包含 `task` 和非负 `retried_count`。
- 在 `app/api/translation.py` 增加 `POST .../translation/retry-failed`。
- 扩展 `tests/test_catalog_api.py`，覆盖 200 响应的 `retriedCount`、无任务 404、停止中 409 和重复调用幂等响应。
- 确认 API 不返回缓存路径、OCR job ID、远端 URL 或凭据。
- 运行：
  - `.venv/bin/pytest -q tests/test_catalog_api.py tests/test_translation_manager.py`
  - `.venv/bin/ruff check app/api/translation.py app/domain/translation.py tests/test_catalog_api.py`
- 提交 API 改动。

## 阶段 3：设置页终态失败任务数据

- 先增加 repository/API 回归测试，覆盖：
  - 活动任务继续返回；
  - 最新 `completed_with_errors` 和整体 `failed` 任务在没有活动 generation 时保留；
  - 活动任务覆盖同话旧终态卡；
  - 最新成功 generation 不会重新暴露旧失败卡；
  - 失败清零并成功完成后任务卡消失。
- 扩展 `BackgroundTranslationStage`，为终态失败卡提供明确的等待重试阶段。
- 调整 `background_tasks` 查询和每话去重规则，不增加数据库迁移。
- 运行定向 Manager 和目录 API 测试及 Ruff。
- 提交后台任务数据改动。

## 阶段 4：阅读器与设置页界面

- 扩展前端领域类型和 API 客户端：
  - `RetryFailedTranslationResult`；
  - `retryFailedTranslation`；
  - 后台等待重试阶段。
- 新增轻量章节标题格式化辅助函数：
  - 当前章节来自 `ComicDetail.chapters`；
  - 开头 `Chapter` 不区分大小写替换为 `Ch.`；
  - 缺失章节记录时将 `chapter-XX` 转为 `Ch. XX`，其他 ID 原样回退。
- 阅读器：
  - 增加批量重试 mutation；
  - 成功后立即更新任务缓存、打开译图模式并提示加入数量；
  - 顶栏按“实时翻译、重试失败、整话重译”排列；
  - 桌面显示文字和数量，窄屏显示图标和数量；
  - 无失败、停止中或请求中正确隐藏或禁用；
  - 保留单片重试。
- 设置页：
  - 为每话任务卡增加“全部重试”和“重试失败”mutation；
  - 使用已确认的 B 布局，将“重试失败”放在失败计数旁；
  - 活动卡保留“强制停止”，终态失败卡不显示；
  - 全部重试保留确认提示，失败重试直接执行；
  - 区分正在处理与等待重试计数，只有活动任务显示全局加载图标；
  - 成功后更新/失效后台任务和章节任务缓存。
- 运行：
  - `cd web && npm run fmt`
  - `cd web && npm run fmt:check`
  - `cd web && npm run lint`
  - `cd web && npm run build`
- 提交前端改动。

## 阶段 5：完整验证与 UI 验收

- 运行完整后端验证：
  - `.venv/bin/pytest -q`
  - `.venv/bin/ruff check app tests`
- 运行完整前端验证：
  - `cd web && npm run fmt:check`
  - `cd web && npm run lint`
  - `cd web && npm run build`
- 停止旧的临时 ComicLens UI 服务，继续使用临时数据目录，以最新 `web/dist` 启动 `0.0.0.0:8233`。
- 验证：
  - `/health` 和 `/settings`；
  - 构建产物包含“重试失败”“全部重试”和章节标题逻辑；
  - 桌面及窄屏按钮不重叠；
  - 设置页运行中/等待重试任务卡布局正确。
- 不调用或保存用户的线上 OCR 地址和凭据。
- 检查 `git diff --check`、提交历史、敏感信息和工作区状态，提交必要收尾改动。
