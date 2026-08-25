# OCR 自动模式后缀匹配调整设计

## 目标

收窄 OCR `auto` 模式的异步接口识别规则，去除对 `/api/v2/ocr/jobs` 的额外包含判断。
自动模式只根据统一的 `/ocr/jobs` 路径后缀区分异步与同步，避免同一协议存在两套判断。

本设计是
`docs/superpowers/specs/2026-08-25-sync-ocr-auth-restoration-design.md` 中“协议选择”部分的
补充；冲突之处以本设计为准。

## 协议解析

`auto` 模式先通过 URL 解析器取得路径，忽略 query 和 fragment，再移除路径末尾的 `/`。
规范化后：

- 路径以 `/ocr/jobs` 结尾时解析为 `job`；
- 其他所有路径解析为 `direct`。

因此以下地址识别为异步：

- `https://ocr.example/ocr/jobs`
- `https://ocr.example/api/v2/ocr/jobs`
- `https://ocr.example/custom/ocr/jobs/?region=test`

以下地址识别为同步：

- `https://ocr.example/layout-parsing`
- `https://ocr.example/v1`
- `https://ocr.example/api/v2/ocr/jobs/123`
- `https://ocr.example/ocr/jobs-old`

显式 `direct` 和 `job` 仍优先于 URL，不改变行为。此次只修改协议解析谓词，不改变同步
请求体、异步任务提交与轮询、鉴权、结果下载、重试、并发或 generation 语义。

## 设置界面文案

OCR 模式下拉框中的自动选项改为：

> 自动识别（默认）

OCR 模式辅助说明改为：

> 自动模式仅将以 `/ocr/jobs` 结尾的地址识别为异步任务，其余地址按同步接口调用；
> PaddleX 同步接口通常以 `/layout-parsing` 结尾。

同步后缀说明只用于帮助用户填写完整 URL，不用于增加同步 URL 校验或自动补全。未匹配
`/ocr/jobs` 的地址仍直接按同步协议正常尝试。

## 文档与测试

- 更新原设计规范和 README，统一为单一 `/ocr/jobs` 后缀规则，并说明同步接口通常以
  `/layout-parsing` 结尾。
- 扩展协议解析参数化测试，覆盖尾部斜杠、查询参数、带前缀的异步路径，以及
  `/ocr/jobs/123`、`/ocr/jobs-old` 不应误判的边界。
- 运行 OCR 算法测试、完整 Pytest、Ruff，以及前端格式检查、Lint 和生产构建。
- 前端构建完成后重启当前监听 `0.0.0.0:8233` 的临时测试服务，供 UI 检查。

## 不在范围内

- 不探测远端服务协议。
- 不自动追加 `/ocr/jobs` 或 `/layout-parsing`。
- 不为 `/v1` 增加专用协议。
- 不修改任何鉴权或异步任务生命周期行为。
