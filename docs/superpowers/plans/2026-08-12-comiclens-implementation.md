# ComicLens 实施计划

日期：2026-08-12  
依据：`docs/superpowers/specs/2026-08-12-comiclens-reader-design.md`

## 实施原则

- 后端使用 FastAPI、HTTPX、BeautifulSoup/lxml、SQLite 和单进程 asyncio 任务调度。
- 前端使用 React、TypeScript、Vite、TanStack Router/Query、Tailwind CSS 和 shadcn/ui `radix-maia` 风格。
- ComicTranslator 的抓图、长图分片、OCR、DeepLX 和图片覆写算法迁入本仓库；运行时不依赖原仓库。
- jm-boom 只作为 MIT 许可下的 UI/工程模式参考；复用部分保留许可与致谢，所有通用领域命名改为 `comic`。
- 每个里程碑完成对应测试后单独提交。不得提交密钥、数据卷、缓存、日志、构建目录或半成品临时文件。

## 里程碑 1：工程骨架

目标提交：`chore: scaffold ComicLens application`

新增：

- `pyproject.toml`：生产依赖、测试依赖、Ruff、Pytest 配置。
- `app/`：配置、应用工厂、稳定错误 DTO、生命周期与静态站点回退。
- `web/`：React/Vite/TanStack/Tailwind 基础工程、路由壳和 Maia 主题。
- `tests/`：应用工厂、健康检查和配置测试。
- `.env.example`、`LICENSE`、`README.md`。

验证：

- `pytest`
- `ruff check app tests`
- `npm run build`（前端依赖落盘后改用 Bun 锁定并执行）

## 里程碑 2：Manga18fx 来源适配器

目标提交：`feat: add Manga18fx catalog source`

新增：

- `app/domain/comic.py`：`ComicSummary`、`ComicDetail`、`ComicListPage`、`ChapterManifest`。
- `app/sources/base.py`：`ComicSource` 协议。
- `app/sources/manga18fx.py`：受控 URL 构造、重试、HTML/sitemap 解析、54 分类回退基线。
- 目录 API：首页、最新、搜索、分类、三种排序、周榜、详情和章节 manifest。
- HTML fixture：列表、空搜索、详情、章节、sitemap。

关键验收：

- 搜索和周榜使用 query 分页，分类使用路径分页。
- `raw` 与 `source:raw-feed` 映射为不同上游路径。
- 解析失败不会伪装为空结果。
- API 不接受任意来源 URL。

## 里程碑 3：SQLite、设置、门禁与用户数据

目标提交：`feat: add persistent settings and access gate`

新增：

- 版本化 SQL migrations 与 SQLite WAL repository。
- `data/secrets.key` 生成、敏感设置加密和 `keep | replace | clear` PATCH 协议。
- 可选 `COMICLENS_ACCESS_PASSWORD`，签名 HttpOnly Cookie、版本失效和登录限速。
- 收藏、阅读历史、已读章节 API。
- 设置、缓存概况和服务器安全提醒 API。

关键验收：

- 密码为空时所有业务接口直接可用且不出现登录流程。
- 密码启用时 API 和媒体都无法绕过 Cookie。
- 敏感值读取时只返回掩码状态。
- 重启后设置、收藏和历史仍存在。

## 里程碑 4：媒体缓存与翻译任务

目标提交：`feat: integrate progressive comic translation`

新增：

- 受控封面、源图片和译图媒体 API。
- 原子文件存储、章节包索引、阅读租约和 5 GB 容量 LRU。
- 从 ComicTranslator 迁入图片清洗、长图 OCR 分片、坐标归一、去重、DeepLX 和渲染模块。
- SQLite 翻译代次、页级检查点、活动译图指针和重启恢复。
- 开启、图片边界暂停、续接、整话重译和按失败阶段单图重试 API。

关键验收：

- 原图先可读；译图逐张原子发布。
- 关闭时前端可立即切回原图，后端完成当前完整源图片后暂停。
- 长图所有 OCR 分片属于同一源图片停止边界。
- 下载/OCR/翻译/渲染失败可按阶段复用成果。
- 缓存无时间 TTL，仅超容量时淘汰，活动章节受保护。

## 里程碑 5：Maia 前端

目标提交：`feat: build ComicLens web reader`

实现页面：

- 登录、首页、探索、搜索、54 分类与 Manhwa Raw、周榜。
- Comic 详情、收藏、历史、设置。
- 条漫/单页/双页阅读器、章节切换、进度同步。
- 实时翻译开关、停止收尾状态、整话重译、失败页重试。
- OCR、DeepLX、代理、长图参数、缓存和敏感字段操作设置。

关键验收：

- 移动端悬浮胶囊导航和桌面自适应布局。
- 译图 URL 版本变化但 DOM key 稳定，不丢滚动位置。
- 翻译关闭后不等待后端即可显示原图。
- React Query 缓存时长符合规格。

## 里程碑 6：部署和总验收

目标提交：`build: add single-container deployment`

新增：

- 多阶段 `Dockerfile`：Bun 构建前端，Python 运行 FastAPI，安装 Noto CJK。
- `docker-compose.yml`：监听 `0.0.0.0`、端口 8233、`/app/data` 持久卷、可选密码和 5120 MB 默认缓存。
- 健康检查、非 root 用户、日志轮转、`.dockerignore`。
- 完整 README、免责声明、许可与来源致谢。

最终验证：

- 后端全量单元/集成测试。
- 前端 TypeScript build、lint 和格式检查。
- Docker image 构建和容器健康检查。
- 无密码与有密码两套 HTTP 烟测。
- 真实 Manga18fx 首页、搜索、分类、周榜、详情和章节只读烟测。
- `git status` 干净，提交 Author/Committer 均为 `little6neko <little6carbon@163.com>`。

## 提交策略

除上述六个主提交外，允许在单个里程碑过大时按可验证子能力拆分，例如：

- `test: add Manga18fx parser fixtures`
- `feat: add chapter cache storage`
- `feat: add translation checkpoints`
- `feat(web): add reader translation controls`

每次提交前运行与改动直接相关的最小测试集；里程碑结束运行该层全量测试。只有所有最终验收完成后才推送实现分支到 `origin/main`。
