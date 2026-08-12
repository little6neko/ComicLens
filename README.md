# ComicLens

ComicLens 是一个个人使用、单用户自托管的 Comic 阅读与实时图片翻译站。它实时读取
Manga18fx 的首页、搜索、全部分类、Manhwa Raw、周热门、Comic 详情和章节，并将
ComicTranslator 的长图切片、OCR、翻译与译文覆写管线集成到阅读器。

## 功能

- Maia 风格的移动端优先 UI，支持搜索、55 个分类入口、三种排序和周榜分页。
- Comic 收藏、阅读历史、已读章节和阅读进度保存在服务器。
- 条漫、单页、双页阅读模式；源图片先显示，译图完成一张替换一张。
- 关闭实时翻译后立即显示原图，后端在当前完整源图片（含长图分片）完成后暂停。
- 支持重新翻译本话，以及下载/OCR/翻译/渲染失败后的单图重试。
- 原图、OCR 结果和译图无时间 TTL，在默认 5 GB 上限内长期保留并按 LRU 淘汰。
- PaddleOCR、DeepL/DeepLX、代理、长图参数和阅读默认值均可在 Web 设置页维护。
- 可选环境变量密码；不填写时不启用登录界面。

## Docker Compose（推荐）

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

Compose 将 `./data` 挂载到容器的 `/app/data`，其中包含：

- `comiclens.db`：设置、收藏、历史、已读和翻译任务状态；
- `secrets.key`：敏感设置的加密密钥；
- `cache/`：封面、原图、OCR/译文检查点和译图。

默认缓存上限是 `5120 MB`。内容没有时间过期，只在超过上限时按容量规则淘汰。可以在 Web
设置页修改上限或清理缓存。

备份时建议先停止容器，再完整复制 `data/`：

```bash
docker compose stop comiclens
tar -czf comiclens-data.tar.gz data
docker compose start comiclens
```

恢复时必须同时恢复数据库与 `secrets.key`，不能只恢复其中一个。请妥善保管备份，因为完整
`data/` 目录可以解密其中的 OCR/翻译服务凭据。

## 翻译设置

进入“设置 → OCR 与翻译”配置：

1. 源语言：自动识别（默认）、英语或韩语；目标固定为简体中文；
2. PaddleOCR 异步任务 URL、Bearer Token、模型、轮询、超时与并发；
3. 翻译服务：默认使用 DeepL 官方 API，也可切换为 DeepLX；
4. DeepL API Key 或 DeepLX URL，以及共用的翻译超时与并发；
5. 可选回退代理和长图切片参数。

PaddleOCR 只使用异步任务接口，默认 URL 为
`https://paddleocr.aistudio-app.com/api/v2/ocr/jobs`，默认模型为
`PaddleOCR-VL-1.6`。DeepL Key 以 `:fx` 结尾时自动使用 Free API，否则使用 Pro API；
DeepL 与 DeepLX 之间不会在失败时自动回退。

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

实现规格见[设计文档](docs/superpowers/specs/2026-08-12-comiclens-reader-design.md)和
[实施计划](docs/superpowers/plans/2026-08-12-comiclens-implementation.md)。第三方归属见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## License

[MIT](LICENSE)
