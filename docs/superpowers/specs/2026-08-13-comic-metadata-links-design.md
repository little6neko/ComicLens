# 漫画详情元数据内部链接设计

## 背景

ComicLens 漫画详情页目前将作者、绘者和 `Genre(s)` 仅作为文字展示。Manga18fx 的详情 HTML 已经为这些项目提供精确链接：

- 作者：`/manga-author/{slug}`
- 绘者：`/manga-artist/{slug}`
- Genre：`/manga-genre/{slug}`

本功能将这些链接接入 ComicLens 内部页面。用户此前提出的 `Chapter XXX` 中文化已经取消，本轮不修改任何章节标题。

## 用户体验

### 详情页

- “作者”文案不变，每个有有效上游作者链接的名字可点击。
- “绘者”文案不变，每个有有效上游绘者链接的名字可点击。
- `Drama`、`Harem`、`Romance`、`School Life` 等 Genre 胶囊可点击。
- 作者和绘者链接保持当前详情文字布局，以顿号分隔，并增加清晰的 hover、focus 与键盘操作状态。
- Genre 保持当前圆角胶囊外观，增加可点击反馈和键盘焦点样式。
- 详情顶部的 Manga18fx `Type` 字段保持普通文字，不参与本功能。

### 内部目标页

- 作者跳转到 `/explore/creator/author/{slug}`。
- 绘者跳转到 `/explore/creator/artist/{slug}`。
- 作者与绘者页面使用 ComicLens 现有页面框架、Comic 卡片网格、加载/空白/错误状态和分页控件。
- 页面标题使用上游归档页解析出的作者或绘者名称，不根据 slug 猜测显示名。
- Genre 跳转到现有 `/explore/category/{slug}`，默认第 1 页和“最新更新”排序。

## 数据模型

详情 API 中作者、绘者和 Genre 项目需要同时携带显示文字与可选目标 slug。为避免并行数组错位，使用统一结构：

```text
ComicMetadataItem
  label: string
  slug: string | null
```

`ComicDetail.authors`、`artists` 和 `genres` 改为该结构的数组。链接缺失或不可信时保留 `label`，并将 `slug` 设为 `null`；前端因此仍能显示原文字，只是不渲染链接。

作者与绘者归档接口返回：

```text
ComicCreatorArchive
  kind: author | artist
  creatorId: string
  label: string
  result: ComicListPage
```

归档结果继续经过现有媒体登记，将封面 URL 本地化为 ComicLens 媒体地址。

## 来源解析与安全边界

- 不从显示名称生成 slug；`School Life` 等名称直接采用上游链接中的 `school-life`。
- 作者只接受当前 Manga18fx 主机下 `/manga-author/{slug}` 的单段路径。
- 绘者只接受当前 Manga18fx 主机下 `/manga-artist/{slug}` 的单段路径。
- Genre 只接受当前 Manga18fx 主机下 `/manga-genre/{slug}` 的单段路径。
- slug 沿用项目现有的小写字母、数字和连字符校验。
- 外站 URL、错误路径、查询参数伪装、额外路径段和无效 slug 不成为内部链接，但其可见文字仍保留。
- 重复的同类条目按首次出现顺序去重。

## 作者与绘者归档数据流

后端来源协议增加作者/绘者归档读取能力。Manga18fx 来源根据受限 `kind` 构造：

- 第 1 页：`/manga-author/{slug}` 或 `/manga-artist/{slug}`
- 后续页：`/manga-author/{slug}/{page}` 或 `/manga-artist/{slug}/{page}`

列表继续复用现有 Manga18fx 列表解析器。归档页标题去掉末尾 `Archives` 后作为显示名称；若标题结构缺失，使用详情链接传入的名称只作为前端导航期间的临时提示，最终仍以接口响应为准。

后端新增只读接口：

```text
GET /api/comics/creators/{kind}/{creator_id}?page=1
```

`kind` 仅允许 `author` 或 `artist`，`creator_id` 和页码执行与其他来源路由一致的边界校验。未知归档返回稳定的 404；网络、限流和结构解析失败沿用现有上游错误语义。

前端增加独立查询键、API 客户端方法和作者/绘者归档路由。页面参数变化会形成独立缓存，分页不会复用其他作者或绘者的结果。

## 兼容性

- 收藏与阅读历史只保存 `ComicSummary`，不持久化上述详情元数据，因此无需数据库迁移。
- 后端详情模型与前端 TypeScript 类型同步更新；现有详情调用方只需改为读取项目的 `label`。
- 有文字但没有链接的旧 fixture 或异常上游数据仍正常显示。
- 章节、状态中文化、发行日期、收藏、阅读历史、翻译和阅读器均不受影响。

## 验证

### 后端与来源

- 详情解析得到作者、绘者和 Genre 的准确 `label`/`slug`。
- `School Life` 保留显示文字并解析为 `school-life`。
- 缺少 href、外站 href、路径类别不匹配及非法 slug 时只返回文字，不返回目标。
- 作者/绘者第 1 页和后续页请求路径正确，归档标题与列表、分页正确解析。
- API 限制 kind、slug 和页码，并对归档结果执行封面本地化。
- 完整 Ruff 与 pytest 通过。

### 前端

- 作者、绘者和有效 Genre 可通过鼠标与键盘进入正确的 ComicLens 内部页面。
- 无效目标保持普通文字或普通胶囊，不产生失效链接。
- 作者/绘者归档页的标题、卡片、分页、加载、空白和错误状态正确。
- Genre 复用现有分类页并从第 1 页按最新更新打开。
- `Type` 和“绘者”文案不变，章节标题没有变化。
- 格式检查、Lint、TypeScript 与生产构建通过。
