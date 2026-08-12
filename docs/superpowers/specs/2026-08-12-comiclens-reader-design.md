# ComicLens 自托管 Comic 阅读与实时翻译站设计

日期：2026-08-12
状态：已完成交互设计确认，等待规格复核

## 1. 背景与目标

ComicLens 是一个个人使用、单用户自托管的 Comic 阅读网站。它实时读取 Manga18fx 的目录、搜索、分类、热门、Comic 详情和章节内容，并将现有 ComicTranslator 的抓图、长图切片、OCR、翻译和图片覆写管线整合到阅读器。

核心目标：

1. 提供首页、搜索、分类、Manga18fx 周热门排行、Comic 详情、章节、收藏和阅读历史。
2. 使用 `shadcn/ui + Radix UI` 的 `radix-maia` 风格，延续 jm-boom 的移动媒体库布局和沉浸式阅读器。
3. 阅读器开启实时翻译后，先显示原图；普通源图片完成一张就原位显示一张译图，长图沿用 ComicTranslator 的切片 OCR 和渐进阅读分片。
4. 设置、收藏、历史、翻译任务和页级进度保存在服务器；原图、OCR 数据和译图在 5 GB 范围内长期保留。
5. 单容器部署。访问密码可选，配置环境变量后保护页面、API 和图片。

## 2. 术语与命名约束

项目领域模型统一使用 `comic`，包括代码、数据库、API 和路由中的名称：

- `ComicService`
- `ComicSummary`
- `ComicDetail`
- `comic_id` / `comicId`
- `/api/comics`
- `/comic/:comicId`

不得使用 jm-boom 中泛化的 `manga` 领域命名。只有以下两类内容允许保留该字符串：

1. 上游品牌专名 `Manga18fx`。
2. 上游无法改变的 URL，例如 `/manga/{slug}` 和 `/manga-genre/{slug}`。

“源图片”指 Manga18fx 章节页面中的一张图片。长图即使被切成多个 OCR 分片或阅读分片，仍是一张源图片。

## 3. 范围与非目标

### 3.1 第一版范围

- Manga18fx 首页、搜索、分类、分类排序、周热门排行和分页。
- Comic 详情、章节列表和相关元数据。
- 移动端优先、桌面自适应的 Maia UI。
- 收藏、阅读历史、已读章节和继续阅读。
- 条漫、单页和双页阅读模式；条漫为默认模式。
- 服务器端设置和可选访问密码。
- 按需实时翻译、停止边界、续译、整话重译和失败单图重试。
- 原图、OCR 产物、译图和封面的容量型长期缓存。
- Docker 单容器部署和单目录数据备份。

### 3.2 非目标

- 不建立或定时同步 Manga18fx 全站 Comic 索引。
- 不提供公开注册、多用户、权限角色或社交功能。
- 不翻译标题、简介和标签等目录元数据；第一版只翻译章节图片。
- 不使用 Playwright 等浏览器自动化绕过上游验证；HTML 无法直接取得时显示可重试错误。
- 不引入 Redis、Celery 或独立任务服务。
- 不在运行时依赖 `/home/liuyingqian/projects/ComicTranslator` 或 `/home/liuyingqian/projects/jm-boom`；所需代码整合到 ComicLens 后独立构建。

## 4. 总体架构

采用 React + FastAPI 单体方案：

```text
Browser
  React + TanStack Router + TanStack Query + shadcn/ui
                         │ same-origin HTTP
                         ▼
ComicLens FastAPI
  ├─ Comic Catalog      ─── Manga18fx HTML
  ├─ Reader             ─── Manga18fx/CDN images
  ├─ Translation        ─── OCR service + DeepLX
  ├─ Settings & Access
  ├─ SQLite repositories
  └─ File cache
```

### 4.1 前端

- React、TypeScript 和 Vite。
- TanStack Router 管理文件路由。
- TanStack Query 管理远端状态和浏览器短期缓存。
- shadcn/ui `radix-maia`、Radix UI、Tailwind CSS 和 CSS variables。
- Geist + Noto Sans SC 字体，Lucide 图标。
- Bun 作为前端包管理和构建工具。

### 4.2 后端

- FastAPI + asyncio，生产环境固定单 worker。
- HTTPX + BeautifulSoup/lxml 解析目录和章节。
- SQLite 使用 WAL、foreign keys 和版本化 SQL migration。
- 进程内异步任务调度；任务事实和页级检查点持续写入 SQLite。
- FastAPI 提供构建后的 React 静态资源，实现单容器、同源部署。

