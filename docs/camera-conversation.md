# 摄像头视觉对话

## 这一版的范围

使用现有的 Qwen Omni 实时会话，把摄像头画面与麦克风语音交给同一个模型。
模型回答继续经过现有对话、语音播放、字幕和桌宠展示链路。
不再使用 MediaPipe、挥手阈值、固定招呼句或浏览器本地 TTS。

这是用户用语音发起话轮的视频对话，不是看到动作就自动开口的主动策略。
文字聊天仍保留原有行为；本版视觉提问请使用视频通话中的语音，图像与音频由 VAD 一起提交。
不进行身份识别，不根据表情给用户贴心理或情绪标签，也不自动写长期记忆。

## 使用

1. 按 `docs/setup.md` 配置桌宠，保持 `.env.local` 和密钥不入 Git。
2. 确保语音模型为支持视觉的 Qwen Omni，例如当前使用的 `qwen3.8-omni-flash-realtime`。
3. 构建前端并启动桌宠：`./scripts/run-pet.sh`。
4. 在聊天框点击“开启摄像头”开关，会直接打开摄像头窗口并开始视频通话，无需第二次点击。也可从控制页面或菜单栏打开“摄像头互动”，此入口只打开窗口，点击“开启视频”后才采集。
5. 授予摄像头和麦克风权限。出现“通话中”后，说“你现在看到什么？”或拿起一个物品询问。AOQ 收到本轮语音后才发送画面，不需要等画面发送才开口。
6. 再次点击聊天框的“关闭摄像头”开关，会停止采集并关闭预览窗口。也可在预览窗口内停止或直接关闭窗口，聊天框开关会同步复位。若开启摄像头前已在语音通话，该连接保持不变；若语音是随视频窗口新开的，则一起结束。

推荐戴耳机测试，避免扬声器声音回到麦克风。不要只挥手而一直保持沉默，本版没有手势触发器。
API 会产生音视频用量；画面进入千问云端，不是本地 VLM 推理。

### 视频小窗

默认尺寸为 400×480，可缩至 320×360。预览保留完整画面比例，不裁切人物。
Mac 顶部使用隐藏式原生标题栏：关闭、最小化、全屏按钮融入深色区域，计时居中，右侧可置顶，空白处可拖动。
通话状态以小标签显示在预览上方；底部是摄像头选择、开启／关闭视频和镜像预览。
镜像只改变本地显示，不改变上传图像。摄像头选择在停止视频时可用，连接中的取消按钮保留。
视频窗口不再重复显示对话文字、模型名称、帧数、音量调试条或 API 用量说明；原聊天记录和日志仍保留。
移除这些说明不改变云端传输、计费、权限申请或 AOQ 音频处理。错误仍在预览区域内显示，不能遮住关闭按钮。

### 通话时长与异常关闭

