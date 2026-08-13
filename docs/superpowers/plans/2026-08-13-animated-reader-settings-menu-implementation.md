# 动画阅读设置菜单实施计划

1. 在 `ReaderBottomBar` 中用 Radix DropdownMenu 包裹“阅读设置”触发按钮，使弹窗以该按钮为定位锚点，并加入打开/关闭动画。
2. 将阅读模式选项迁移到弹出菜单，名称统一为“条漫 / 单页 / 双页”，仅在非条漫模式渲染翻页方向。
3. 从 `ReaderSettingsPanel` 删除旧的全屏 Dialog 设置面板，保留章节目录 Dialog。
4. 调整阅读器路由的属性传递，继续使用现有服务器保存 mutation 和 `heldOpen` 控制栏保持逻辑。
5. 运行前端格式、Lint、TypeScript/生产构建及后端回归测试。
6. 重启 ComicLens，在真实章节中验证定位、开关动画、模式可见性、服务器持久化、外部点击/Escape 关闭和底部按钮顺序。
7. 提交实现并推送到 SSH 远端 `main`。