### 4.3 模块边界

建议目录：

```text
app/
  api/                 # HTTP DTO、路由、鉴权依赖
  application/         # 用例编排
  domain/              # Comic、Reader、Translation 领域模型
  sources/             # ComicSource 协议与 Manga18fx 适配器
  translation/         # 从 ComicTranslator 整合的翻译管线
  cache/               # 章节包、文件存储、LRU
  repositories/        # SQLite 持久化
  security/            # 会话和敏感设置加密
web/
  src/components/
  src/features/
  src/routes/
  src/lib/api/
  src/domain/
```

各模块只通过明确 DTO 或领域接口协作。`translation/` 不直接处理页面路由；`sources/` 不写收藏、历史或任务；`repositories/` 不包含上游解析逻辑。

## 5. Comic 目录与上游数据

### 5.1 数据获取边界

目录的实时获取边界沿用 jm-boom 的设计，Manga18fx URL 和 HTML 解析则由独立适配器实现：

- 首页、搜索、分类、周榜和详情由后端在请求时实时访问上游。
- 后端不持久化完整目录，也不为目录响应增加服务器 TTL 缓存。
- React Query 在浏览器内短期缓存响应。
- 收藏和历史保存当时的 `ComicSummary` 快照，因此上游暂时不可用时仍可展示本地列表。
- 封面和阅读图片独立使用服务器文件缓存。

建议的 React Query 策略：

| 数据 | staleTime | gcTime |
|---|---:|---:|
| 首页、搜索、分类、周榜列表 | 30 分钟 | 6 小时 |
| Comic 详情 | 10 分钟 | 1 小时 |
| 分类筛选项 | 12 小时 | 24 小时 |
| 章节阅读清单 | 1 小时 | 2 小时 |

页面重新加载后，未持久化的 Query 缓存可以丢失。这是设计行为，不把浏览器缓存同步到 SQLite。

### 5.2 Manga18fx 获取契约

以下契约已于 2026-08-12 对 Manga18fx 实际页面验证。领域层定义 `ComicSource` 协议，Manga18fx 适配器按功能分别构造 URL，不能假设所有列表使用同一种分页格式：

| 功能 | 上游请求 | 行为 |
|---|---|---|
| 首页重点更新与最新更新 | `GET /` | 分别解析重点更新轨道和最新更新第一页 |
| 最新更新翻页 | `GET /page/{page}` | `page` 从 1 开始；只返回最新更新列表及分页，不重复重点更新轨道 |
| 搜索 | `GET /search?q={query}&page={page}` | `q` 由 HTTP 客户端编码；`page` 从 1 开始；空查询由 ComicLens 拒绝 |
| 分类目录 | `GET /` | 从导航中的 `/manga-genre/{slug}` 链接发现并去重；同时纳入来源专用的 `/manhwa-raw` 列表入口 |
| 普通分类 | `GET /manga-genre/{slug}?orderby={order}`；第 2 页起使用 `/manga-genre/{slug}/{page}?orderby={order}` | `order` 只允许 `latest`、`rating`、`views`，默认 `latest` |
| 来源专用 Raw 列表 | `GET /manhwa-raw?orderby={order}`；第 2 页起使用 `/manhwa-raw/{page}?orderby={order}` | 与普通分类使用相同的三种排序；只在适配器内保留该上游名称 |
| 周热门排行 | `GET /hot-manga?page={page}` | Manga18fx 页面明确为一周热门；第一版不显示来源未提供的日榜、月榜或总榜切换 |
| Comic 详情 | `GET /manga/{comic_slug}` | 解析元数据和完整章节列表 |
| 章节清单 | `GET /manga/{comic_slug}/{chapter_slug}` | 从 `.page-break img` 按 DOM 顺序解析全部有效源图片 |

搜索和周榜使用 `page` 查询参数，最新更新与分类分页使用路径段；例如不能把搜索第 2 页构造成 `/search/2`，也不能把周榜第 2 页构造成 `/hot-manga/2`。适配器从受控参数构造上述白名单 URL，不直接跟随客户端提交的 URL 或未经校验的任意分页链接。

分类 API 使用稳定的 `category_id`。普通分类 ID 对应已验证的 slug；来源专用入口由适配器中的显式映射表解析，不能把 `category_id` 当作任意上游路径。导航中重复出现的分类按规范化 slug 去重并保留第一次出现的标签和顺序。

