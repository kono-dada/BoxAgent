# 通用 VRM 桌宠展示

Electron 透明窗口中的 iframe 使用 Three.js / three-vrm 渲染 VRM。Electron 管理窗口、拖动、菜单和 React 小窗，Python Engine 继续处理语音和任务。动画代码不读取角色名称；所有本地模型使用同一状态控制器、动作池和骨架重定向路径。本轮实际渲染检查使用 Zome 和小企鹅。

## 使用

```sh
bash scripts/setup_vrm.sh
./启动桌宠.command
```

右键桌宠打开控制台，在「设置 → 3D 形象」中选择角色。新模型首帧成功后才保存选择，下次启动直接恢复该 3D 形象。没有保存选择，或旧配置选过内置／下载的 2D 形象时，使用仓库内的 Zome。产品只保留 VRM，旧图集和在线商店已移除。导入其他模型：

```sh
node scripts/import_vrm.mjs '/路径/角色.vrm' local-example '角色名称'
```

参数是模型、以 `local-` 开头的标识和可选显示名；默认引用仓库共享动作包。需要自定义动作时，可在标识前增加动作目录参数。导入前检查 VRM/VRMA 格式与 LFS 内容，相同素材和配置直接复用已有目录，不覆盖其他已存在的标识；全部复制成功后才发布形象目录。导入后重启即可在伙伴控制台「设置 → 3D 形象」中选择。

形象目录按模型内容、动作内容和渲染配置去重，优先展示仓库版本；仅名称相同不会合并。旧选择若指向等价本地副本，会恢复到仓库版本。不同模型或配置变体仍可独立选择。

团队共享模型位于 `assets/vrm/models/zome/`，128 个动作只保留一份，位于 `assets/vrm/motions/mate-engine/`。`.vrm` 和 `.vrma` 由 Git LFS 管理；清单、文件摘要与来源说明使用普通 Git。额外的个人模型仍可导入 `.runtime/pets/`，引用仓库动作包时不再复制动作。依赖由 pnpm 管理，构建输出 `assets/vrm/viewer.js` 不提交。安装后显示形象不依赖外网。

每位成员首次克隆后运行上面的设置脚本；更新资产后运行 `git lfs pull`。未下载的 LFS 指针会触发明确错误。当前选择、窗口位置、缓存和运行记录仍只保存在 `.runtime/`。资产更新应与相关清单一起提交，尽量由单人负责同一模型，避免二进制修改冲突。

## 与 Mate-Engine 对齐的控制逻辑

参考本机 Mate-Engine 的 `Assets/MATE ENGINE - Scripts/AvatarHandlers/`、`CustomVRM.prefab` 及 `Assets/MATE ENGINE - Animations/AvatarAnimatorController.controller`。

| 行为 | Mate-Engine 来源 | BoxAgent 实现 |
| --- | --- | --- |
| 待机轮换 | `AvatarAnimatorController.Update` 更新 IdleIndex；预制体实际覆盖为 10 秒切换、1 秒混合 | `mate-controller.js` 每 10 秒自主待机时间按耗能抽样，动作循环播放；交互期间暂停抽样，待机切换统一 1 秒混合 |
| 按下和松开 | 按下设置 isDragging，最短保持 0.30 秒，松开后退出 | 实际移动时发送 Drag 开始，拖动后的 `mouseUp` 发送结束；单击只展开或收起对话 |
| Drag 状态 | 控制器引用 `PET_DRAGGING.anim`，以 0.25 秒过渡进入/退出 | `motionSet.dragging` 对应 `PET_DRAGGING.vrma`，在 AnimationMixer 中循环播放 |
| 摆动 | `AvatarSwayController.Update` 先清理旧叠加，`LateUpdate` 在动画 hips 局部旋转后乘摆动 | 每帧恢复动画基准、更新 Mixer、保存新基准，再后乘 hips 摆动，防止逐帧累积 |
| 摆动参数 | 弹簧频率 2.6、阻尼 0.35、混合速度 8；默认四肢附加权重为 0 | 使用相同参数；原生速度换算为 60 Hz 鼠标位移，弹簧分子步积分以适应 WebKit 调度 |
| 视线 | `AvatarMouseTracking` 与预制体均允许 Idle/Drag 跟随 | 动画后叠加头部、脊柱、胸部和眼睛跟随；偏航/俯仰限制、平滑和混合权重按预制体配置 |
| 弹簧骨外力 | `AvatarGravityController`，预制体 impactMultiplier = 0.35 | 移动帧施加归一化窗口反向外力；停止移动即清零；VRM 0 叠加作者重力，VRM 1 按源码覆盖运行时重力 |
| 基础画面 | `Mate-Engine-Web/src/runtime/app/MateEngineWebApp.ts` 的 `configureScene` | 白色主光 2.4，位置 (2.5, 4.5, 4)；天空色 0xcfd8ff、地面色 0x2b2b2b、补光 2.1；透明背景与 PCF 软阴影 |
| 睡眠 | `AvatarSleepController` 默认关闭，开启后在允许状态累计时间，拖动唤醒 | 默认关闭；可通过 behaviorSettings 开启，默认计时 60 秒 |

