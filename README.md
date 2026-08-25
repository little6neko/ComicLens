# ComicLens

ComicLens 是一个个人使用、单用户自托管的 Comic 阅读与实时图片翻译站。它实时读取
Manga18fx 的首页、搜索、全部分类、Manhwa Raw、周热门、Comic 详情和章节，并将
ComicTranslator 的长图切片、OCR、翻译与译文覆写管线集成到阅读器。

## 功能

- Maia 风格的移动端优先 UI，支持搜索、55 个分类入口、三种排序和周榜分页。
- Comic 收藏、阅读历史、已读章节和阅读进度保存在服务器。
- 条漫、单页、双页阅读模式；阅读器使用可自动隐藏的顶部控制栏和底部阅读胶囊。
- 翻译任务随源图到达持续累加分片；OCR 可按设置的全服务并发数预取，翻译、渲染与展示仍严格按分片顺序，完成一片立即显示一片。
- 关闭实时翻译后立即显示原图，后端完成当前分片后暂停。
- 支持重新翻译本话，以及 OCR/翻译/渲染失败后的单分片重试。
- 原图、OCR 结果和译图无时间 TTL，在默认 5 GB 上限内长期保留并按 LRU 淘汰。
- PaddleOCR 同步/异步协议与鉴权、DeepL/DeepLX、漫画代理、长图参数和阅读默认值均可在 Web
  设置页维护；主题、阅读模式和默认实时翻译仅保存在当前浏览器并即时生效。
- 可选环境变量密码；不填写时不启用登录界面。

## Docker 镜像（推荐）

正式版本发布在 GitHub Container Registry，支持 `linux/amd64` 和 `linux/arm64`，Docker
会自动选择当前主机对应的架构。建议生产部署固定版本标签：

```bash
docker pull ghcr.io/little6neko/comiclens:v0.1.5
```

也可以使用始终指向最新正式版本的 `latest`：

```bash
docker pull ghcr.io/little6neko/comiclens:latest
```

在准备保存数据的目录中启动固定版本：

```bash
mkdir -p data
docker run -d \
  --name comiclens \
  --restart unless-stopped \
  --init \
  --security-opt no-new-privileges:true \
  -p 8233:8233 \
  -v "$(pwd)/data:/app/data" \
  ghcr.io/little6neko/comiclens:v0.1.5
```

打开 <http://127.0.0.1:8233>；从其他设备访问时，将 `127.0.0.1` 换成服务器地址。容器监听
`0.0.0.0:8233`，如需修改宿主机端口，只需将 `-p 8233:8233` 左侧的端口改为目标端口。

不传 `COMICLENS_ACCESS_PASSWORD` 时不会启用密码界面。需要密码时，请用下面的命令首次
启动，或删除旧容器后使用该命令重新创建：

```bash
docker run -d \
  --name comiclens \
  --restart unless-stopped \
  --init \
  --security-opt no-new-privileges:true \
  -p 8233:8233 \
  -e COMICLENS_ACCESS_PASSWORD='换成足够长的密码' \
  -v "$(pwd)/data:/app/data" \
  ghcr.io/little6neko/comiclens:v0.1.5
```

首次部署时可以额外添加 `-e COMICLENS_PROXY_URL='http://代理地址:端口'`，为 Web 设置中的
“漫画代理 URL”提供初值。该字段非空时，漫画目录、搜索、详情、章节和源图只走指定代理，
失败后不会改为直连；部署后可直接在设置页编辑或清除 URL，并可另行填写代理账号和加密保存
的代理密码。

应用也遵循 httpx 支持的 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 和 `NO_PROXY`。漫画代理
URL 留空时，漫画请求使用这些标准环境变量；OCR、DeepL 和 DeepLX 不使用漫画代理 URL，
但始终可以受标准环境变量控制。使用 `docker run` 时通过 `-e` 传入所需变量；Compose 部署可
在 `.env` 中填写，项目会将其原样传入容器。未设置应用代理和标准代理时均为直连。

查看状态和日志：

```bash
docker ps --filter "name=^/comiclens$"
docker logs -f comiclens
```

升级时先把变量改成准备部署的版本标签，再拉取镜像并重建容器；`data` 是宿主机目录，停止
和删除容器不会删除其中的设置、历史、缓存与翻译结果。重新创建时需要保留仍在运行期使用的
`-e` 参数，尤其是访问密码和标准代理环境变量；已写入数据库的漫画代理 URL 不依赖容器重建
时再次传入 `COMICLENS_PROXY_URL`。下面仍以不启用密码和代理为例：

```bash
COMICLENS_VERSION=v0.1.5
docker pull "ghcr.io/little6neko/comiclens:${COMICLENS_VERSION}"
docker stop comiclens
docker rm comiclens
docker run -d \
  --name comiclens \
  --restart unless-stopped \
  --init \
  --security-opt no-new-privileges:true \
  -p 8233:8233 \
  -v "$(pwd)/data:/app/data" \
  "ghcr.io/little6neko/comiclens:${COMICLENS_VERSION}"
```

