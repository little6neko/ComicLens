# ComicLens 漫画详情元数据内部链接实施计划

## 目标

按已确认规格，让详情页作者、绘者和 Genre 使用 Manga18fx 提供的精确 slug 跳转到 ComicLens 内部作品列表；保留异常数据的纯文字兜底，不修改顶部 `Type`、“绘者”文案或任何章节标题。

## 阶段 1：详情元数据模型与解析

- 新增包含 `label` 与可空 `slug` 的详情元数据项目模型，并同步前端类型。
- 将 `ComicDetail.authors`、`artists`、`genres` 改为元数据项目数组。
- Manga18fx 解析时分别限制 `/manga-author/`、`/manga-artist/`、`/manga-genre/` 路径和合法 slug。
- 保留无链接、外站、路径类型不匹配和非法 slug 项目的显示文字，将其目标置空。
- 更新详情 fixture、来源测试和详情 API 合约测试。

## 阶段 2：作者与绘者归档后端

- 为来源协议增加受限的 `author | artist` 归档读取方法。
- 第 1 页与后续页按上游真实路径构造请求，复用现有 Comic 列表和分页解析。
- 从归档标题解析显示名称，返回带归档身份和 `ComicListPage` 的模型。
- 新增只读归档 API，校验 kind、slug 和页码，并通过现有媒体登记本地化封面。
- 覆盖正确路径、分页、非法参数、未知归档和 API 媒体本地化测试。

## 阶段 3：前端详情链接与归档页

- 详情 Genre 胶囊使用有效 slug 进入现有分类页；无 slug 时保持普通胶囊。
- 作者和绘者逐项渲染，使用有效 slug 进入内部归档页，无 slug 时保持普通文字，继续以顿号分隔。
- 新增归档 API 客户端方法、独立查询键和 `/explore/creator/$kind/$creatorId` 页面。
- 归档页复用现有页面框架、Comic 网格、分页和查询状态组件，标题与身份来自 API。
- 保留详情顶部 `Type`、“绘者”文案和所有章节标题原样。

## 阶段 4：验证与交付

- 运行来源与目录 API 定向测试、完整 pytest 和 Ruff。
- 运行前端格式化、格式检查、Lint、TypeScript 与生产构建，并确认路由树包含新页面。
- 静态审查所有内部链接均使用解析出的 slug，没有名称转 slug 的猜测逻辑。
- 分阶段提交实现，重启 `comiclens.service`，确认监听 `0.0.0.0:8233`。
- 用真实 `Wireless Onahole Raw` 详情与作者、绘者、Genre 目标验证响应和页面入口。
