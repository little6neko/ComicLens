# 漫画批量预先翻译实施计划

依据：`docs/superpowers/specs/2026-08-26-comic-pretranslation-batches-design.md`

## 实施原则

- `PretranslationCoordinator` 只负责编排章节，不读取或修改页面、分片、OCR Job、翻译结果或缓存检查点。
- `TranslationManager` 仍是单话任务的唯一执行者；批次只调用其公开的 `start`、`pause`、`retry_failed` 和状态查询能力。
- 批次、条目、暂停原因、交互暂让和 generation 所有权全部持久化；进程内 `Event` 只用于唤醒，不作为真实状态来源。
- 后端阶段先增加失败测试，再实现最小改动；测试不得调用真实漫画站、OCR 或翻译服务。
- 每个阶段完成定向测试和 Ruff 后单独提交，最后再做完整回归。

## 阶段 1：数据库迁移、领域模型与批次 Repository

### 1.1 先固定迁移行为

- 在 `tests/test_settings_auth.py` 扩展数据库迁移测试：
  - schema 版本包含 `9`；
  - 存在 `translation_batches`、`translation_batch_items`；
  - `translation_generations` 存在可空 `batch_item_id`；
  - 外键、唯一索引和部分唯一索引均已创建；
  - 从包含 1–8 号迁移的旧数据库升级时，原有设置、generation、页面和分片记录保持不变，新增字段为 `NULL`。
- 新增 `app/repositories/migrations/009_pretranslation_batches.sql`：
  - 创建 `translation_batches`，保存 `batch_id`、漫画标识与标题快照、状态、`pause_reason`、`interactive_yielded`、安全错误字段和时间戳；
  - 创建 `translation_batch_items`，保存 `batch_item_id`、批次、章节快照、从旧到新的 `position`、状态、尝试次数、安全错误字段和时间戳；
  - 对 `(batch_id, chapter_id)` 和 `(batch_id, position)` 建唯一约束；
  - 使用部分唯一索引约束同一漫画最多一个状态不为 `completed`/`cancelled` 的批次；
  - 给等待调度的批次和条目建立按状态、创建时间、位置查询的索引；
  - 给 `translation_generations` 增加可空外键 `batch_item_id`，并对非空值建立唯一索引。
- 状态值在 SQL `CHECK` 中与设计文档保持一致；`interactive_yielded` 只接受 `0/1`。

### 1.2 定义稳定的批次领域对象

- 新增 `app/domain/pretranslation.py`，使用现有 `ComicModel` 的 camelCase 序列化规则，定义：
  - `TranslationBatchStatus`、`TranslationBatchItemStatus` 和暂停原因类型；
  - 创建请求 `CreateTranslationBatchRequest`，限制 1–5000 个章节 ID；
  - 章节翻译概览、批次条目摘要、当前单话任务摘要和批次汇总；
  - 创建结果，能表达“已创建批次”和“无需处理且未创建批次”两种成功结果；
  - 批次操作响应。
- 对外只返回安全错误码和摘要，不返回缓存路径、远端 Job ID、图片内容、Token 或认证信息。

### 1.3 增加事务化 Repository

- 新增 `app/repositories/pretranslation.py` 和 `tests/test_pretranslation_repository.py`。
- 先覆盖以下失败测试：
  - 一个事务原子创建批次及有序条目，输入顺序最终按明确的旧到新 `position` 保存；
  - 同一漫画第二个未关闭批次违反约束，不同漫画可同时排队；
  - 按批次创建时间 FIFO 选择全局候选，按 `position` 选择下一条目；
  - `pending → running → completed/skipped/failed` 的合法转换与汇总计数；
  - 完成本章后暂停、无当前章时立即暂停、继续、取消未开始条目和当前章结束后取消；
  - `completed_with_errors` 只把失败条目重新排为 `pending`，不改成功、跳过或取消条目；
  - `completed_with_errors`/`failed` 可结束为 `cancelled`，活动批次不可直接结束；
  - `pause_reason` 与 `interactive_yielded` 可同时存在，清除交互暂让不会覆盖用户或配置暂停；
  - 重复暂停、继续、取消、重试失败和结束操作保持幂等；
  - 中途 SQL 异常时批次和条目完整回滚。
