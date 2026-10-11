# 第三方资源来源

本目录保留团队协作使用的第三方素材和实现参考信息，不变更原作者的授权条件。

- `models/zome/model.vrm`：来自 Mate-Engine 的 `Assets/MATE ENGINE - Avatar/Zome.vrm`，作者 Yorshka，模型版本 1.3。原始 VRM 元数据与文件摘要见 `models/zome/source.json`。Mate-Engine README 将默认角色标为 Yorshka Shop 保留所有权利，并要求不随构建再分发。
- `motions/mate-engine/`：来自 `mate_engine_motions` 的完整 128 个 VRMA，导出器标记为 `MateEngineAnimToVrma (Unity Editor)`。原项目将动画等资产署名为 Shiny。逐文件大小和 SHA-256 见动作包的 `manifest.json`。
- `web/vrm/` 的状态、摆动、视线与弹簧骨外力实现参考 Mate-Engine 的 AvatarHandlers 和 CustomVRM 预制体；动作清单与速度来自其 Animator 控制器。具体对应关系见 `docs/vrm.md`。
- 灯光参数参考 Mate-Engine-Web 的 `src/runtime/app/MateEngineWebApp.ts` 中 `configureScene`。

Mate-Engine 的原始版权与完整许可文本保留在 `MateEngine-LICENSE.md`。资源进入内部仓库不表示其授权发生变化；后续对外提供构建时需单独处理这些第三方内容。