### 5.3 列表解析与领域 DTO

搜索、分类和周榜复用同一个列表项解析器。其作用域限定为内容区 `.listupd > .page-item`，不能全页扫描 `/manga/` 链接，以免把导航、广告或推荐区误当结果。每项归一化为 `ComicSummary`：

| 字段 | Manga18fx 来源与规则 |
|---|---|
| `comic_id` | 从列表项内详情链接 `/manga/{comic_slug}` 提取并规范化 |
| `title` | 优先使用 `h3.tt a` 的 `title` 或文本，缺失时回退封面 `alt` |
| `cover_url` | `.thumb-manga img` 的 `data-src` 优先、`src` 回退；API 返回受控的 ComicLens 媒体 URL |
| `rating` | `.mmrate[data-rating]`；缺失或空值为 `null`，不能伪装成 0 分 |
| `is_adult` | 是否存在 `.adult-badges` |
| `latest_chapters` | `.list-chapter .chapter-item` 中的章节 ID、标题和链接，以及 `.post-on` 的原始更新时间标签；没有日期时允许 `null` |

首页的重点更新轨道使用 `.trending-block .hot-item` 单独解析；最新更新区仍复用上述列表项解析器和分页 DTO。重点更新卡至少返回 `comic_id`、标题、封面和卡片上的最新章节标签。首页“重点更新”不等同于 `/hot-manga` 周榜，前端使用不同标题和 Query Key。

首页最新更新、搜索、分类和周榜统一返回 `ComicListPage`：

```text
items
page                 # 一基页码
available_pages      # 当前分页条中出现的页码
has_previous
has_next
```

分页状态从 `ul.pagination` 解析：`li.active` 表示当前页，`li.prev`、`li.next` 是否禁用决定前后页；上游的 `data-page` 是零基值，不能直接作为 API 页码。上游没有可靠的结果总数，因此 API 不虚构 `total`；分页条缺失时，按单页结果返回。

搜索结果区存在且显示明确的 `No result` 时返回 200 和空 `items`。若预期内容容器缺失、列表项结构全部无法识别，或请求页与解析出的活动页矛盾，则返回可重试的 `UPSTREAM_PARSE_ERROR`，不能把站点改版、验证页或错误页冒充空结果。

`comic_id` 使用规范化的 `comic_slug`；`chapter_id` 使用该 Comic 下 URL-safe 的章节 slug。数据库中用 `(comic_id, chapter_id)` 作为章节身份，不把用户输入直接拼接为文件路径。

文件缓存键由 `(comic_id, chapter_id)` 的规范化值经过无歧义编码和散列生成；即使不同 Comic 存在相同章节 slug，也不能共享缓存路径。

解析器将上游 HTML 归一化为稳定领域对象。HTML fixture 测试用于锁定选择器、URL 形式、空结果和分页状态；上游结构变化只修改适配器，不传播到 API 和 UI。

### 5.4 网络与安全

- 目录和图片先直连；发生连接超时等可重试错误时，使用设置中的回退代理重试。
- 为目录请求设置明确的连接、读取和总超时；对超时、429 和瞬时 5xx 做有上限的退避重试，尊重 `Retry-After`，不做无界重试。
- 发送稳定、可配置的常规浏览器 `User-Agent` 和来源需要的最小请求头；不转发浏览器 Cookie，也不登录来源站点。
- API 不接受任意网页 URL 或图片 URL，避免把服务变成 SSRF 代理。
- 源 URL 只能来自适配器解析结果，协议必须是 HTTP(S)，并拒绝回环、私网、链路本地和非法重定向目标。
- 目录请求只接受最终落在允许来源主机上的有限次重定向；2xx HTML 仍需通过预期内容结构校验，不能仅凭状态码认定成功。
- Manga18fx 使用外部图片 CDN 时，由适配器维护允许规则并继续执行地址安全检查。

## 6. 页面、导航与 UI

### 6.1 视觉体系

采用已确认的 A 方案：Maia 移动媒体库。

- 移动端使用底部悬浮胶囊导航。
- 首页使用横向 Comic 轨道、封面卡片和简洁分区标题。
- 桌面端扩大内容宽度和网格密度，但不切换为另一套设计系统。
- 支持明暗主题；所有设置页可见设置保存在服务器。

### 6.2 主导航

