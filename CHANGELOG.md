# 更新日志

## v1.6.0 (2026-11-22)

- **P0 升级：Agent 控制产品化**。内置 MCP Bridge（`agent_bridge.py`），GUI 的「Agent 桥接」开关以 `--agent-bridge` 模式拉起 stdio JSON-RPC Server，暴露 15 个工具
- 新增结构化 `snapshot`：返回控件文字 / 资源 ID / 中心坐标 / 可点击状态，可选在截图上画编号；`click_by_selector` 支持按 text/resourceId/className/description 定位
- 新增人在回路：敏感操作（密码/验证码/支付/权限）弹审批框，GUI 可一键暂停/恢复 Agent，所有工具调用写入审计日志并实时展示
- 新增 `shell` 白名单与危险命令拦截；`switch_transport` 支持有线/Wi-Fi 互切（需批准）
- 中文输入支持 ADBKeyboard（可选放 `tools/ADBKeyboard.apk`），英文用转义后的 `input text`
- GUI 连接后写 `device_state.json`，Bridge 复用同一条 adb 通道，不重复配对
- 修复 Windows 下 MCP stdio 输出被本地 GBK 编码导致客户端 UnicodeDecodeError 的问题

## v1.5.0 (2026-11-22)

- 新增 GUI 按钮控制手机亮屏 / 黑屏（只关显示面板、不锁屏）
- 连接时不再改动手机音量与静音状态；测试音保留且原样恢复
- 新增快捷键贴图「跟随」开关；贴图标题栏新增置顶按钮
- 修复锁屏 / 密码界面不可见、不可点击：默认保持亮屏并启用 `--keep-active`
- 横竖屏切换时按窗口旧中心点重定位，避免窗口跳太远
- 修复快捷键贴图无法启动（exec 字符串缩进）与按钮误杀自身进程的问题
- 带宽实测改用 `adb pull ... nul`（此前 `exec-out dd` 严重低估带宽）

## v1.4.0 (2026-11-22)

- 每次连接按实测带宽的 80% 决定视频码率，帧率固定 60 fps
- 修正带宽测量方法：USB 实测约 340 Mbps → 取 269 Mbps

## v1.3.0 (2026-11-21)

- 新增 USB 有线投屏，支持自动 / 有线 / 无线三档
- 新增 USB 设备实时状态提示与巡检 watchdog
- 修复「配对成功但设备不出现」：配对后必须再 `adb connect`

## v1.2.0 (2026-11-21)

- 新增二维码扫码配对（IP / 端口分离输入的手动配对也保留）

## v1.0.0 (2026-11-16)

- 首个可交付版本：无线 ADB 发现与配对、scrcpy 投屏、快捷键贴图、测试音
