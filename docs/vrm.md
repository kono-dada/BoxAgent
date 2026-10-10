# 通用 VRM 桌宠展示

透明 WKWebView 和 Three.js / three-vrm 在现有 macOS 窗口中渲染 VRM。窗口、菜单、聊天、语音和任务仍由原生宿主管理。动画代码不读取角色名称；所有本地模型使用同一状态控制器、动作池和骨架重定向路径。本轮实际渲染检查使用 Zome。

## 使用

```sh
bash scripts/setup_vrm.sh
./启动桌宠.command
```

右键桌宠，在「本地 VRM 形象」中选择角色。新模型首帧成功后才保存选择，下次启动直接恢复该 3D 形象。没有保存选择时默认使用仓库内的 Zome；用户明确选择过 2D 时保留该选择。「形象商店」可以换回小鸭。导入其他模型：

```sh
node scripts/import_vrm.mjs '/路径/角色.vrm' \
  assets/vrm/motions/mate-engine local-example '角色名称'
```

四个参数是模型、共享 VRMA 动作包、以 `local-` 开头的标识、显示名。导入后重启即可在菜单中选择。企鹅快捷脚本仅提供素材路径与隐藏面具的资源配置，不定制动作。

团队共享模型位于 `assets/vrm/models/zome/`，128 个动作只保留一份，位于 `assets/vrm/motions/mate-engine/`。`.vrm` 和 `.vrma` 由 Git LFS 管理；清单、文件摘要与来源说明使用普通 Git。额外的个人模型仍可导入 `.runtime/pets/`，引用仓库动作包时不再复制动作。依赖由 pnpm 管理，构建输出 `assets/vrm/viewer.js` 不提交。安装后显示形象不依赖外网。

每位成员首次克隆后运行上面的设置脚本；更新资产后运行 `git lfs pull`。未下载的 LFS 指针会触发明确错误。当前选择、窗口位置、缓存和运行记录仍只保存在 `.runtime/`。资产更新应与相关清单一起提交，尽量由单人负责同一模型，避免二进制修改冲突。

## 与 Mate-Engine 对齐的控制逻辑

参考本机 Mate-Engine 的 `Assets/MATE ENGINE - Scripts/AvatarHandlers/`、`CustomVRM.prefab` 及 `Assets/MATE ENGINE - Animations/AvatarAnimatorController.controller`。

| 行为 | Mate-Engine 来源 | BoxAgent 实现 |
| --- | --- | --- |
| 待机轮换 | `AvatarAnimatorController.Update` 更新 IdleIndex；预制体实际覆盖为 10 秒切换、1 秒混合 | `mate-controller.js` 按索引轮换共享待机，动作循环播放；拖拽期间索引也继续推进 |
| 按下和松开 | 按下设置 isDragging，最短保持 0.30 秒，松开后退出 | 原生 `mouseDown` 立即发送开始，`mouseUp` 发送结束；短按也保留最短 Drag 时间 |
| Drag 状态 | 控制器引用 `PET_DRAGGING.anim`，以 0.25 秒过渡进入/退出 | `motionSet.dragging` 对应 `PET_DRAGGING.vrma`，在 AnimationMixer 中循环播放 |
| 摆动 | `AvatarSwayController.Update` 先清理旧叠加，`LateUpdate` 在动画 hips 局部旋转后乘摆动 | 每帧恢复动画基准、更新 Mixer、保存新基准，再后乘 hips 摆动，防止逐帧累积 |
| 摆动参数 | 弹簧频率 2.6、阻尼 0.35、混合速度 8；默认四肢附加权重为 0 | 使用相同参数；原生速度换算为 60 Hz 鼠标位移，弹簧分子步积分以适应 WebKit 调度 |
| 视线 | `AvatarMouseTracking` 与预制体均允许 Idle/Drag 跟随 | 动画后叠加头部、脊柱、胸部和眼睛跟随；偏航/俯仰限制、平滑和混合权重按预制体配置 |
| 弹簧骨外力 | `AvatarGravityController`，预制体 impactMultiplier = 0.35 | 移动帧施加归一化窗口反向外力；停止移动即清零；VRM 0 叠加作者重力，VRM 1 按源码覆盖运行时重力 |
| 基础画面 | `Mate-Engine-Web/src/runtime/app/MateEngineWebApp.ts` 的 `configureScene` | 白色主光 2.4，位置 (2.5, 4.5, 4)；天空色 0xcfd8ff、地面色 0x2b2b2b、补光 2.1；透明背景与 PCF 软阴影 |
| 睡眠 | `AvatarSleepController` 默认关闭，开启后在允许状态累计时间，拖动唤醒 | 默认关闭；可通过 behaviorSettings 开启，默认计时 60 秒 |

上一版程序生成的头顶悬挂、整体上移、落地压缩及强制恢复直立姿势已移除。不会因为动作中躯干转角较大而拒绝加载。动作姿势由素材决定，物理只作附加层。

动作范围限定为本地 `mate_engine_motions` 目录的 128 个 VRMA，全部登记在仓库共享动作包中；启动只预载自动状态需要的动作，其余保留为按需加载资源。完整清单见 [本地动作目录](vrm-motion-catalog.md)。表情、舞蹈、坐姿等素材不会混入自动待机。