- Repository 提供小而明确的事务方法，不让协调器拼接 SQL：
  - 创建、读取当前漫画批次、列出后台批次；
  - 领取全局下一个批次/条目和记录条目结果；
  - 设置用户/配置暂停、交互暂让、取消剩余、失败重排和结束失败批次；
  - 根据条目实时统计总数、完成、跳过、失败、取消和当前章；
  - 按 `batch_item_id` 查找其拥有的 generation。

### 1.4 验证并提交

- 运行：
  - `.venv/bin/pytest -q tests/test_settings_auth.py tests/test_pretranslation_repository.py`
  - `.venv/bin/ruff check app/domain/pretranslation.py app/repositories/pretranslation.py tests/test_pretranslation_repository.py tests/test_settings_auth.py`
- 提交数据库、模型和 Repository 基础改动。

## 阶段 2：Manager 所有权、恢复隔离与活动通知

### 2.1 在 worker 启动前持久化所有权

- 先扩展 `tests/test_translation_manager.py`，覆盖：
  - 普通 `start()`/`retry_failed()` 不传所有者时行为不变；
  - 批次调用传入 `batch_item_id` 后，复用 paused、active、failed 或 matching generation 时均先绑定所有权，再创建/唤醒 worker；
  - 新 generation 在同一创建事务中写入 `batch_item_id`；
  - 设置指纹变化导致新 generation 时，所有权从旧 generation 转移到新 generation，且任意时刻只有一个 generation 拥有该条目；
  - 所有者与漫画/章节不匹配时拒绝启动，避免错误关联；
  - 不带所有者的普通调用不会意外清除现有批次所有权。
- 调整 `TranslationRepository.create_generation()`，接受可空所有者并在 INSERT 时写入；新增事务化的 generation 所有权绑定/转移方法。
- 调整 `TranslationManager.start()`、内部 `_start()` 和 `retry_failed()`，接受仅供编排器使用的可空 `batch_item_id`。
- 所有复用、恢复、失败重试和新建分支都必须在 `_ensure_worker()` 前完成所有权提交。

### 2.2 隔离普通自动恢复和后台任务列表

- 增加恢复回归测试：
  - 服务重启时，属于未关闭批次的 generation 会恢复为可协调状态，但 `_resume_recovered_workers()` 不为其直接创建 worker；
  - 普通 generation 继续按原行为自动恢复；
  - 已关闭批次遗留的非终态 generation 不会永久失去普通恢复能力；
  - `background_tasks()` 不单独返回未关闭批次拥有的 generation，批次关闭后其失败 generation 可重新出现为普通单话卡。
- 修改 `recover_interrupted()`、恢复查询和后台查询时，只通过 `batch_item_id → batch status` 判断所有权，不从旧任务字段或漫画/章节猜测来源。
- 保留现有页面、分片和缓存恢复逻辑，不改变 OCR 协议与失败重试语义。

### 2.3 提供协调器所需的公开观察能力

- 在 `TranslationRepository`/`TranslationManager` 增加批量读取接口：
  - 一次读取一组章节的最新 generation 状态，用于概览与增量判断；
  - 查询所有未关闭批次之外的活动 generation，用于识别交互任务；
  - 通过 generation ID 读取稳定的 `TranslationTaskState`。
- 给 Manager 增加轻量活动监听注册：单话启动、状态变化、暂停和 worker 结束时只调用无阻塞唤醒回调；协调器收到通知后仍重新查询数据库。
- Manager shutdown 前停止发送通知；监听器异常不得影响单话 worker。
- 提供公开的运行时服务配置校验入口，复用现有 `_runtime_settings(require_services=True)` 规则，不在协调器复制 OCR/翻译配置判断。

### 2.4 验证并提交

- 运行：
  - `.venv/bin/pytest -q tests/test_translation_manager.py tests/test_pretranslation_repository.py`
  - `.venv/bin/ruff check app/translation/manager.py app/repositories/translation.py tests/test_translation_manager.py`
