# Debug Duck v2 来源与渲染契约

核查日期：2026-09-14。

本目录直接复用 Codex Pet Share 公开仓库自带的 Debug Duck v2。没有生成、重绘或修改原始角色图。它是黄色像素橡皮鸭，带深色键盘道具。

- 用户指定站点：[codex-pets.net](https://codex-pets.net/)。
- 站点关联公开仓库：[portons/codex-pet-share](https://github.com/portons/codex-pet-share)。
- 固定来源版本：`22725091da2787e8e525c9289cb7826a34be4950`。
- [原角色目录](https://github.com/portons/codex-pet-share/tree/22725091da2787e8e525c9289cb7826a34be4950/test-assets/pets/debug-duck-v2)。
- [包格式说明](https://github.com/portons/codex-pet-share/blob/22725091da2787e8e525c9289cb7826a34be4950/README.md#pet-package-format)。
- [帧布局与方向定义](https://github.com/portons/codex-pet-share/blob/22725091da2787e8e525c9289cb7826a34be4950/src/domain/config.ts)。
- [Playground 动画时序](https://github.com/portons/codex-pet-share/blob/22725091da2787e8e525c9289cb7826a34be4950/src/playground/core/config.ts)。

## 包格式

运行所需文件只有 `pet.json` 与 `spritesheet.webp`。原 manifest 保持英文及原样，便于与上游比较。`spritesheetPath` 是相对包目录路径；缺省版本属于 v1，本包明确写有 `spriteVersionNumber: 2`。

| 格式 | 尺寸 | 布局 |
| --- | --- | --- |
| v1 | 1536 × 1872 | 8 列、9 行 |
| v2 | 1536 × 2288 | 8 列、11 行 |

单格均为 192 × 208 像素，行列从 0 开始，由图片左上角向右、向下计数。裁剪矩形为 `(列 × 192, 行 × 208, 192, 208)`。不要把整个 WebP 当作自带动画时间轴的动态图片播放；这是静态精灵图集。

## 状态及本 POC 可采用的时序

以下 fps 与循环规则来自上游 Playground，适合作为 POC 的显式播放约定。

| 状态 | 行 | 有效帧数 | fps | 循环 | 原始含义 |
| --- | --- | --- | --- | --- | --- |
| idle | 0 | 6 | 6 | 是 | 安静待机 |
| running-right | 1 | 8 | 12 | 是 | 向右移动 |
| running-left | 2 | 8 | 12 | 是 | 向左移动 |
| waving | 3 | 4 | 5 | 否 | 招手 |
| jumping | 4 | 5 | 14 | 否 | 跳跃 |
| failed | 5 | 8 | 6 | 否 | 失败反应 |
| waiting | 6 | 6 | 4 | 是 | 等待用户 |
| running | 7 | 6 | 12 | 是 | 正在工作 |
| review | 8 | 6 | 6 | 是 | 专注查看 |

每行只播放所列帧数，其余格子不属于该循环。v2 第 0 行第 6 列是独立 neutral look，不参与 idle 的 6 帧循环；第 7 列为空。

图集格式并没有统一编码 fps。上游画廊预览采用整轮 `max(帧数 × 260, 1400)` 毫秒，上游编辑器预览又采用 8 fps。本机 hatch-pet 技能使用另一套逐帧时长，并把 neutral 描述为默认 idle 回退。它们不能同时称为唯一标准。本 POC 应保持行列契约，并明确自己选用的播放时序；neutral 以实际包内容为准。

## 16 个注视方向

方向从朝上开始顺时针，每步 22.5 度。索引 `i` 的位置为 `row = 9 + floor(i / 8)`、`column = i % 8`。

| 索引 | 0 | 4 | 8 | 12 |
| --- | --- | --- | --- | --- |
| 方向 | 上 | 右 | 下 | 左 |
| 图格 | 9,0 | 9,4 | 10,0 | 10,4 |

这些是静态方向姿势，不应作为普通 16 帧循环持续播放。鼠标落在角色中心的死区时可以显示 neutral，或恢复 idle。只有空闲时看鼠标即可，正在听说或执行任务时由业务状态控制表现。该优先级是 BoxAgent 的建议，不是站点协议。

## BoxAgent 交互映射建议

| BoxAgent 状况 | 图集状态 | 说明 |
| --- | --- | --- |
| 未开启语音、空闲 | idle | 默认小动作 |
| 开启语音 | waving 一次 | 随后进入当前稳定状态 |
| 听用户说话 | review | 图集没有专门 listening 动画，靠字幕和麦克风指示明确状态 |
| 正在回复 | waving 或 idle | 图集没有唇形或 speaking 行，避免宣称口型同步 |
| 后台任务运行 | running | 语音交互可以暂时覆盖视觉；任务仍有独立状态 |
| 需要用户补充 | waiting | 不应把所有后台等待都渲染为需要用户 |
| 已验证完成 | jumping 一次 | 然后回到仍有效的状态 |
| 执行失败 | failed 一次 | 同时展示具体失败原因 |
| 左右拖动 | 方向行 | 本包方向素材的视觉差异见下一节 |

## 核验与限制

- 已用实际下载文件检查尺寸、RGBA 透明度、9 行有效帧、neutral 与全部 16 个 look 图格非空，以及其他格子透明。结果见 `validation.json`。
- 下载文件与固定来源 Git checkout 中的 WebP 字节一致；上游另有较完整的生成 QA，可从角色目录的 `qa/` 查阅。这些历史 QA 不等同于 BoxAgent 运行验收。
- 已目视查看上游 contact sheet：同一黄色像素鸭贯穿各行，具有标准状态及 16 个注视方向。
- 本包 row 1（命名 running-right）的鸭嘴朝屏幕左侧，row 2 朝右侧。上游命名和素材视觉朝向相反。保持图集原样；若启用拖动朝向，可在本角色配置中对调选择行，或暂不展示方向移动动画。
- 本次机器对站点主页/API 的直接请求超时，因此没有实时验证网站的完整角色目录、下载按钮或当前线上版本。素材来自其可读取的公开仓库，不能表述为刚从在线画廊成功下载。

## 许可

已原样保留上游 `LICENSE`：MIT，署名为 `Copyright (c) 2026 Codex Pet Share contributors`。本包是上游仓库直接提交的测试角色资源；未找到该目录单独的许可证或排除条款，因此按随仓库发布的许可保留来源和许可文本。

此结论不应推广为“codex-pets.net 上所有用户上传素材都采用 MIT”。更换其他角色时应保留其作者和具体素材许可；站点软件的开源许可不能自动覆盖所有投稿者的作品。
