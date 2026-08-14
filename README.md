# ComicLens

ComicLens 是一个个人使用、单用户自托管的 Comic 阅读与实时图片翻译站。它实时读取
Manga18fx 的首页、搜索、全部分类、Manhwa Raw、周热门、Comic 详情和章节，并将
ComicTranslator 的长图切片、OCR、翻译与译文覆写管线集成到阅读器。

## 功能

- Maia 风格的移动端优先 UI，支持搜索、55 个分类入口、三种排序和周榜分页。
- Comic 收藏、阅读历史、已读章节和阅读进度保存在服务器。
- 条漫、单页、双页阅读模式；阅读器使用可自动隐藏的顶部控制栏和底部阅读胶囊。
- 翻译前先缓存整话源图并确定完整分片数；OCR、翻译和渲染严格按分片顺序执行，完成一片立即显示一片。
- 关闭实时翻译后立即显示原图，后端完成当前分片后暂停。
- 支持重新翻译本话，以及 OCR/翻译/渲染失败后的单分片重试。
- 原图、OCR 结果和译图无时间 TTL，在默认 5 GB 上限内长期保留并按 LRU 淘汰。
- PaddleOCR、DeepL/DeepLX、代理、长图参数和阅读默认值均可在 Web 设置页维护。
- 可选环境变量密码；不填写时不启用登录界面。

## Docker 镜像（推荐）

正式版本发布在 GitHub Container Registry，支持 `linux/amd64` 和 `linux/arm64`，Docker
会自动选择当前主机对应的架构。建议生产部署固定版本标签：

```bash
docker pull ghcr.io/little6neko/comiclens:v0.1.0
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
  ghcr.io/little6neko/comiclens:v0.1.0
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
  ghcr.io/little6neko/comiclens:v0.1.0
```

部署地区无法直接访问上游时，可以额外添加
`-e COMICLENS_PROXY_URL='http://代理地址:端口'`；能够直接访问时不要设置代理。

查看状态和日志：

```bash
docker ps --filter "name=^/comiclens$"
docker logs -f comiclens
```

升级时先把变量改成准备部署的版本标签，再拉取镜像并重建容器；`data` 是宿主机目录，停止
和删除容器不会删除其中的设置、历史、缓存与翻译结果。重新创建时需要保留此前使用的全部
`-e` 参数，尤其是访问密码和代理；下面仍以不启用密码和代理为例：

```bash
COMICLENS_VERSION=v0.1.0
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

## 数据与缓存

上述 `docker run` 和 Compose 均将 `./data` 挂载到容器的 `/app/data`，其中包含：

- `comiclens.db`：设置、收藏、历史、已读和翻译任务状态；
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
`data/` 目录可以解密其中的 OCR/翻译服务凭据。

## 翻译设置

进入“设置 → OCR 与翻译”配置：

1. 源语言：自动识别（默认）、英语或韩语；目标固定为简体中文；
2. PaddleOCR 异步任务 URL、Bearer Token、模型、轮询与超时；
3. 翻译服务：默认使用 DeepL 官方 API，也可切换为 DeepLX；
4. DeepL API Key 或 DeepLX URL，以及共用的翻译超时与并发；
5. 可选回退代理和长图切片参数。

PaddleOCR 只使用异步任务接口，默认 URL 为
`https://paddleocr.aistudio-app.com/api/v2/ocr/jobs`，默认模型为
`PaddleOCR-VL-1.6`。DeepL Key 以 `:fx` 结尾时自动使用 Free API，否则使用 Pro API；
DeepL 与 DeepLX 之间不会在失败时自动回退。

新翻译任务默认按 `1600px` 高度切片并保留 `200px` 重叠上下文，OCR 请求不发送
`useOcrForImageBlock`，避免图片区域被误渲染为大文本框。任务获取第一张源图并切片后会立即
开始 OCR、翻译和显示，同时继续准备后续源图；顶部进度分母会随新分片动态增加。分片调度
会按设置并发预取 OCR，默认值为 `1`，该上限由所有章节共享并在保存设置后立即生效；翻译、
渲染和发布仍按分片顺序串行。翻译并发只用于当前分片内部的文本批次，不会让后续分片乱序
显示。PaddleOCR `jobId` 会持久化，超时、重试或服务重启后优先继续轮询远端任务。

敏感字段只返回掩码，编辑时明确选择“保留 / 替换 / 清除”。`.env.example` 中的
`COMICLENS_OCR_*`、`COMICLENS_DEEPL_API_KEY`、`COMICLENS_DEEPLX_URL` 和
`COMICLENS_PROXY_URL` 只用于首次初始化尚不存在的服务器设置，之后以数据库中的值为准。

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