- 提交 Manager 所有权与恢复隔离改动。

## 阶段 3：持久化 `PretranslationCoordinator`

### 3.1 建立可控的调度循环

- 新增 `app/translation/pretranslation.py` 与 `tests/test_pretranslation_coordinator.py`。
- 协调器提供显式 `start()`、`shutdown()` 和 `wake()`；全局只创建一个 scheduler task。
- scheduler 每轮从 Repository 重新读取事实状态：
  1. 若存在交互任务，先处理当前批量章的安全暂让，不领取其他批次；
  2. 否则按批次创建时间领取最早候选；
  3. 恢复或核对其当前 `running` 条目；
  4. 没有当前条目时按 `position` 领取下一条 `pending`；
  5. 调用 Manager 后等待活动通知或短时持久状态轮询；
  6. 提交条目结果，并在批次边界处理暂停、取消或最终汇总。
- 任何唤醒丢失都只能造成短暂延迟，不能造成重复 generation 或并发运行两章。

### 3.2 实现增量章节路由

- 用可控 fake manager 先覆盖每一种分支：
  - 最新任务完整成功：条目标记 `skipped`，不调用 OCR/翻译；
  - 同章已有活动任务：不重复启动，等待其结果；
  - paused 或整话 `failed`：调用 `start(..., batch_item_id=...)`；
  - `completed_with_errors` 且存在失败项：调用 `retry_failed(..., batch_item_id=...)`；
  - 从未开始：调用 `start(..., batch_item_id=...)`；
  - batch 启动后正常成功记 `completed`，最终仍有失败项记 `failed` 并继续下一章。
- 三章必须严格旧到新；多个漫画的批次按创建时间共享一个槽，任意时刻最多一个批次拥有的章节运行。
- 每章实际开始时调用 Manager 的当前配置校验/运行入口，不保存批次创建时的 OCR、翻译或代理设置。

### 3.3 实现暂停、取消、失败重排和关闭

- “完成本章后暂停”：
  - 有当前章时批次转 `pausing`，不调用单话 `pause()`，让本章自然结束；
  - 没有当前章时直接转 `paused`；
  - 当前章结束后不领取下一章。
- “取消剩余”：立即把未开始条目标记 `cancelled`；当前章自然结束后批次转 `cancelled`。
- 单章错误不阻塞队列；全部条目结束后根据失败数生成 `completed` 或 `completed_with_errors`。
- “重试失败章节”只重排失败条目并把批次重新放回 FIFO 队列；“结束批次”只关闭批次，不修改其 generation。
- 所有用户操作方法唤醒 scheduler，并在重复调用时返回相同稳定状态。

### 3.4 实现阅读器任务优先

- 增加竞态测试：
  - 批量 OCR 正在处理分段时出现普通单话任务，协调器调用现有 `manager.pause()`，已发请求不由协调器取消；
  - 批量章到达 paused 后设置 `interactive_yielded=1`，所有交互任务结束前不启动任何批次或下一章；
  - 交互任务结束且没有用户/配置暂停时，协调器带原 `batch_item_id` 恢复同一章；
  - 用户暂停和交互暂让同时存在时，只清除 `interactive_yielded`，不自动恢复用户暂停的批次；
  - 被暂让的批次保持全局槽，不允许另一本漫画的批次绕过；
  - 手动任务与批量章同一漫画/章节时也不创建重复 generation。

### 3.5 实现重启核对与错误边界

- 覆盖进程在以下时点重启：条目领取后、generation 绑定后、OCR 中、交互暂让、`pausing`、`cancelling`。
- 启动核对规则：
  - owned generation 已终态时补交条目结果；
  - owned generation 可恢复且批次允许运行时，通过 Manager 公共入口恢复；
  - 用户 paused、`pausing` 和 `cancelling` 保持其持久语义；
  - 同一时间仍只恢复一个批量章。
