# ComicLens 设置页圆角下拉组件实施计划

## 目标

按照已确认的 B 方案，用项目现有 Radix Select 替换设置页全部原生下拉框。关闭状态和展开浮层统一为大圆角矩形，箭头右侧与文字左侧均保留 16px 间距，并提供选中状态、键盘操作及轻量开关动画；所有设置数据和保存逻辑保持不变。

## 阶段 1：通用 Select UI 组件

涉及文件：

- 新增 `web/src/components/ui/select.tsx`
- 修改 `web/src/styles/globals.css`

实施内容：

- 使用 `radix-ui` 中的 Select primitives 组合受控组件。
- 组件接收当前字符串值、二元组选项、值变化回调、可选 className、禁用状态和无障碍名称。
- 触发器保持 44px 高度，使用 16px 圆角与 16px 水平内边距；Chevron 位于最右侧并在展开时旋转。
- 浮层使用 Portal 和 Popper 定位，宽度跟随触发器并在视口边缘自动避让。
- 选项提供高亮、当前项浅色背景、右侧勾选和禁用状态。
- 在全局样式中定义约 150ms 的开关动画、方向位移和 reduced-motion 兜底。

验证：

- TypeScript 能推导组件属性与 Radix 属性。
- 组件内部不使用业务字段或设置页状态。
- 左右边距由同一个 `px-4` 保证，不使用绝对定位猜测箭头位置。

## 阶段 2：替换设置页全部下拉

涉及文件：

- 修改 `web/src/routes/_app/settings.tsx`

实施内容：

- 导入通用 Select，删除设置页内的原生 Select helper。
- 将主题、默认阅读模式、翻页方向、源语言和翻译服务接入 `onValueChange`。
- 将 SecretField 的“保留 / 替换 / 清除”原生选择器替换为同一组件。
- 敏感操作下拉保留窄版布局和字段专属 `aria-label`；值变化时继续清空暂存明文。
- 不改动选项值、显示文字、条件渲染、draft 类型和提交载荷。

验证：

- `web/src` 中不再保留项目编写的原生 `<select>`。
- 条漫仍隐藏翻页方向；DeepL/DeepLX 切换仍显示对应敏感字段。
- 所有敏感字段操作下拉都由共享 SecretField 自动获得新组件。

## 阶段 3：格式、静态检查与服务验证

执行：

- `npm run fmt`
- `npm run fmt:check`
- `npm run lint`
- `npm run build`
- `rg -n "<select" web/src`
- `git diff --check`

审查：

- 浅色与深色均只使用 ComicLens 语义主题色。
- Radix Content 的 z-index 足以覆盖设置卡片，且不会影响阅读器菜单。
- 打开、关闭、当前项、键盘高亮及 reduced-motion 样式完整。
- 设置页没有数据模型、API 或保存行为差异。

交付：

- 将实现作为独立提交，提交者保持 `little6neko <little6carbon@163.com>`。
- 确认无正在运行的后台翻译任务后重启 `comiclens.service`。
- 验证服务继续监听 `0.0.0.0:8233`、健康检查及设置页 SPA 入口正常。