```text
首页 | 探索 | 收藏 | 历史 | 设置
```

Comic 详情和阅读器使用独立页面，不显示主导航，以保持沉浸体验。

### 6.3 路由

```text
/
/explore
/explore/search
/explore/category/:categoryId
/explore/ranking
/favorites
/history
/settings
/comic/:comicId
/reader/:comicId/:chapterId
/login                         # 仅密码门禁启用时使用
```

### 6.4 页面内容

- 首页：继续阅读、最新更新、重点更新和来源提供的其他重点分区。
- 探索：统一搜索框、分类入口、分类排序、周热门排行和分页列表。
- Comic 详情：封面、标题、作者、标签、简介、收藏按钮、继续阅读、章节列表。
- 收藏：服务器持久化，默认按最近收藏排序。
- 历史：最近阅读、页进度和继续阅读；支持删除单项及清空确认。
- 设置：阅读、翻译、接口、代理、安全状态、缓存占用和版本信息。

## 7. 设置与访问门禁

### 7.1 服务器设置

以下设置保存到 SQLite，而不是 localStorage：

- 阅读模式、页方向、双页模式和主题。
- `realtime_translation_default`：进入阅读器时实时翻译开关的默认值。
- 默认源语言，第一版默认 `EN`；目标语言固定为 `ZH`。
- OCR 模式 `auto | direct | job`。
- OCR 认证模式、API URL、Token、Basic 用户名和密码。
- OCR 模型、轮询间隔、总超时和并发。
- DeepLX URL、请求超时和翻译并发。
- 回退代理 URL。
- ComicTranslator 的长图阈值、OCR 分片高度、重叠和阅读分片高度；放在高级设置。
- 缓存容量，默认 5120 MB。

环境变量中的 OCR/翻译配置只用于首次启动时为缺失的数据库设置提供初值，之后以数据库值为准。访问密码始终只来自环境变量。

设置更新使用 PATCH 语义。敏感字段不使用“空字符串代表保留”这种歧义协议，而是明确发送：

```json
{
  "ocrToken": { "action": "keep" }
}
```

`action` 可为 `keep`、`replace` 或 `clear`；`replace` 必须附带 `value`。

### 7.2 敏感设置

- 首次启动生成 `data/secrets.key`，权限尽量设为 `0600`。
- OCR Token、Basic 密码、可能包含凭据的服务 URL 和代理凭据加密后写入 SQLite。
- 设置读取 API 只返回掩码和 `configured` 状态，不返回明文。
- 日志不得打印请求认证头、Token、密码或带凭据的完整 URL。
- 恢复数据库但缺少对应 `secrets.key` 时启动失败并给出明确恢复提示，不静默清空密钥。
- 加密主要保护脱离数据卷单独泄漏的数据库；拥有完整 `data/` 目录即拥有解密能力。

### 7.3 可选密码

环境变量：

```text
COMICLENS_ACCESS_PASSWORD=
```

- 未设置或为空：完全关闭门禁，不显示登录页。
- 设置后：登录接口做常量时间比较并签发服务端签名的 HttpOnly、SameSite Cookie。
- HTTPS 下设置 Secure；提供退出登录和会话状态接口。
- 会话载荷包含访问密码配置版本；环境变量中的密码改变后，旧会话立即失效。
- 除健康检查、门禁配置和登录接口外，业务 API 与媒体资源都要求有效会话。
- React 页面路由在未登录时跳转 `/login`；静态登录壳可以加载，但不能读取业务数据。
- 登录失败进行轻量内存限速。

会话签名密钥从 `data/secrets.key` 派生，因此正常重启不会使会话无故失效；Cookie 不保存访问密码本身。

如果服务监听非回环地址且未设置密码，启动日志和设置页显示安全提醒，但不强制启用密码。

## 8. 阅读器与实时翻译

### 8.1 初始阅读

进入章节后先取得章节阅读清单，并立即显示原图。翻译不能阻塞原图阅读。

阅读清单只包含受控的本地媒体 URL，不向浏览器暴露任意代理入口。原图在首次阅读或翻译时按需下载到公共原图缓存；阅读器和翻译管线使用同一个缓存服务及源图片级锁，避免并发重复下载。章节中的全部有效源图片都进入清单和任务，不沿用 ComicTranslator 原有的 20 张默认截断；若触发防御性数量上限，必须整章报错而不能静默返回半章。