如果 GHCR 包尚未设为公开，请先创建具有 `read:packages` 权限的 GitHub token，再登录：

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u little6neko --password-stdin
```

## Docker Compose（源码构建）

要求 Docker Engine 与 Compose 插件。首次运行：

```bash
cp .env.example .env
mkdir -p data
docker compose up -d --build
```

打开 <http://127.0.0.1:8233>。容器内部固定以单 worker 监听 `0.0.0.0:8233`，宿主机端口
可用 `.env` 中的 `PORT` 修改。

个人网络外可访问时，建议在 `.env` 中设置：

```text
COMICLENS_ACCESS_PASSWORD=换成足够长的密码
```

如果保持为空，ComicLens 会直接进入首页，不显示密码界面。设置密码后重新创建容器：

```bash
docker compose up -d
```

查看状态和日志：

```bash
docker compose ps
docker compose logs -f comiclens
```

## 诊断日志

ComicLens 默认以 `COMICLENS_LOG_LEVEL=INFO` 将入站访问日志和安全的业务诊断事件写到容器
stdout/stderr，不创建日志文件。业务事件采用 `级别 服务 event=类型 字段...` 的固定格式，
服务名位于最前，便于直接区分漫画、OCR、翻译与任务阶段。例如：

```text
INFO manga event=request operation=detail request_ref=00000001 method=GET endpoint=https://manga.example/manga/demo route=environment_or_direct attempt=1 redirect_index=0
INFO manga event=response operation=detail request_ref=00000001 status=200 duration_ms=184 response_bytes=48231 content_type=text/html endpoint=https://manga.example/manga/demo attempt=1 redirect_index=0
INFO ocr event=request operation=analyze request_ref=00000002 protocol=direct auth=bearer image_bytes=248391 payload_bytes=331298 method=POST endpoint=https://ocr.example/layout-parsing attempt=1
INFO ocr event=state operation=poll protocol=job job_ref=7c9a12ef state=running poll_count=2
INFO deepl event=response operation=translate_batch request_ref=00000003 auth=api_key text_count=8 total_chars=214 payload_bytes=301 source_lang=EN target_lang=ZH-HANS status=200 duration_ms=326 response_bytes=418 content_type=application/json endpoint=https://api.deepl.com/v2/translate attempt=1
INFO task event=stage_complete stage=render duration_ms=41 cached=false output_bytes=198204 generation_ref=23f7b1ac comic=demo chapter=chapter-1 page_index=0 segment_index=1 global_index=1
```

INFO 会记录每次外部请求/响应、重试与最终失败、OCR 异步状态变化、翻译任务阶段和相关缓存
命中。重复的 OCR poll 请求/响应不会出现在 INFO；将 `COMICLENS_LOG_LEVEL=DEBUG` 写入 `.env`
并重建 Compose 容器后，才会显示每次 poll，以及非 2xx 或协议异常响应的脱敏、最多 1024
字符 JSON 摘要。`docker run` 部署可在创建容器时添加
`-e COMICLENS_LOG_LEVEL=DEBUG`。

即使使用 DEBUG，日志也不会输出成功响应正文、源图/Base64、OCR 文本、原文/译文、请求
Header、Cookie、Token、API Key、Basic 或代理凭据。普通 endpoint 会删除 userinfo、query 和
fragment；OCR 异步结果地址只显示 origin。Compose 的 `json-file` 日志轮转保持为单文件
`10m`、最多 `3` 个文件。

## 数据与缓存

上述 `docker run` 和 Compose 均将 `./data` 挂载到容器的 `/app/data`，其中包含：

- `comiclens.db`：设置、收藏、历史、已读和翻译任务状态；OCR API URL、漫画代理 URL 和代理
  账号以明文保存在其中；
- `secrets.key`：敏感设置的加密密钥；
- `cache/`：封面、原图、OCR/译文检查点和译图。

默认缓存上限是 `5120 MB`。内容没有时间过期，只在超过上限时按容量规则淘汰。可以在 Web
设置页修改上限或清理缓存。

备份时建议先停止容器，再完整复制 `data/`。下面的命令对 Docker 镜像和 Compose 部署均
适用：

```bash
docker stop comiclens
tar -czf comiclens-data.tar.gz data
docker start comiclens
```

恢复时必须同时恢复数据库与 `secrets.key`，不能只恢复其中一个。请妥善保管备份，因为完整
`data/` 目录可以解密其中的 OCR/翻译服务凭据和独立代理密码。

## OCR、翻译与代理设置

进入设置页，相关内容按“OCR → OCR 长图高级设置 → 翻译 → 代理”分区：

1. 源语言：自动识别（默认）、英语或韩语；目标固定为简体中文；
2. OCR 模式：自动识别（默认）、同步接口或异步任务接口；
3. OCR API URL，以及无鉴权（默认）、Bearer Token 或 Basic Auth；
4. OCR 模型、轮询间隔、总超时和全服务并发；
5. OCR 长图阈值、分片高度、重叠和兼容阅读分片高度；
6. 翻译服务：默认使用 DeepL 官方 API，也可切换为 DeepLX，并配置凭据、超时与并发；
7. 可选漫画代理 URL、账号和密码，仅控制漫画目录和源图请求。

PaddleOCR 同时支持 PaddleX 服务化部署的同步 JSON 接口和云端异步任务接口。新安装的 OCR
URL 是示例值 `http://example.com/layout-parsing`，必须替换为自己的服务地址。自动模式将
路径以 `/ocr/jobs` 结尾的地址识别为异步任务，其余所有地址均按同步接口正常尝试；PaddleX
同步接口通常以 `/layout-parsing` 结尾。ComicLens 不会自动补全路径，也不会针对 `/v1`
增加专用协议。默认模型为 `PaddleOCR-VL-1.6`，模型和轮询设置只由异步任务协议使用。

