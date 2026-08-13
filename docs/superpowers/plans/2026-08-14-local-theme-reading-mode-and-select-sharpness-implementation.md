# ComicLens 本地主题、阅读模式与下拉清晰度实施计划

## 目标

按照已确认设计，将主题和阅读模式从服务器设置中彻底移除，改为浏览器本地、即时生效且支持同源多标签同步的偏好；保留主题深浅过渡以及翻页方向等其余服务器设置行为。同时移除设置下拉的常驻 GPU 合成提示，使展开完成后的文字恢复清晰。

## 阶段 1：删除服务端主题与阅读模式

涉及文件：

- 修改 `app/domain/settings.py`
- 修改 `app/application/settings.py`
- 新增 `app/repositories/migrations/008_remove_browser_preferences.sql`
- 修改 `tests/test_settings_auth.py`

实施内容：

- 从 `ServerSettings` 和 `ServerSettingsPatch` 删除 `theme`、`reading_mode`。
- 对 `ServerSettingsPatch` 启用未知字段拒绝，旧客户端提交 `theme` 或 `readingMode` 时返回 422，而不是静默忽略。
- 从 `SETTING_DEFINITIONS` 删除 `theme`、`reading_mode`，使新数据库不再初始化这两项。
- 新增数据库迁移，删除升级数据库中已有的两条 `app_settings` 记录。
- 更新迁移版本断言、设置默认值测试、加密持久化测试，并新增 API 字段不存在、旧字段被拒绝和数据库记录删除测试。

验证：

- 全新及模拟升级数据库均不包含两项记录。
- GET 响应不含 `theme`、`readingMode`。
- PATCH 旧字段返回统一的 422 校验错误。
- 其余设置仍能保存、加密和跨重启持久化。

## 阶段 2：建立浏览器阅读模式偏好模块

涉及文件：

- 新增 `web/src/lib/reading-mode-preference.ts`

实施内容：

- 定义 `ReadingMode`、合法值、默认值 `strip` 和存储键 `comiclens-reading-mode`。
- 使用 `useSyncExternalStore` 封装统一 hook，集中处理读取、校验、订阅和写入。
- 写入时先更新当前标签页的内存快照，再尝试写入 `localStorage`，并发送当前文档自定义事件。
- 监听浏览器 `storage` 事件以同步同源其他标签页；无效值统一回退条漫。
- 本地存储不可用时仍保证当前标签页即时切换。

验证：

- 缺失或无效值返回 `strip`。
- 同一页面多个订阅者和两个标签页能收到一次有效更新。
- 刷新后恢复有效本地值。

## 阶段 3：设置页改为本地即时偏好

涉及文件：

- 修改 `web/src/domain/api.ts`
- 修改 `web/src/routes/_app/settings.tsx`
- 修改 `web/src/components/auth-boundary.tsx`

实施内容：

- 从前端 `ServerSettings` 类型删除 `theme`、`readingMode`。
- 删除 `AuthBoundary` 中服务器主题到 `next-themes` 的同步 effect，使主题只剩浏览器单一写入源。
- 设置页直接从 `useTheme` 读取主题并由 `setTheme` 即时修改；对未挂载或异常主题值回退 `system`。
- 设置页直接使用本地阅读模式 hook，并即时写入。
- 从服务器 draft、保存载荷、保存成功回调中移除两项。
- 条漫模式下翻页方向字段的条件显示改为读取本地模式，其他字段及底部保存行为不变。

验证：

- 选择主题或阅读模式不发送 PATCH 请求且立即更新 UI。
- 保存其余字段的请求体不含两项，保存成功不会覆盖本地偏好。
- 主题保持现有 CSS 深浅过渡，不启用 `disableTransitionOnChange`。

## 阶段 4：阅读器接入本地阅读模式

涉及文件：

- 修改 `web/src/routes/reader.$comicId.$chapterId.tsx`

实施内容：

- 删除 `modeOverride` 及服务器 `settings.data.readingMode` 读取。
- 通过共享 hook 取得阅读模式，阅读器小窗切换时直接写入本地偏好。
- `saveReadingSettings` 收窄为只保存 `pageDirection`；阅读器方向切换行为不变。
- 章节初始化不再重置模式覆盖；实时翻译默认值仍等待服务器设置。
- 保留切入条漫时滚动到当前页、双页索引归整及所有现有布局逻辑。

验证：

- 设置页与阅读器小窗互相同步模式。
- 多标签切换模式时已打开阅读器立即改变布局。
- 阅读模式切换不调用服务器，翻页方向切换仍正常调用服务器。

## 阶段 5：下拉文字清晰度

涉及文件：

- 修改 `web/src/styles/globals.css`

实施内容：

- 删除 `.settings-select-content` 的常驻 `will-change: transform, opacity`。
- 保留现有开关关键帧、150ms 时长、纯纵向方向和 reduced-motion 行为。
- 不修改阅读器设置菜单及下拉交互组件。

验证：

- 动画结束后计算样式为 `transform: none`、`opacity: 1`。
- Chrome LayerTree 不再报告该浮层因 `WillChangeTransform` 或 `WillChangeOpacity` 独立合成。
- 设置下拉的滚动、触控、外部关闭、键盘和焦点行为无回归。

## 阶段 6：自动化和真实浏览器回归

执行：

- `.venv/bin/pytest`
- `npm run fmt`
- `npm run fmt:check`
- `npm run lint`
- `npm run build`
- `git diff --check`

隔离浏览器验证：

- 从深色切换浅色和跟随系统，记录根元素 class 与本地存储变化，确认无反复切换。
- 打开两个同源标签页，分别验证主题和阅读模式跨标签同步。
- 记录 `/api/settings` 请求，确认本地偏好切换没有 PATCH；保存其他设置的请求体也没有两项。
- 检查深浅过渡没有被 `disableTransitionOnChange` 临时禁用。
- 检查下拉动画完成后的计算样式和合成原因，并回归桌面、移动端滚动与关闭操作。

交付：

- 独立提交实现，提交者保持 `little6neko <little6carbon@163.com>`。
- 检查正式后台翻译任务状态；本次仅重启 Web/API 进程，不删除缓存或任务。
- 重启 `comiclens.service`，确认监听 `0.0.0.0:8233`、健康接口、设置接口、设置页与阅读器入口。