上一版程序生成的头顶悬挂、整体上移、落地压缩及强制恢复直立姿势已移除。不会因为动作中躯干转角较大而拒绝加载。动作姿势由素材决定，物理只作附加层。

## 身体与脚部的自然运动

参考本机 `Mate-Engine-Web/src/runtime/avatar/animation/VrmAnimationLibrary.ts`，Web 版通过 `createVRMAnimationClip` 直接重定向 VRMA，并由 `AvatarAnimationStateController` 的 AnimationMixer 播放髋部位移与骨骼旋转。没有双脚锁定、双腿 IK 或限制髋部转角的求解器。`AvatarBreathingOverlayController` 还会给髋部和大腿叠加轻微呼吸、摆动及随机微调，脚随完整身体动作自然移动。

此前 BoxAgent 只播放旋转，丢弃了与腿部姿势配套的髋部位移。现在保留重定向后的髋部位置轨道，按模型比例换算；每帧恢复纯动画的位置与旋转，再播放和混合动作，最后叠加视线与拖拽摆动。已撤除试验性的双脚硬锁定和髋部可达范围限制，保留抬脚、转脚、脚趾运动和重心变化。窗口整体移动仍由原生宿主管理。

Web 版的 `SpringBoneGroundCollider` 只把平面碰撞器加入头发、衣物等弹簧骨，不负责给人形腿骨定位；`LowPolyIsland` 根据脚的位置调整岛面接触效果，`AvatarCameraController` 则跟随胸部高度。这些实现没有证明动作中脚部完全不滑动。BoxAgent 保留透明桌宠窗口和固定取景，没有加入岛屿或相机跟随。

两边使用的待机素材也不同：Web 版默认使用 `009_neutral_idle2.vrma`，BoxAgent 使用 Unity 导出动作包中的十一种待机；不能只按相同骨架播放器推断画面逐帧一致。当前调试信息记录实际模型双脚的位置与朝向，界面检查覆盖十一种待机、拖动与释放，并确认脚能随动作移动。

动作范围限定为本地 `mate_engine_motions` 目录的 128 个 VRMA，全部登记在仓库共享动作包中；启动只预载自动状态需要的动作，其余保留为按需加载资源。完整清单见 [本地动作目录](vrm-motion-catalog.md)。表情、舞蹈、坐姿等素材不会混入自动待机。

待机候选取原版控制器 Idle 混合树中预制体允许的前 11 项：基础待机、4 个 UPDATE2 待机、PET_IDLE_2、14、4、3、5、15。基础待机按 0.3 倍，14 和 15 按 0.4 倍，其余按各自配置播放。睡眠动作按原版 0.6 倍；没有统一将动作都调慢。每个动作由修复后的关节摆幅、角速度及髋部位移计算耗能，使用 `p ∝ exp(−E / T)` 抽样，默认 `T=0.8`。启动选最低耗能动作；允许连续抽到同一个动作，保持连续播放。拖拽、说话等交互状态暂停抽样计时，恢复先前动作；待机之间统一按 1 秒过渡并保留归一化动作相位。全部 39 个待机类别素材的分数、11 个自动候选的概率与计算方法见 [待机动作耗能与抽样](vrm-idle-energy.md)。说话和任务成功是 BoxAgent 的业务状态适配，Drag 优先于它们。