同步请求、异步任务提交和轮询使用当前选择的鉴权。异步结果地址仅在与 OCR API 的协议、
主机和有效端口均相同时携带 Basic Auth；跨源结果不携带 Basic，Bearer Token 不发送给
任何结果下载地址。切换鉴权模式不会清除已保存的另一套凭据。

DeepL Key 以 `:fx` 结尾时自动使用 Free API，否则使用 Pro API；DeepL 与 DeepLX 之间不会
在失败时自动回退。

新翻译任务默认按 `1600px` 高度切片并保留 `200px` 重叠上下文，OCR 请求不发送
`useOcrForImageBlock`，避免图片区域被误渲染为大文本框。任务获取第一张源图并切片后会立即
开始 OCR、翻译和显示，同时继续准备后续源图；顶部进度分母会随新分片动态增加。分片调度
会按设置并发预取 OCR，新安装默认值为 `2`，该上限由所有章节共享并在保存设置后立即生效；翻译、
渲染和发布仍按分片顺序串行。翻译并发只用于当前分片内部的文本批次，不会让后续分片乱序
显示。异步模式下 PaddleOCR `jobId` 会持久化，超时或服务重启后优先继续轮询远端任务；
OCR 失败后的手动重试会创建新任务。同步模式不保存 `jobId`，重试时重新发送完整请求。

设置页的“进入章节时默认实时翻译”切换后直接写入当前浏览器，无需点击“保存全部设置”，
并从之后进入的章节开始使用。已经打开的当前章节仍由阅读器顶部实时翻译开关独立控制；该
开关不会反写浏览器默认值。

OCR Token、Basic 密码、DeepL Key、DeepLX URL 和独立代理密码等敏感字段只返回掩码，编辑
时明确选择“保留 / 替换 / 清除”。OCR API URL、漫画代理 URL 和代理账号则以明文保存在
SQLite 中，由 `/api/settings` 完整返回并直接显示在设置页；如果把凭据写进代理 URL，这些凭据
也会以明文可见。

“漫画代理 URL”非空时，所有漫画目录和源图请求只使用该代理，同一请求的重试与重定向不会
切换线路。独立账号或密码任一非空时，会在每次请求前覆盖 URL 自带凭据，但不会拆分、修改或
写回原 URL；两者均为空时原 URL 保持生效。URL 留空时由标准代理环境变量和 `NO_PROXY` 决定
使用代理还是直连。OCR、DeepL 和 DeepLX 不读取这些漫画代理字段，但同样遵循标准代理环境
变量。

`.env.example` 中的 `COMICLENS_OCR_*`、`COMICLENS_DEEPL_API_KEY`、
`COMICLENS_DEEPLX_URL` 和 `COMICLENS_PROXY_URL` 只用于首次初始化或设置结构升级重建时提供
初值，之后以数据库中的值为准。`HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 和 `NO_PROXY`
则是每次启动时由 httpx 直接读取的运行期变量。Basic 用户名和密码环境变量不会自动切换
鉴权模式，仍需在 Web 设置页选择 Basic Auth。独立代理账号和密码不提供环境变量，只能在
设置页维护。

## 本地开发

后端：

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8233
```

前端（开发服务器会代理 `/api` 到 `127.0.0.1:8233`）：

```bash
cd web
npm ci
npm run dev
```

检查：

```bash
.venv/bin/pytest
.venv/bin/ruff check app tests
cd web && npm run build && npm run lint && npm run fmt:check
```

## 注意事项

- ComicLens 与 Manga18fx、其内容提供者、PaddleOCR、DeepL 或 DeepLX 服务提供者无隶属关系。
- 仅限个人、研究和技术交流用途；请自行遵守所在地法律、内容版权和各服务条款。
- Manga18fx 页面结构变化时，来源解析可能需要随之更新。
- 翻译调度是单进程设计，不要把 Uvicorn worker 数量调大。

实现规格见[基础设计文档](docs/superpowers/specs/2026-08-12-comiclens-reader-design.md)、
[分片渐进翻译设计](docs/superpowers/specs/2026-08-13-progressive-segment-reader-design.md)和
[实施计划](docs/superpowers/plans/2026-08-13-progressive-segment-reader-implementation.md)。第三方归属见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## License

[MIT](LICENSE)