阅读器内只有一个主要开关：“实时翻译”。设置页保存全局默认值；阅读器内的切换只覆盖当前阅读会话，不反写全局默认。

### 8.2 开启实时翻译

开启后：

1. 查询当前语义配置指纹对应的缓存和任务。
2. 已完成译图立即显示。
3. 存在暂停任务时，从第一张未完成源图片续接。
4. 不存在任务时创建章节翻译代次。
5. 普通图片完整完成后原位切换为译图。
6. 长图完整复用 ComicTranslator 的切片、坐标偏移、文本去重和覆写逻辑；其渐进发布行为保持与该管线一致。

长图发布沿用现有算法边界：串行 OCR 分片模式可以在安全阅读分片完成后渐进预览；并行 OCR 分片模式等待全部分片汇总后发布。如果后续 OCR 分片失败，已发布内容只视为临时预览，整张源图片回退原图并显示源图级失败卡；重试时按完整源图片恢复，不能把部分预览标记为完成缓存。

前端在任务活跃时约每秒轮询任务状态。使用稳定的源图片/分片 key 和预留尺寸更新 `src`，避免译图出现时改变列表顺序或造成明显滚动跳动。

manifest 返回带不可变内容版本的媒体 URL，或返回可用于 URL 查询参数的版本号。重译成功后 URL 必须变化，不能依赖浏览器重新请求同一个长期缓存 URL 来发现新译图。

### 8.3 关闭实时翻译

关闭开关时：

1. 前端立即把当前章节全部切回原图。
2. 后端设置 `stop_after_current_source_image`。
3. 如果当前没有正在处理的源图片，任务立即暂停。
4. 如果正在下载、OCR、翻译或渲染一张源图片，则完成该完整源图片及全部长图分片，原子保存成果，然后暂停，不开始下一张。
5. 当前图片失败也算本次处理结束；记录失败后暂停。
6. 停止收尾期间重新开启会清除停止请求，并继续后续图片。

离开阅读器不停止任务。只有关闭实时翻译开关才触发上述图片边界停止。

这里的“未完成”只包含 `pending` 和因进程中断回滚后的待处理页。已明确进入 `failed` 的页不会在每次进入章节时无限自动重试，只能通过“重新翻译此图”或“重新翻译本话”恢复。

### 8.4 翻译配置指纹

指纹用于判断旧译图是否与当前语义设置兼容，包含：

- 源图片内容身份。
- 源语言和目标语言。
- OCR 模式与模型。
- 长图切片参数。
- 渲染器版本和字体身份。

凭据、API URL、代理、请求超时、并发和轮询间隔不进入指纹。它们改变执行方式而不应泄露到散列或自动触发重译。

语义设置改变后，下一次在实时翻译开启状态进入章节会创建新代次。旧译图在新结果成功前继续可用。

## 9. 重译与失败恢复

### 9.1 重新翻译本话

阅读器工具栏提供“重新翻译本话”。确认框明确说明会重新调用 OCR 和翻译接口。

如果当前阅读会话的实时翻译处于关闭状态，确认重译会同时把该会话切换为开启；这是一次明确的翻译操作，但不会修改设置页中的全局默认值。

确认后：

1. 创建新翻译代次，即使语义指纹未变化也从第一张源图片重新执行 OCR 和翻译。
2. 如果旧任务仍在运行，先让其完成当前完整源图片，再切换到新代次，禁止两个代次并发处理同一章节。
3. 旧译图继续可读。
4. 新代次每成功一张，原子更新该页的活动译图指针；失败页继续使用旧译图，若没有旧译图则使用原图。
5. 新代次结束后成为当前代次；旧代次进入普通容量淘汰范围。

“成为当前代次”不要求失败页指向新代次：`active_translation_pages` 逐页选择最后一次成功结果，因此一次 `completed_with_errors` 的重译可以同时引用新代次成功页和旧代次兜底页。仍被活动页引用的旧代次不得淘汰。

重复点击只返回同一个活动重译任务，不创建重复代次。

### 9.2 单图失败与重试

源图片失败时不阻断后续图片。阅读流保留原图，并在翻译模式下显示小型错误卡和“重新翻译此图”按钮。

按失败阶段复用成果：

| 失败阶段 | 单图重试行为 |
|---|---|
| 原图下载 | 从下载开始 |
| OCR | 清理该源图片的不完整 OCR 分片，重做完整源图片 OCR |
| 翻译 | 复用 OCR 文本和坐标，只重试翻译与覆写 |
| 渲染/文件写入 | 复用 OCR 与译文，只重新渲染和原子写入 |

