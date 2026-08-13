# ComicLens 设置页下拉滚动与 Switch 对比度修复实施计划

## 目标

将共享设置下拉从会强制锁定页面的 Radix Select 改为 Radix DropdownMenu 非模态单选菜单，使桌面与手机在菜单打开时仍可滚动且浮层跟随触发器；同时将浮层动画收敛为纯纵向淡入，并增强 Switch 关闭轨道对比度。设置值与保存逻辑保持不变。

## 阶段 1：非模态单选下拉

涉及文件：

- 修改 `web/src/components/ui/select.tsx`

实施内容：

- 保持 `Select` 的 value、options、onValueChange、ariaLabel、className 和 disabled 接口不变。
- 使用受控 `DropdownMenu.Root modal={false}`、Trigger、Portal、Content、RadioGroup 与 RadioItem 替换 Radix Select primitives。
- 保留当前触发器尺寸、圆角、左右留白、箭头旋转、浮层等宽、选中背景与勾选样式。
- Content 使用触发器 CSS 变量控制宽度，并继续启用视口碰撞避让。
- 鼠标外部 pointerdown 沿用 Radix 关闭；触摸外部 pointerdown 阻止自动关闭，再由外部 click 区分轻点和滚动。
- 只在菜单打开期间注册外部 click 监听，使用 trigger/content refs 排除组件内部目标，并在关闭时清理。
- 选择 RadioItem 时调用原有回调并让菜单正常关闭；保留键盘导航和关闭后的焦点恢复。

验证：

- 打开菜单时 body 不出现 `overflow: hidden`、滚动条补偿或外部指针锁定。
- 普通与窄版组件仍保持 16px 圆角和与触发器等宽。
- 选项值与设置页调用方无需修改。

## 阶段 2：纯纵向动画与 Switch 关闭态

涉及文件：

- 修改 `web/src/styles/globals.css`
- 修改 `web/src/components/ui/switch.tsx`

实施内容：

- 下拉打开动画改为 `opacity: 0; translateY(-6px)` 到正常位置。
- 关闭动画反向执行，不包含 scale、translateX 或按 data-side 改变方向的变量。
- 保留约 150ms 时长、箭头旋转和 reduced-motion 完全禁用动画的规则。
- Switch 未选中轨道改为 `muted-foreground` 半透明背景；选中轨道、滑块、尺寸、位移及焦点行为不变。

验证：

- 动画源码和浏览器计算样式中没有 scale 或横向位移。
- 上方与下方浮层都从正上方向正下方进入。
- Switch 深浅主题关闭态均能看出完整轨道，开启态仍为 primary。

## 阶段 3：工程与真实浏览器回归

执行：

- `npm run fmt`
- `npm run fmt:check`
- `npm run lint`
- `npm run build`
- `git diff --check`

桌面浏览器验证：

- 记录菜单打开前后 `innerWidth`、`documentElement.clientWidth`、body overflow、底部导航矩形与滚动位置。
- 打开菜单并发送真实滚轮事件；确认页面滚动、菜单保持打开、浮层与触发器相对位置保持一致。
- 验证鼠标外部点击、选择、Escape、方向键及焦点返回。

手机浏览器验证：

- 使用移动视口与真实 touch/pointer 序列滚动页面；确认菜单保持打开、页面滚动且浮层跟随。
- 使用无移动的外部触摸轻点，确认菜单关闭。
- 确认浮层视口碰撞避让和页面宽度稳定。

交付：

- 审查差异只涉及共享 Select、下拉动画与 Switch。
- 独立提交实现，提交者保持 `little6neko <little6carbon@163.com>`。
- 确认没有后台翻译任务后重启 `comiclens.service`。
- 验证健康检查、设置页与 `0.0.0.0:8233` 监听状态。
