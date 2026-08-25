# OCR 默认并发与浏览器实时翻译偏好实施计划

## 目标

按照已确认设计，将全新数据库的 OCR 并发默认值改为 `2`，同时把“进入章节时默认实时翻译”
从服务器设置彻底移除，改为浏览器本地即时偏好。升级数据库复用现有设置 schema 重建逻辑
丢弃旧字段并保留其他设置，不新增字段专用删除代码。

## 阶段 1：服务端设置与 schema

涉及文件：

- `app/application/settings.py`
- `app/domain/settings.py`
- `tests/test_settings_auth.py`

实施内容：

- 将 `ocr_concurrency` 新安装默认值从 `1` 改为 `2`。
- 将设置 schema 版本从 `5` 提升为 `6`。
- 从设置定义、响应模型和 PATCH 模型删除 `realtime_translation_default`。
- 不增加 SQL migration 或字段专用迁移分支；让通用设置重建只复制仍存在的已知字段。
- 更新新数据库默认值测试和运行时 OCR 并发测试。
- 新增 schema 5 升级测试，验证 OCR 并发 `1` 与其他普通、敏感设置保留，旧默认实时翻译
  记录被丢弃，schema 版本变为 `6`。
- 将删除字段加入现有旧浏览器设置 PATCH 拒绝测试，确认现有 `extra="forbid"` 自然返回
  `422`。

## 阶段 2：浏览器实时翻译偏好模块

涉及文件：

- 新增 `web/src/lib/realtime-translation-preference.ts`

实施内容：

- 定义默认值 `false` 和存储键 `comiclens-realtime-translation-default`。
- 提供 React 订阅 hook、当前值读取和写入入口。
- 只接受本地存储字符串 `true`、`false`；其他值回退关闭。
- 写入时更新内存快照、尝试写入 `localStorage`，并触发当前文档自定义事件。
- 监听 `storage` 事件以同步同源其他标签页。
- 本地存储不可用时保持当前标签页可用。

## 阶段 3：设置页接入即时浏览器偏好

涉及文件：

- `web/src/domain/api.ts`
- `web/src/routes/_app/settings.tsx`

实施内容：

- 从前端服务器设置类型删除 `realtimeTranslationDefault`，使 `SettingsPatch` 自动不再包含它。
- 设置页通过新偏好 hook 直接读取和写入默认实时翻译。
- 从服务器 `Draft`、`toDraft` 和表单 PATCH 载荷中移除该字段。
- 保留开关在“阅读”区块的位置，改为点击后立即生效且不触发保存 mutation。
- 更新页面总说明和开关辅助文字，明确浏览器归属及只影响之后进入的章节。
- 页面底部“保存全部设置”继续只保存服务器字段。

## 阶段 4：阅读器按章节采样本地默认值

涉及文件：

- `web/src/routes/reader.$comicId.$chapterId.tsx`

实施内容：

- 删除对 `settings.data.realtimeTranslationDefault` 的读取和 effect 依赖。
- 新章节初始化时从偏好模块读取当时最新值，据此设置 `translationEnabled` 并决定是否启动
  翻译。
- 服务器设置仍用于翻页方向，因此现有设置查询和等待边界保持不变。
- 默认值在标签页间变化时，不修改已经初始化的当前章节。
- 阅读器顶部开关、重试和整话重译继续只控制当前章节，不写入浏览器默认值。

## 阶段 5：文档与验证

涉及文件：

- `README.md`
- 相关后端和前端源文件

实施内容：

- 将 OCR 并发默认值说明更新为 `2`。
- 说明主题、阅读模式和默认实时翻译只保存在浏览器，其余设置保存在服务器。
- 全局搜索确认业务代码不再包含服务器字段 `realtime_translation_default` /
  `realtimeTranslationDefault`。
- 运行 Ruff、完整 Pytest、前端格式检查、Lint、TypeScript 与生产构建。
- 使用全新临时数据库启动测试服务，检查设置 API 默认 OCR 并发为 `2`，设置页本地开关无需
  保存按钮，并确认服务监听状态正常。
- 运行 `git diff --check` 并检查工作区只包含本次改动。

## 提交顺序

1. 服务端设置、schema 与后端测试；
2. 浏览器偏好、设置页和阅读器接入；
3. README、完整验证及必要修正。