长图的“此图”始终表示完整源图片。不能只重试其中一个 OCR 或阅读分片，以免文本去重、坐标和覆写结果不一致。

如果重译代次中的某页失败但旧代次已有译图，旧译图保持活动，并在工具栏/页角显示该页重译失败和重试入口。

### 9.3 自动重试与锁

- 可重试网络错误在显示手动按钮前自动重试最多 2 次，并使用短退避。
- 页级错误保存阶段、稳定错误码、脱敏摘要、时间和尝试次数。
- 以 `(comic_id, chapter_id, source_page_index, generation)` 加锁。
- 同一章节同一时刻最多一个正常任务或重译任务执行。
- 单图重试使用最新连接凭据和运行参数，但保持所属代次的语义设置快照。

## 10. 任务状态机与重启恢复

### 10.1 章节任务状态

```text
queued
  → running
  → stopping_after_page → paused
  → completed
  → completed_with_errors

queued/running → failed       # 仅章节清单无法取得等致命错误
paused → running              # 再次开启实时翻译
```

单张源图片状态：

```text
pending → downloading → ocr → translating → rendering → completed
                                         └──────────────→ failed
```

页失败后任务可继续处理下一页，因此页失败不等同于章节任务 `failed`。

### 10.2 检查点

每个阶段完成后持久化必要检查点：

- 原图写入成功后再记录原图路径。
- OCR 原始响应和标准化文本块原子写入后再标记 OCR 完成。
- 译文保存后再进入渲染状态。
- 译图和分片全部原子写入后再更新活动页指针。

### 10.3 服务重启

启动时：

1. 将遗留的 `queued`、`running`、`stopping_after_page` 归一为 `paused`。
2. 校验数据库记录指向的文件；只有文件完整存在的阶段才保留完成状态。
3. 删除 `.tmp` 等未完成文件。
4. 重新进入章节且实时翻译开启时，从第一张未完成源图片续接。

单 worker 是第一版明确约束；不得通过增加 Uvicorn worker 横向扩展进程内任务调度。

## 11. 持久化模型

### 11.1 SQLite

至少包含以下表：

| 表 | 作用 |
|---|---|
| `app_settings` | 普通和加密后的敏感服务器设置 |
| `favorites` | Comic 摘要快照和收藏时间 |
| `reading_history` | 最近章节、页码、总页数和最后阅读时间 |
| `read_chapters` | 已读章节集合 |
| `translation_generations` | 章节翻译代次、语义快照、状态和进度 |
| `translation_pages` | 每代次每张源图片的阶段、路径、错误和检查点 |
| `active_translation_pages` | 当前每页活动译图指针，支持重译时逐页替换 |
| `cache_bundles` | 章节缓存包大小、访问时间和保护状态 |
| `cache_entries` | 缓存包内文件相对路径、类型、大小和校验信息 |

收藏与历史不会因缓存淘汰而删除。

章节缓存被淘汰时，其 `translation_generations`、`translation_pages`、活动页指针和缓存索引随文件一起清理；这些是可重新生成的缓存事实。收藏、历史、已读状态和应用设置不属于缓存，必须继续保留。

### 11.2 文件目录

```text
data/
  comiclens.db
  secrets.key
  cache/
    covers/
    chapters/{chapter_key}/
      originals/
      generations/{generation_id}/
        ocr/
        blocks/
        translations/
        display-parts/
```

数据库只保存相对 `data/` 的路径。任何文件写入都先写同目录临时文件，flush 后原子替换，禁止客户端读到半写文件。

## 12. 长期缓存与 5 GB 上限

### 12.1 保留策略

以下数据没有时间 TTL：

- 规范化原始 Comic 图片。
- 长图原始阅读分片。
- OCR 原始响应。
- 标准化文本块和坐标。
- 译文文本。
- 覆写译图和译图阅读分片。
- 相关任务元数据。

只要总缓存没有超过容量上限，它们就长期保留。默认：

```text
COMICLENS_CACHE_MAX_MB=5120
```

数据库设置中的容量值覆盖该环境变量提供的初始默认值。

容量统计覆盖 `data/cache/` 下由缓存索引管理的封面、原图、OCR/文本产物和译图文件；`comiclens.db`、`secrets.key` 和普通应用日志不计入这 5 GB，但日志必须单独轮转，不能无限增长。

