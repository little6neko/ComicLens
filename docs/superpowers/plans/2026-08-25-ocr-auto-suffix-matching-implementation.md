# OCR 自动模式后缀匹配调整实施计划

依据：`docs/superpowers/specs/2026-08-25-ocr-auto-suffix-matching-design.md`

## 阶段 1：协议边界测试与实现

- 扩展 `tests/test_translation_algorithms.py` 的参数化用例。
- 保留 `/ocr/jobs`、`/api/v2/ocr/jobs`、尾部斜杠和 query 的异步识别。
- 增加 `/ocr/jobs/123`、`/ocr/jobs-old` 必须解析为同步的回归用例。
- 将 `resolve_ocr_protocol` 收敛为单一 `path.endswith("/ocr/jobs")` 判断。
- 运行 OCR 算法测试与 Ruff。

## 阶段 2：界面和文档

- 将自动选项改为“自动识别（默认）”。
- 辅助说明只描述 `/ocr/jobs` 异步后缀，并说明 PaddleX 同步接口通常以
  `/layout-parsing` 结尾。
- 更新 README 和原 OCR 恢复设计规范，移除额外 `/api/v2/ocr/jobs` 包含规则。
- 运行前端格式检查、Lint 和生产构建。

## 阶段 3：全量验证与测试服务

- 运行完整 Pytest 和 Ruff。
- 停止当前临时服务进程，使用原临时数据目录和最新前端构建重新启动
  `0.0.0.0:8233`。
- 检查健康接口、设置 API 和 `/settings` 页面路由。
- 检查 Git diff、敏感信息和工作区状态。
