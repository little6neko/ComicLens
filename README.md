# ComicLens

ComicLens 是一个个人使用的自托管 Comic 阅读与实时图片翻译站。项目当前按
[`ComicLens 实施计划`](docs/superpowers/plans/2026-08-12-comiclens-implementation.md)
开发。

## 开发环境

后端：

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8233
```

前端：

```bash
cd web
npm install
npm run dev
```

默认监听 `0.0.0.0:8233`。`COMICLENS_ACCESS_PASSWORD` 留空时不启用登录门禁。

## License

[MIT](LICENSE)
