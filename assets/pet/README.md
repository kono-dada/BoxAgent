# 桌宠素材与协议

默认角色为 [Debug Duck v2](debug-duck-v2/pet.json)，原始图集在 [spritesheet.webp](debug-duck-v2/spritesheet.webp)。素材、状态协议与业务逻辑分别保存；业务代码不应根据角色名字决定语音或任务行为。

- [atlas-contract.json](atlas-contract.json)：角色无关的 Codex Pet Share 行列、帧数、Playground fps、循环与 16 方向定义。
- [debug-duck-v2/SOURCE.md](debug-duck-v2/SOURCE.md)：详细来源、状态映射建议、协议差异及未验证边界。
- [debug-duck-v2/LICENSE](debug-duck-v2/LICENSE)：上游 MIT 许可原文，随素材保留。
- [debug-duck-v2/validation.json](debug-duck-v2/validation.json)：实际下载文件尺寸、透明度、有效图格和 SHA-256 检查结果。

来源是 [portons/codex-pet-share](https://github.com/portons/codex-pet-share/tree/22725091da2787e8e525c9289cb7826a34be4950)，核查日期 2026-09-14。默认角色来自该仓库自带的完整测试包，没有重绘或修改图集。站点软件 MIT 许可不能推广为所有用户投稿的许可；本目录默认包随其仓库许可发布，已保留署名。

## 解耦方式

建议保持三层：语音/任务的语义状态 → 可替换的表现映射 → 图集渲染器。`listening`、`speaking` 等业务状态不写入原始 `pet.json`；本包本来没有这两种专门动画。更换兼容角色只需提供另一组 `pet.json` 与 `spritesheet.webp`，更换动画映射也不应影响任务状态机。

## 已确认的协议差异

- v1 为 8 × 9、1536 × 1872；v2 为 8 × 11、1536 × 2288，`spriteVersionNumber: 2`；单格 192 × 208。
- 站点当前 v2 含第 0 行第 6 列 neutral；idle 仍只有前 6 帧。16 个 look 方向在第 9、10 行，0 度向上，顺时针，每步 22.5 度。
- atlas 不携带 fps。本站画廊、Playground、本地 hatch-pet 技能分别有不同时间策略。本 POC 选用站点 Playground fps，不能宣称该 fps 是所有 Codex 客户端唯一标准。
- 本地 hatch-pet 技能把部分空格和 neutral 的说明写成另一种约定；渲染现有包时以当前站点源代码和实际图格为准，无需生成或升级该包。
- Debug Duck v2 本身存在方向行视觉差异：`running-right` 的鸭嘴朝左，`running-left` 朝右。角色配置可对调左右移动的动画选择；不要改动通用状态名称或原图。

本次站点主页和 API 的直接连接超时；源码、素材与许可证已从公开仓库取得并检查。没有宣称线上画廊已成功运行，当时的素材调研没有进行 BoxAgent UI 运行验收；后续宿主验收见 [验收记录](../../docs/poc-verification.md)。