`scripts/sync_mate_motion_profile.mjs` 从实际动作文件、Unity .meta 的 GUID、控制器与预制体提取清单、引用、顺序和速度，生成 `web/vrm/mate-motion-profile.json`，包含源控制器和动作文件摘要。导入器以实际存在的文件与该配置的交集生成运行清单。配置复核命令：

```sh
node scripts/sync_mate_motion_profile.mjs '/路径/mate_engine_motions' '/路径/Mate-Engine'
```

## 动作坐标与头发修复

本地动作包标记为 `MateEngineAnimToVrma (Unity Editor)`。该包把父节点索引写在子节点的 `children` 中；颈部与上身断开，颈部轨道保存的是世界旋转。直接将它作为标准局部旋转接到目标人形骨架，会再次叠加上身旋转，使头部过度转动，并带起零重力长发。

`mate-animation-compat.js` 仅在生成器标记和反向层级结构同时匹配时，将断开骨骼的世界旋转转换成相对预期父骨骼的局部旋转。使用动作每一帧的父级旋转求逆，不减去固定角度，不按角色名称分支，也不修改原素材。标准 VRMA 跳过该兼容处理。

Zome 原有头发重力 0、刚度 0.6、阻尼 0.3 与碰撞配置保留。试验性的长发重力补偿已移除。每次渲染最多以 1/60 秒子步更新动画与弹簧骨，载入模型转向后重置弹簧历史，防止初始化朝向变换成为一次甩动。

## 结构和适配范围

- `web/vrm/mate-controller.js`：状态优先级、定时、拖拽保持、摆动数值。
- `web/vrm/behavior.js`：BoxAgent 业务状态、眨眼与视线输入。
- `web/vrm/motion-bank.js`：加载共享动作并重定向至标准人形骨架。
- `web/vrm/avatar-animator.js`：循环播放、状态过渡、每帧清理与添加骨骼叠加。
- `web/vrm/mate-tracking.js`：按 Mate 参数分层跟随屏幕指针。
- `web/vrm/spring-physics.js`：短时窗口外力与作者物理配置恢复。
- `web/vrm/mate-animation-compat.js`：旧导出包断开骨骼的坐标兼容。
- `web/vrm/expressions.js`：优先标准 VRM 表情，缺失时按常见 morph 名称适配。
- `boxagent/interfaces/desktop/pets/vrm.py`：受限本机资源 HTTP 服务。
- `web/panel/desktop/host.cjs`、`src/companion/pet.tsx`：透明窗口、两阶段换装、边界裁剪、拖动与 renderer 消息。

这不是 Unity Mecanim 的运行时移植。Unity 原版播放 `.anim`，当前宿主播放已有 `.vrma`；当前动作库使用骨骼旋转与髋部位置轨道，表情和视线轨道没有直接交给 Mixer。窗口控制全局移动，表情和视线由各自适配层控制。尚未完整复现 Unity 的根运动/人体求解、分层表情、手部 IK、窗沿坐姿与音乐驱动舞蹈，不能据此声称画面逐帧等同于原版。

加载器采用 VRM 0.x/1.0 人形接口；实际验收的企鹅和 Zome 均为 VRM 0.x。非人形 GLB、任意 FBX、缺失必要骨架的模型不在兼容范围内。企鹅使用 morph 表情，Zome 优先使用标准 VRM 表情。缺失可选骨骼和表情时跳过相应通道。

`pet.json` 的 `motionPack` 指向仓库共享动作包；`model`、`actions`、`motionSet` 指定模型和语义动作；`motionSettings` 保存各动作播放速度、循环与相位设置；`behaviorSettings` 可覆盖统一控制参数；`physicsSettings.impactMultiplier` 和 `trackingSettings` 可覆盖物理与视线参数；`hiddenMeshes` 控制可选部件；`expressionAliases` 补充表情名称。缺失动作时使用通用静态回退，缺失 Drag 不能视为已播放提起动画。

## 资源与生命周期

VRM 窗口为 252 × 182 点，额外宽度是长发甩动的透明留白，竖直方向保留动作边界余量；切回窄版形象时保持底部中心和输入草稿，平时目标 30 fps，Drag 状态目标 60 fps。像素倍率上限为 2，不可见时暂停更新。口型由说话状态驱动，尚未做真实音频音素同步。