待机顺序取原版控制器 Idle 混合树中预制体允许的前 11 项：基础待机、4 个 UPDATE2 待机、PET_IDLE_2、14、4、3、5、15。基础待机按 0.3 倍，14 和 15 按 0.4 倍，其余按各自配置播放。睡眠动作按原版 0.6 倍；没有统一将动作都调慢。索引回到 0 时直接切换，其余按 1 秒过渡；待机切换起点保留归一化动作相位。说话和任务成功是 BoxAgent 的业务状态适配，Drag 优先于它们。

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
- `boxagent/interfaces/macos/pets/vrm.py`：本地服务、WebKit 消息和资源释放。

这不是 Unity Mecanim 的运行时移植。Unity 原版播放 `.anim`，当前宿主播放已有 `.vrma`；当前动作库只使用骨骼旋转轨道，位置、表情和视线轨道没有直接交给 Mixer。窗口控制全局移动，表情和视线由各自适配层控制。尚未完整复现 Unity 的根运动/人体求解、分层表情、手部 IK、窗沿坐姿与音乐驱动舞蹈，不能据此声称画面逐帧等同于原版。

加载器采用 VRM 0.x/1.0 人形接口；实际验收的企鹅和 Zome 均为 VRM 0.x。非人形 GLB、任意 FBX、缺失必要骨架的模型不在兼容范围内。企鹅使用 morph 表情，Zome 优先使用标准 VRM 表情。缺失可选骨骼和表情时跳过相应通道。

`pet.json` 的 `motionPack` 指向仓库共享动作包；`model`、`actions`、`motionSet` 指定模型和语义动作；`motionSettings` 保存各动作播放速度、循环与相位设置；`behaviorSettings` 可覆盖统一控制参数；`physicsSettings.impactMultiplier` 和 `trackingSettings` 可覆盖物理与视线参数；`hiddenMeshes` 控制可选部件；`expressionAliases` 补充表情名称。缺失动作时使用通用静态回退，缺失 Drag 不能视为已播放提起动画。

## 资源与生命周期

VRM 窗口为 252 × 182 点，额外宽度是长发甩动的透明留白，竖直方向保留动作边界余量；切回窄版形象时保持底部中心和输入草稿，平时目标 30 fps，Drag 状态目标 60 fps。像素倍率上限为 2，不可见时暂停更新。口型由说话状态驱动，尚未做真实音频音素同步。

本地资源服务只监听回环地址，使用随机路径令牌和精确白名单。WebKit 使用非持久数据存储。3D 容器仅包含 WebKit，没有 2D 占位对象。加载期间保持透明并允许鼠标穿透，首个 3D 渲染帧完成后显示模型并恢复交互。模型失败、超时或渲染进程退出时保持透明，并通过对话窗口提示具体原因；资源在启动前检查失败时报告错误，不静默切回小鸭。换装失败保留原形象，成功后才保存；切换和退出时释放 WebKit 与本地服务。

## 验证

```sh
pnpm --dir web/vrm test
uv run --script scripts/pet.py --check
BOXAGENT_VRM_CONTROLS_ONLY=1 uv run --script scripts/pet.py --check-vrm-ui
BOXAGENT_VRM_CONTROLS_ONLY=1 BOXAGENT_VRM_PET_DIR=assets/vrm/models/zome \
  uv run --script scripts/pet.py --check-vrm-ui
```

单元检查覆盖 10 秒待机轮换、拖动中的索引推进、按下即进入 Drag、短按 0.30 秒保持、持续移动不重置保持时间、Drag 优先级和退出恢复。骨架测试检查动画中的躯干/手臂姿态不被程序覆盖，以及连续数百帧后的摆动不累积。

控制模式的真实 WebKit 检查透明像素、帧推进、说话画面变化、原生按下消息后 Drag 动作开始计时、释放后恢复待机、菜单换装、草稿与位置保留和资源释放。控制模式报告位于 `.runtime/vrm-control-check/local-zome/report.json`。语音与任务使用本地替身，此检查不是人工鼠标操作验收或与 Unity 原版的视觉一致性测试。

完整截图检查不带 `BOXAGENT_VRM_CONTROLS_ONLY` 运行，涵盖十一种待机及每种实际播放速度、左右持续拖动、提住静止、释放和恢复。最新报告位于 `.runtime/vrm-check/<形象标识>/report.json`。Zome 的 `.runtime/vrm-check/local-zome/avatar-motion.gif` 是真实 WebKit 渲染的预览；输入来自检查程序发送的原生交互消息。

当前 20 项前端检查、244 项 Python 检查通过，覆盖共享动作包路由、目录边界、LFS 指针提示与仓库模型选择恢复。128 个动作逐文件校验值与导出清单一致。此前 Zome 原生检查记录了 46 项通过，包括十一项动作速度、灯光配置、首帧前无 2D、透明渲染与换装释放；窗口位置断言受到检查期间手动拖动影响，完整报告未标记为全项通过。本次资产入库未重新进行视觉验收。

动作目前尚未与 Unity 运行结果逐帧对照，AnimationMixer 的混合不是完整 Mecanim 实现。视觉参数直接参考同为 Three.js 的 Web 版，但保留桌宠的透明窗口与固定取景，没有加入 Web 版岛屿和相机跟随。原版完整表情动画层尚未移植，眨眼与口型仍由 BoxAgent 的适配层驱动；清单中存在一个表情资源，不代表其对应的 Unity 状态已经接入。

团队仓库通过 LFS 保存 Zome 与共享动作包。资源来源、模型元数据与原始许可见 `assets/vrm/licenses/`，各素材原有授权条件保持不变。