### 12.2 章节缓存包

自动淘汰以完整章节缓存包为基本单位。一个包包括该章节的原图、所有 OCR/翻译代次和任务缓存元数据。

访问 Comic 详情中的该章节、打开原图或命中译图都会刷新章节包的 `accessed_at`。

封面不属于章节，因此作为独立的 Comic 封面缓存单元参与同一个容量上限和 LRU 排序。阅读会话保护使用服务器租约实现：读取章节 manifest 或媒体文件会把该章节的 `protected_until` 延长 5 分钟，持续阅读会持续续租，离开后保护自动结束。

### 12.3 淘汰顺序

只有写入后超过容量上限才执行：

1. 排除运行中、停止收尾中和阅读租约有效的章节。
2. 在同一章节中优先删除不再被任何活动页指针引用的完整旧重译代次，但不删除原图和当前仍被引用的代次。
3. 仍超限时，把独立封面单元和章节缓存包按最近访问时间从旧到新排序；封面可单独删除，章节只能整包删除。
4. 文件删除与数据库索引更新保持一致；失败时下次维护扫描修复孤儿。
5. 当前受保护内容可能使缓存临时超过上限；任务/阅读结束后再收敛。

设置页显示占用、上限和条目数，并提供：

- 删除单章缓存。
- 清除全部普通缓存。
- 调整容量上限。

这些操作需要确认，且不能删除活动任务正在使用的文件。

## 13. API 边界

建议的主要接口：

```text
GET    /api/home/feed
GET    /api/home/latest?page={page}
GET    /api/comics/search?q={query}&page={page}
GET    /api/comics/categories
GET    /api/comics/categories/{category_id}?page={page}&order={latest|rating|views}
GET    /api/comics/ranking?page={page}
GET    /api/comics/{comic_id}
GET    /api/comics/{comic_id}/chapters/{chapter_id}/manifest

GET    /api/favorites
PUT    /api/favorites/{comic_id}
DELETE /api/favorites/{comic_id}
GET    /api/history
PUT    /api/history/{comic_id}
DELETE /api/history/{comic_id}

GET    /api/settings
PATCH  /api/settings
GET    /api/system/cache
DELETE /api/system/cache
DELETE /api/system/cache/comics/{comic_id}/chapters/{chapter_id}

GET    /api/comics/{comic_id}/chapters/{chapter_id}/translation
POST   /api/comics/{comic_id}/chapters/{chapter_id}/translation/start
POST   /api/comics/{comic_id}/chapters/{chapter_id}/translation/pause
POST   /api/comics/{comic_id}/chapters/{chapter_id}/translation/retranslate
POST   /api/comics/{comic_id}/chapters/{chapter_id}/translation/pages/{page_index}/retry

GET    /api/media/covers/{comic_id}
GET    /api/media/comics/{comic_id}/chapters/{chapter_id}/pages/{page_index}/{variant}

GET    /api/auth/config
GET    /api/auth/session
POST   /api/auth/login
POST   /api/auth/logout
GET    /health
```

`/api/home/feed` 分别返回重点更新和最新更新第一页，继续加载使用 `/api/home/latest`；`/api/comics/ranking` 固定返回 `period = "week"`，明确表示来源只提供周榜。四个分页列表接口都返回第 5.3 节的 `ComicListPage`，分类接口另回显实际 `order`，搜索接口回显规范化查询词。`page` 必须是大于等于 1 的整数，`order` 只能使用白名单值。

目录响应中的上游封面 URL 先由后端校验并登记为封面缓存元数据，再转换成受控的 `/api/media/covers/{comic_id}`；这份媒体索引不等同于持久化全站目录，也不能作为陈旧目录结果返回。

媒体 API 通过领域 ID 和受控 variant 查找数据库记录，不接受文件系统路径或任意远端 URL。

## 14. 错误处理

- 上游目录失败：显示重试页，不用陈旧服务器目录冒充最新结果。
- 上游章节解析失败：原图清单不可用，章节任务进入致命 `failed`。
- 单图抓取/OCR/翻译/渲染失败：保留原图、记录页错误、继续下一页。
- 服务接口未配置：实时翻译开关开启时显示设置引导，不创建空任务。
- 磁盘空间或缓存写入失败：不标记阶段完成，保留可重试状态。
- 数据库与文件不一致：启动和定期维护扫描修复孤儿索引及临时文件。
- 所有 API 错误返回稳定 `code`、用户可读 `message` 和 `retryable`，前端不解析日志文本判断状态。