本地资源服务只监听回环地址，使用随机路径令牌和精确白名单。WebKit 使用非持久数据存储。3D 容器仅包含 WebKit，没有 2D 占位对象。加载期间保持透明并允许鼠标穿透，首个 3D 渲染帧完成后显示模型并恢复交互。模型失败、超时或渲染进程退出时保持透明，并通过对话窗口提示具体原因；资源在启动前检查失败时报告错误，不切换成其他渲染类型。换装失败保留原形象，成功后才保存；切换和退出时释放 WebKit 与本地服务。

## 验证

```sh
pnpm --dir web/vrm test
uv run --script scripts/pet.py --check
BOXAGENT_VRM_CONTROLS_ONLY=1 uv run --script scripts/pet.py --check-vrm-ui
BOXAGENT_VRM_CONTROLS_ONLY=1 BOXAGENT_VRM_PET_DIR=assets/vrm/models/zome \
  uv run --script scripts/pet.py --check-vrm-ui
```

单元检查覆盖 10 秒待机抽样、交互中暂停计时、能量对摆幅与速度的响应、Boltzmann 概率及十万次固定种子抽样、按下即进入 Drag、短按 0.30 秒保持、持续移动不重置保持时间、Drag 优先级和退出恢复。骨架测试检查上身/手臂姿态保留、髋部位移和脚部旋转按轨道求值、脚能随动作移动，以及连续数百帧后的摆动和位置不累积。

控制模式的真实 WebKit 检查透明像素、帧推进、说话画面变化、原生按下消息后 Drag 动作开始计时、释放后恢复待机、菜单换装、草稿与位置保留和资源释放。控制模式报告位于 `.runtime/vrm-control-check/zome/report.json`。语音与任务使用本地替身，此检查不是人工鼠标操作验收或与 Unity 原版的视觉一致性测试。

完整截图检查不带 `BOXAGENT_VRM_CONTROLS_ONLY` 运行，涵盖十一种待机及每种实际播放速度、左右持续拖动、提住静止、释放和恢复。最新报告位于 `.runtime/vrm-check/<形象标识>/report.json`。Zome 的 `.runtime/vrm-check/zome/avatar-motion.gif` 是真实 WebKit 渲染的预览；输入来自检查程序发送的原生交互消息。

2026-10-11 撤除硬锁脚后的前端检查共 27 项，通过髋部位移、脚部旋转和跨帧恢复验证。Zome 和小企鹅各通过 69 项实际 WebKit 界面检查，记录了双脚随动作变化的位置与朝向，并检查动作速度、画面边界、透明渲染、原生交互与换装释放；语音和任务仍使用本地替身。此前双脚完全固定的数值检查不能作为自然动作的验收依据。128 个动作此前逐文件校验值与导出清单一致，本次没有修改二进制素材。

动作目前尚未与 Unity 运行结果逐帧对照，AnimationMixer 的混合不是完整 Mecanim 实现。视觉参数直接参考同为 Three.js 的 Web 版，但保留桌宠的透明窗口与固定取景，没有加入 Web 版岛屿和相机跟随。原版完整表情动画层尚未移植，眨眼与口型仍由 BoxAgent 的适配层驱动；清单中存在一个表情资源，不代表其对应的 Unity 状态已经接入。

团队仓库通过 LFS 保存 Zome 与共享动作包。资源来源、模型元数据与原始许可见 `assets/vrm/licenses/`，各素材原有授权条件保持不变。

## 控制台角色预览主题

控制台复用同一播放器，以 `previewTheme=light|dark` 指定预览背景，并通过父窗口主题消息同步变化；深色预览使用控制台同色底，模型无需重载。未带预览参数的 Electron 桌宠保持透明背景。预览页面处理明暗枚举；桌宠 iframe 仅接受来自父窗口的 present / interact / dispose 消息。形象应用由 Electron Host 调用 stdio 资源服务，收到 ready 后提交。

## 桌宠大小与窗口边界

伙伴控制台「设置 → 3D 形象 → 桌宠大小」支持 50%～600% 连续调整和恢复默认。偏好保存在当前数据目录的 `appearance.json`，重启和换装保留；实际尺寸受屏幕可见范围限制。前端通过 `desktop_set_pet_scale` 调用 Host，复用本机缩放与持久化逻辑。WebKit 返回角色可见边界，原生窗口裁掉透明空白，渲染视口保持原比例；聊天框使用稳定锚点，不随动画裁剪漂移。