- 当前或后续章节遇到 OCR/翻译配置错误时，将条目保留为可继续状态，并以 `pause_reason=config` 暂停批次；修正设置后由用户继续。
- 漫画源、OCR、翻译、渲染或缓存导致的单话终态错误记为章节失败并继续；数据库一致性或协调器自身异常才把批次记为 `failed`，等待用户继续或结束，避免热循环。
- 使用 `log_event` 输出创建、领取、跳过、完成、失败、暂停、暂让、恢复、取消和批次终态；只记录缩短的 `batch_ref`、现有安全任务上下文、位置、状态和错误码。

### 3.6 验证并提交

- 运行：
  - `.venv/bin/pytest -q tests/test_pretranslation_coordinator.py tests/test_translation_manager.py tests/test_pretranslation_repository.py`
  - `.venv/bin/ruff check app/translation/pretranslation.py tests/test_pretranslation_coordinator.py`
- 提交协调器核心改动。

## 阶段 4：API、应用生命周期与端到端后端测试

### 4.1 接入应用生命周期

- 在 `app/api/dependencies.py` 增加批次 Repository/Coordinator 依赖。
- 在 `app/main.py` 中按以下顺序装配：
  1. 创建 `TranslationRepository` 和 `PretranslationRepository`；
  2. 创建 `TranslationManager`；
  3. 创建 Coordinator、注册 Manager 活动监听并启动 scheduler；
  4. 关闭时先停止 Coordinator，再关闭 Manager，最后关闭源站客户端和数据库。
- 该顺序确保 Manager 普通恢复查询先排除批次所有者，同时 shutdown 期间不会再领取新章节。

### 4.2 增加批次 API

- 新增 `app/api/pretranslation.py` 并由 `app/api/router.py` 注册：
  - `GET /api/comics/{comic_id}/translation-overview`；
  - `POST /api/comics/{comic_id}/translation-batches`；
  - `GET /api/translation-batches/background`；
  - `POST /api/translation-batches/{batch_id}/pause`；
  - `POST /api/translation-batches/{batch_id}/resume`；
  - `POST /api/translation-batches/{batch_id}/cancel-pending`；
  - `POST /api/translation-batches/{batch_id}/retry-failed`；
  - `POST /api/translation-batches/{batch_id}/close`。
- 概览接口只调用一次漫画详情和一次批量本地状态查询，返回原目录顺序、明确的旧到新位置、每章增量状态和当前未关闭批次。
- 创建接口重新读取最新漫画详情：
  - 空数组、请求中超过 5000 个章节 ID 或包含未知 ID 时返回 `422` 和稳定错误码；
  - 重复 ID 去重，不创建重复条目；
  - 依据上游目录位置排序，不解析章节标题数字；
  - 先计算真实工作量；全部完整成功时返回 no-work 成功，不验证 OCR/翻译配置且不创建批次；
  - 有实际工作时才校验服务配置并事务创建；
  - 已有未关闭批次返回 `409` 和稳定冲突码。
- `resume` 重新校验配置；仍无效则返回 `409` 且数据库状态不变。`close` 只接受 `completed_with_errors`/`failed`，运行中引导使用取消剩余。
- 所有操作返回最新批次摘要并保持幂等。

### 4.3 增加 API 与生命周期测试

- 在 `tests/test_catalog_api.py` 增加：
  - overview 一次返回所有章节、本地增量状态、执行位置和当前批次；
  - 章节乱序提交后按旧到新创建，重复 ID 被去重，边界特别篇不依赖标题数字；
  - 空、未知和超过上限请求的 `422`；
  - 同漫画冲突 `409` 与不同漫画可排队；
  - 全部完成返回 no-work，未配置服务也不会失败；
  - 后台列表隐藏纯成功批次，保留排队、运行、暂停、`completed_with_errors` 和 `failed`；
  - pause/resume/cancel/retry/close 的状态限制、幂等性和 camelCase 响应；
  - API 响应不泄露凭据、缓存路径或 OCR Job ID。