## 15. 测试与验收

### 15.1 后端单元测试

- Manga18fx 首页重点更新、最新更新、搜索、分类、周榜、详情和章节 fixture 解析。
- 首页最新更新路径分页、搜索 query 分页、分类路径分页、三种分类排序、周榜 query 分页和分页状态解析。
- 空搜索结果、空评分、重复分类、异常页面及站点结构变化的判定。
- URL 规范化、ID 生成和 SSRF 防护。
- ComicTranslator 长图切片、文本去重、坐标偏移和渲染。
- 翻译指纹、设置掩码和任务状态机。
- 图片边界停止、重新开启、章节重译和页级重试决策。

### 15.2 后端集成测试

使用伪 OCR、伪 DeepLX 和临时 SQLite/文件目录覆盖：

- 普通图片逐张完成。
- 长图多分片成功与某分片失败。
- OCR/翻译超时、429、5xx 和退避重试。
- 关闭后完成当前完整源图片并暂停。
- 服务重启后的文件校验与续接。
- 重译时旧图保留和新图逐页原子替换。
- 单图按失败阶段复用成果。
- 章节包 LRU、旧代次优先删除和活动任务保护。

### 15.3 前端测试

- 首页最新更新、搜索词、分类排序、四类分页契约和领域 DTO 映射。
- 实时翻译默认值与会话覆盖。
- 状态轮询、稳定图片 key 和滚动位置保持。
- 失败卡、重复重试点击和整话重译确认。
- 原图/译图即时切换和停止收尾状态提示。
- 密钥设置的 keep/replace/clear 表单协议。

### 15.4 端到端烟测

1. 未配置密码时直接进入首页。
2. 配置密码时登录后才能访问 API 和图片。
3. 浏览首页最新更新并继续加载；执行搜索并翻页；切换分类的最新、评分、浏览量排序；浏览周榜并翻页；进入详情和章节。
4. 开启翻译，看到原图先出现、译图逐张替换。
5. 关闭翻译，立即回原图，当前源图片结束后停止。
6. 再次开启，从缓存和下一张未完成图片续接。
7. 注入单图失败并点击“重新翻译此图”。
8. 点击“重新翻译本话”，确认旧译图在新图成功前可读。
9. 重启容器并验证设置、历史、收藏、缓存和任务检查点。

### 15.5 完成标准

- 所有业务代码中的泛化领域名称均为 `comic`。
- 一个 Docker 容器可启动前后端，持久卷只需挂载 `/app/data`。
- 不配置访问密码时无登录流程；配置后业务资源无法绕过会话访问。
- 首页最新更新、搜索、分类三种排序和周榜分页均使用第 5.2 节验证过的真实上游路径，且解析结果符合第 5.3 节 DTO。
- 目录不写入全站索引，行为符合实时请求 + React Query 缓存边界。
- 翻译、停止、续接、整话重译、单图重试和长图处理符合本规格。
- 缓存没有时间 TTL；只有超过默认 5 GB 才按规则淘汰。
- 自动化测试覆盖关键状态和故障路径，构建、类型检查和 lint 通过。

## 16. 部署与运维

Docker Compose 至少提供：

```text
COMICLENS_ACCESS_PASSWORD=
COMICLENS_CACHE_MAX_MB=5120
COMICLENS_DATA_DIR=/app/data
PORT=8233
```

OCR、DeepLX、代理和阅读设置在 Web 设置页维护。可以用环境变量为首次启动提供初值，但不要求每次改 Compose。

挂载：

```text
./data:/app/data
```

备份完整 `data/` 即可迁移数据库、密钥、原图、OCR 数据和译图。恢复时数据库与 `secrets.key` 必须成对恢复。

## 17. 实施注意事项

- 从 jm-boom 复用前端模式时保留其 MIT 许可声明和必要版权信息。
- 从 ComicTranslator 整合代码时先保留现有行为测试，再按模块边界迁移，避免在同一步重写算法。
- 不手改 TanStack Router 的生成路由文件。
- 先建立领域模型、来源 fixture 和任务状态机，再连接真实 OCR/DeepLX。
- 第一版优先保证单用户单进程下的确定性，不为假想多 worker 提前引入分布式复杂度。
