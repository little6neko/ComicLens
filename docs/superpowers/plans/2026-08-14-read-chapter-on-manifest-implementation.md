# ComicLens 章节打开即已读实施计划

## 目标

按已确认的[修复设计](../specs/2026-08-14-read-chapter-on-manifest-design.md)，将 Reader 的
已读写入时机从“到达最后一页”提前为“manifest 成功且至少包含一页”，使详情页和 Reader 目录
及时获得已读状态。

## 阶段 1：调整已读 mutation

涉及文件：

- `web/src/routes/reader.$comicId.$chapterId.tsx`

实施内容：

- 为 `markRead` mutation 定义显式目标参数，携带 `comicId`、`chapterId` 和防重章节键，避免异步
  回调读取已经变化的路由闭包。
- mutation 开始前取消目标 comic 的在途 `readChapters` 查询。
- 以不可变方式把目标章节合并进查询缓存，已经存在时不重复追加。
- 成功后将服务端完整集合与当前乐观集合做并集合并，避免旧章节的较晚响应移除新章节状态。
- 失败后只撤销本次原先不存在的乐观章节，重新获取服务端集合，并允许该章节下次进入时重试。
- 已读失败继续静默处理，不增加 toast 或阻断 Reader。

## 阶段 2：提前触发已读写入

涉及文件：

- `web/src/routes/reader.$comicId.$chapterId.tsx`

实施内容：

- 删除仅用于“到达最后一页才已读”的 `completionPageIndex` 计算。
- 已读 effect 改为同时要求 `manifest.isSuccess`、`totalPages > 0` 和当前章节尚未触发。
- effect 一满足条件就调用带明确目标参数的 mutation，不依赖 `clampedCurrent`、阅读模式、图片状态
  或翻译状态。
- 保留连续进入防重；切换章节后再返回同一章节仍允许更新现有 `read_at`。

## 阶段 3：静态与行为复核

检查：

- Reader 不再以最后页位置作为已读条件；
- manifest 失败或零页不会触发；
- mutation 回调只使用传入目标，不使用可能变化的路由参数；
- 乐观合并不修改原数组、不产生重复章节；
- 快速切章时成功回包只增加状态；
- 详情页和 Reader 目录现有灰色样式、当前章节优先级与 aria 文案保持不变；
- Git diff 仅包含设计、计划和 Reader 修复，不修改远程部署或数据。

## 阶段 4：质量门禁

执行：

- `git diff --check`
- `.venv/bin/ruff check .`
- `.venv/bin/pytest -q`
- `cd web && npm run fmt:check`
- `cd web && npm run lint`
- `cd web && npx tsc --noEmit`
- `cd web && npm run build`

复核现有后端 API 持久化测试继续通过。当前本地测试服务已经按用户要求停止，本次验证不自动重启，
也不连接或修改远程部署。

## 阶段 5：提交

- 使用 `little6neko <little6carbon@163.com>` 创建实现提交
  `fix: mark chapters read when opened`。
- 确认工作区干净，提交只包含本计划范围内的代码。
- 本次不自动发布新版本、不创建标签、不推送，除非用户后续明确要求。