- 在 `tests/test_app.py` 或协调器生命周期测试中覆盖：
  - 应用退出先停止 scheduler，不留下 asyncio task；
  - 重启后只有 Coordinator 恢复批次 generation，普通任务仍由 Manager 恢复。
- FakeComicSource 扩展为多章目录；测试 pipeline 保持本地可控，不访问网络。

### 4.4 验证并提交

- 运行：
  - `.venv/bin/pytest -q tests/test_catalog_api.py tests/test_app.py tests/test_pretranslation_coordinator.py tests/test_translation_manager.py`
  - `.venv/bin/ruff check app/api/pretranslation.py app/api/dependencies.py app/api/router.py app/main.py tests/test_catalog_api.py tests/test_app.py`
- 提交 API 与应用装配改动。

## 阶段 5：漫画详情页入口、范围选择与批次详情

### 5.1 前端类型与数据访问

- 扩展 `web/src/domain/api.ts`：章节概览、批次/条目状态、汇总、创建结果和操作响应类型。
- 扩展 `web/src/lib/api-client.ts`：overview、create、background 和五个批次操作。
- 扩展 `web/src/lib/query-keys.ts`：
  - 按漫画 ID 的 `translationOverview`；
  - 全局 `backgroundTranslationBatches`；
  - 操作后统一失效 overview、批次后台列表、普通后台任务和当前单话 task。

### 5.2 实现纯选择逻辑

- 新增 `web/src/features/comic-detail/pretranslation-selection.ts`，集中实现：
  - 全选；
  - 任意复选；
  - 两个目录边界之间的包含式区间替换；
  - “从指定章开始”包含边界章及所有 `position` 更大的新章节；
  - 搜索只过滤显示，不清空隐藏章节的选择；
  - 根据 overview 计算已选数量、实际补齐数量、将跳过和将重试数量。
- 所有范围都依据服务端提供的位置，不对 `Chapter`、`Ch.`、番外或特别篇标题做数字解析。
- 选择模式切换时使用明确初始化规则，提交始终是去重后的 `chapterIds` 快照。

### 5.3 实现响应式弹窗

- 新增 `web/src/features/comic-detail/pretranslation-dialog.tsx`，复用项目已有 Radix `Dialog`：
  - 桌面为居中、限高弹窗；移动端为真正全屏布局；
  - 标题和底部操作固定，章节列表独立滚动；
  - 一级模式为“全部”“指定章节”“从指定章开始”；
  - 指定章节支持搜索、逐章复选和包含式“选择区间”；
  - 章节行显示未开始、活动、暂停、已完成将跳过、失败将重试等状态；
  - 底部显示“已选 N 话 · 实际补齐 M 话”，无工作时显示“无需处理”并禁用开始；
  - 请求中锁定重复提交，失败后保留选择；
  - 焦点锁定、Esc/关闭按钮、关闭后焦点恢复、标签关联和窄屏触控尺寸符合现有 UI 规则。

### 5.4 接入漫画详情页和批次控制

- 修改 `web/src/routes/_app/comic.$comicId.tsx`：
  - 在“继续阅读”和“收藏”旁加入主操作“预先翻译”；
  - 无未关闭批次时打开范围选择；有批次时打开批次详情；
  - 按状态显示 `预先翻译 X / Y`、已暂停或有失败项；
  - 活动时轮询 overview，稳定终态降低/停止轮询；
  - 创建 no-work 时提示无需处理，不打开空批次。
- 新增可复用的 `web/src/features/pretranslation/pretranslation-batch-card.tsx` 或同等组件，详情视图显示：
  - 漫画、批次状态、章节总进度和完成/跳过/失败/取消汇总；
  - 当前章节及嵌套的现有单话页/分片进度；
  - 按状态显示完成本章后暂停、继续、取消剩余、重试失败章节和结束批次；
  - 危险或不可逆范围操作提供清晰确认，重复提交时禁用按钮。
- mutation 成功后同时更新返回值和失效相关查询，避免等待下一次轮询才改变按钮。

### 5.5 前端验证并提交

- 运行：
  - `cd web && npm run fmt`
  - `cd web && npm run fmt:check`
  - `cd web && npm run lint`
  - `cd web && npm run build`