应用没有设置“几分钟后自动关闭视频”的总时长计时器。
截至 2026-10-28，[千问官方说明](https://help.aliyun.com/zh/model-studio/realtime)
列出当前模型单次会话最长 120 分钟；240 秒视频历史上限是上下文保留范围，不是通话上限。
网络中断、语音会话结束、连续 8 秒没有新摄像头帧或发送失败仍会关闭采集，不在后台自动恢复摄像头。

本机当日 22:52 的日志出现 `Cannot cancel response before response.created`：
客户端发起新回复后仍保留上一轮回复 ID，用户此时插话会提前发送取消，前端又将这个取消竞态当成连接错误关掉摄像头。
22:54 的另一条日志是上一请求未确认，下一轮等待 8 秒后关闭；这不是视频总时长限制。
现在新请求清空旧 ID，待服务端确认后才取消已过期回复，并将上述明确的取消竞态降为诊断事件。
其他服务端错误仍展示，真正缺失确认仍有限时并关闭未知状态连接；`response.ack_timeout` 区分等待本轮还是前一轮。
单元测试重放了“旧回复结束、创建新回复、确认前插话、迟到确认、继续下一轮”的顺序。
离线测试不等于真实云端长通话稳定性保证，仍需后续实际通话观察。

### 独立开发配置

组员按 `docs/setup.md` 安装依赖，并在自己的 `.env.local` 中配置
`DASHSCOPE_API_KEY`、`BOXAGENT_DASHSCOPE_WORKSPACE_ID` 和 `BOXAGENT_DASHSCOPE_REGION`。
Key 和 Workspace 必须属于同一可访问的北京地域工作空间，并已获得所用实时模型的访问资格。
要明确验证 AOQ 链路，可设置 `BOXAGENT_QWEN_TRANSPORT=aoq`，然后运行 `./scripts/run-pet.sh`。
修改凭据后重启桌宠；不要提交真实密钥，也不要直接编辑指向其他工作区的共享配置符号链接。
开发者个人的 `.runtime/start-camera.command` 和账号文件不在仓库中，组员无需这些文件即可运行。
摄像头页面必须从桌宠打开，不要直接在浏览器打开源码 `web/panel/camera.html`。

### 连接超时

视频通话先建立同一条语音连接，所以 AOQ 启动慢会同时影响语音和摄像头。
2026-10-26 的本机日志曾记录语音约 31 秒才就绪，而原来的 Engine RPC 和交互层都只等待 20 秒。
现在交互层等待就绪最多 45 秒，语音相关桌面请求最多等待 60 秒（包含清理时间）；AOQ 网络握手本身最多 30 秒，不因持续收到音频统计而无限续期。
启动失败会清理旧连接，避免界面报错后麦克风会话又在后台接通。已建立通话的自动重连仍保留。
摄像头启动响应携带状态版本号；页面忽略该版本之前的事件，避免旧的停止通知延迟到达后误关新通话。权限令牌仍只保存在主进程。
`logs/runtime/qwen/events.jsonl` 中的 `transport.token_request`、`transport.token_received`、`transport.connecting`、`transport.connected` 可区分鉴权、网络连接和会话就绪阶段，不记录 Token。
这修复的是等待预算和失败清理，不保证外部网络每次都能连通，也不会自动切换成 WebSocket。

## 模块边界

```
CameraPage (getUserMedia, JPEG, 约 1 fps)
  -> CameraWindow (权限、受限 IPC、私有会话 token)
  -> EngineServer.camera_frame
  -> CameraInteractionService (时效、顺序、大小、限频、断线和关闭)
  -> CameraRuntime / CameraFeed 协议
  -> InteractionService (借用当前语音，或在无语音时新建连接)
  -> QwenRealtimeSession.feed_image
  -> WebSocket / AOQ Data Track: input_image_buffer.append
  -> 原有 ASR、话轮准备、回复、语音、对话记录
```

- 组合根是 `boxagent/bootstrap/engine.py`；领域层不导入千问或 Electron。
- 帧最多 640×480，JPEG 质量 0.7，Base64 不超过 256,000 字节；每秒最多一帧，最多一个发送请求在途。
- AOQ 保留原生麦克风、Opus 编解码、扬声器、AEC 和打断链路，只通过已有 Data 轨附加 `input_image_buffer.append`；不发布 Video 轨，不启动第二套麦克风，不额外发送音频 append、commit 或 response.create。
- AOQ 仅在服务端 `speech_started` 确认本轮音频到达后送图，`speech_stopped` / `committed` / `cleared` 关闭本轮图像入口。没有用定时器假定音频已经到达。WebSocket 保留原有音频与图像实现，只有显式选择才使用。
- 沉默时页面保持“通话中”，画面本地预览但不往 AOQ 上传；没有图像待发送队列。语音结束后由原有 VAD 提交，不会因为收到图片就主动生成回复。
- 一个摄像头 token 绑定一个具体语音连接。切换会话、关闭语音或断线后停止摄像头；不在后台自动恢复云端采集。
- 聊天框的一键开启意图由 Electron 主进程保存，摄像头页面就绪后仅消费一次。权限申请和语音连接期间可取消；拒绝权限、关闭或刷新窗口都会复位开关，不会在下次加载时自动继续采集。
- 超过 8 秒未收到新画面时停止摄像头；正常等待开口没有 10 秒超时。画面发送失败、窗口关闭或渲染器退出均不得关闭借用的语音。
- 借用语音的自动重连策略保持原样，但摄像头不跟随新连接自动恢复；随视频新开的语音断线后需用户重新开启，避免后台恢复采集。
- 原始图片只在内存和传输链路中使用，不加入事件快照、对话库或记忆库；千问 trace 的媒体字段会被脱敏。
- 对话文字仍按项目现有机制保存。云端数据处理适用你们百炼账号的服务条款，本模块不承诺云端零留存。
- 未修改后台任务、通知 Outbox、记忆策略或 P2F 研究代码。

## 检验与边界

```
uvx --from pyright==1.1.414 pyright --pythonpath .venv/bin/python
UV_CACHE_DIR=/tmp/uvcache uv run --no-project -- .venv/bin/python -m pytest -q
pnpm --dir web/panel test
pnpm --dir web/panel run build
cd web/panel
pnpm exec electron tests/camera-smoke.cjs
```

`camera-smoke.cjs` 使用 Chromium 合成摄像头和模拟千问传输，验证实际 UI、等待/发送状态、JPEG、Python 会话、回复通道、窄屏布局、关闭清理，以及借用语音在关闭摄像头后继续工作；不证明模型识别精度。

2026-10-15 另行调用真实千问 API，用合成图片和 macOS 合成语音验证：
- 手动提交红色图形，回复“红色”。
- 通过项目的 `QwenRealtimeSession`、VAD 和宿主话轮控制发送蓝色图形，回复“图片中间的图形是蓝色的。”，没有 API 错误。
- 上述真实测试使用 WebSocket，没有采集真人摄像头或麦克风；AOQ 的 Data Track 接口有离线协议测试，真人自然交互仍需实际体验评估。

2026-10-19 AOQ 增量验证：
- 保留生产 AOQ 音频编解码和 Audio/Data 轨配置，用 SDK 外部音频输入注入 macOS 合成语音，不打开物理麦克风或扬声器。
- 单轮纯语音取得真实 ASR 和回复；另一次带蓝色图形的真实 AOQ 会话取得 ASR、图像发送、回复“中间的图形是蓝色的。”及 `response.done`。图像没有进入 trace。
- 连续多轮探测仍出现不返回下一轮 ASR 的情况，尚不能区分测试音频注入和实际 AOQ 链路的问题；不能将单轮成功描述为多轮稳定验收。
- 随后新建 AOQ 会话被 Token 接口拒绝：HTTP 403，`AccessDenied.Unpurchased`，提示没有模型访问资格。停止云端重试，需要账号管理员确认北京地域、当前工作空间和 `qwen3.8-omni-flash-realtime` 的访问权限后继续。
- 离线回归覆盖借用语音、摄像头错误隔离、原有语音自动重连、逐话轮音频先于图像、沉默等待以及原有播放/打断协议；真实用户摄像头与麦克风的连续通话仍待验收。

2026-10-21 新账号增量验证：
- 北京地域的新 Key/Workspace 配对成功申请 AOQ Token，不再出现此前的 HTTP 403。
- 在同一个真实 AOQ 会话中，依次完成纯语音问好、蓝色图形识别、停止送图后的“三加五”问答。最终回复为“3加5等于8。”；三阶段图片发送数为 `0 / 6 / 0`。
- 测试仅注入合成语音与图片，保留生产音轨和 Opus 配置。探测器持续推送 PCM/静音，并显式静音 SDK 的设备采集，避免早期探测出现的回复回灌；这些测试用音频控制没有加入生产代码。
- 真正摄像头与真人麦克风的体验、扬声器回声以及长时间稳定性仍需人工验收，三轮合成测试不代表全部场景稳定。
- Electron 合成摄像头测试再次通过，覆盖发送画面、正常回复、关闭清理以及关闭摄像头后继续使用原有语音；新增回归固定 Audio/Data 轨、原有 Opus 参数与原生设备所有权。

它是低频画面辅助的视觉语音对话，不是高帧率动作识别；快速动作、遮挡、光线和模型延迟仍会影响效果。

## 语音排障

AOQ 测试曾出现画面持续发送、没有语音识别事件、60 秒后服务端超时的情况。
可用 `BOXAGENT_QWEN_TRANSPORT=aoq ./scripts/run-pet.sh` 显式使用 AOQ；摄像头本身不切换音频协议。
临时对照测试可用 `BOXAGENT_AOQ_OUTPUT_DEVICE='' BOXAGENT_QWEN_TRANSPORT=websocket ./scripts/run-pet.sh`；
空的输出设备变量关闭仅适用于 AOQ 的独立音响选项。
如代理影响连接，可同时设置 `BOXAGENT_QWEN_WEBSOCKET_PROXY=direct` 直连百炼工作空间端点。
`direct` 只作用于千问 WebSocket，不更改 macOS 系统代理或其他服务的代理。

视频小窗不展示麦克风音量调试条。WebSocket 的输入音量状态仍来自实际发送的 PCM 数据幅度，不保存录音。
AOQ 的音频输入保持原生采集；可在运行日志中查看
`transport.audio_publish` 的字节数、码率和编码音量，再结合服务端 `speech_started` 与 ASR 判断音频是否真正到达。
字节数增长本身也可能只是静音，不等同于识别到了用户。
语音对话仍需短暂停顿让 VAD 结束话轮，不能保证零延迟。

## 协议参考

- [千问实时模型](https://help.aliyun.com/zh/model-studio/realtime)
- [图片缓冲区事件](https://help.aliyun.com/en/model-studio/client-events)
- [AOQ 图片输入](https://help.aliyun.com/en/model-studio/use-aoq-to-access-qwen3-5-omni-plus-realtime-to-realize-key-voice-dialogue)

本次交付分支为 `codex/fuchenbo-camera-desktop`。合并时只提交源代码、测试和文档，不提交 `.env.local`、`.runtime`、日志或录制数据。
