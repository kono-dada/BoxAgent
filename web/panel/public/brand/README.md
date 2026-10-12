# BoxAgent 图标

原创可编辑矢量图：白色圆角底、奶油色圆脸小猫、暖纸盒。用户指定“纸盒里探出头的小猫/精灵”，后续明确要求简洁、不要薄荷底和额外装饰，同时保留亲和表情与搭在盒沿的小爪子。MiraJelly 图标仅作风格参考，未复制图片。

`boxagent-icon.svg` 为源图；`boxagent-icon.png` / 512、256、128 尺寸 PNG 与 `boxagent.icns` 用于产品身份和控制台。`trayTemplate.svg` 为独立单色简化轮廓，PNG 1x/2x 用于 macOS 菜单栏。

在 `web/panel` 执行 `pnpm exec electron scripts/render-icons.cjs` 离线导出，ICNS 使用 macOS 自带 sips / iconutil。当前图标由矢量代码创作，无 AI 生图 API 调用。