- 人工检查桌面和移动宽度下：模式切换、搜索、区间边界、长章节标题、空选择、no-work、请求失败保留和键盘焦点。
- 提交漫画详情与弹窗改动。

## 阶段 6：设置页批次卡与单话任务去重

- 修改 `web/src/features/settings/background-translation-tasks.tsx`：
  - 同时查询普通后台单话和批次后台列表；
  - 先显示批次卡，再显示普通阅读器/手动任务；
  - 当前批次拥有的单话状态嵌套在批次卡中，不再渲染第二张独立卡；
  - 批次关闭后，底层仍失败的 generation 可按普通单话卡重新出现；
  - 顶部摘要区分批次排队/运行/暂停/失败和普通单话处理中/待重试。
- 复用阶段 5 的批次卡和 mutations，设置页提供完整的暂停、继续、取消剩余、重试失败章节和结束批次控制。
- 保留现有单话“全部重试”“重试失败”“强制停止”行为，不改变其确认文案和缓存刷新。
- 增加错误隔离：一个查询刷新失败时保留另一类任务和上次成功数据，不把批次卡误删。
- 运行：
  - `cd web && npm run fmt`
  - `cd web && npm run fmt:check`
  - `cd web && npm run lint`
  - `cd web && npm run build`
- 人工检查无任务、只有普通任务、只有批次、批次与交互任务并存、批次失败后关闭五种设置页状态。
- 提交设置页集成改动。

## 阶段 7：竞态回归、日志检查与完整验收

### 7.1 完整自动检查

- 运行后端：
  - `.venv/bin/pytest -q`
  - `.venv/bin/ruff check app tests`
- 运行前端：
  - `cd web && npm run fmt:check`
  - `cd web && npm run lint`
  - `cd web && npm run build`

### 7.2 重点回归场景

- 用可控测试 pipeline 验证：
  - 三章旧到新、多漫画 FIFO、全局单批量章；
  - 阅读器单话出现后安全暂让，结束后仅在允许时恢复；
  - 用户暂停与交互暂让叠加不丢失意图；
  - 服务在 generation 绑定、OCR、`pausing`、`cancelling` 时重启，不重复 worker；
  - 当前设置变化后，尚未启动章节使用新设置，已有关联 generation 遵守现有单话指纹语义；
  - 批次失败重试和结束后仍能从阅读器按当前设置继续。
- 验证已有单话启动、暂停、重试失败、全部重译、强停、自动恢复、异步 OCR 轮询和后台卡片测试无回归。

### 7.3 UI 与日志验收

- 如需要浏览器验收，使用临时数据目录和假/本地 pipeline 启动 `0.0.0.0:8233`；不调用或保存用户的线上 OCR 地址与凭据。
- 检查桌面居中弹窗、移动全屏弹窗、详情入口进度、设置页嵌套卡和所有状态按钮。
- 检查 Docker 标准输出中的批次日志能关联短 `batch_ref`、generation、漫画、章节、位置和结果，同时不含图片、响应正文、Token、Basic Auth、代理密码或完整远端 Job ID。
- 检查 `git diff --check`、提交历史、敏感字符串和工作区状态；提交必要的收尾修正。

## 完成定义

只有同时满足以下条件才算实现完成：

1. 批次持久化、增量补齐、全局串行和旧到新顺序均有自动测试；范围选择通过类型检查、生产构建和桌面/移动端交互验收。
2. generation 所有权在 worker 启动前提交，重启后不会被 Manager 普通恢复逻辑抢跑。
3. 阅读器任务能安全暂让批量章，且用户暂停意图不会被自动恢复覆盖。
4. 详情页和设置页都能准确展示并控制批次，移动端使用全屏弹窗。
5. 所有操作幂等，配置错误可恢复，失败批次可重试或结束。
6. 原有单话 OCR/翻译、重试、强停、缓存和后台任务功能全部通过回归测试。
7. 后端、前端完整检查通过，日志不泄露认证或远端任务敏感信息。
