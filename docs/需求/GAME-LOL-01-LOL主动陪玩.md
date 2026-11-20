# GAME-LOL-01：LOL 主动陪玩

状态：本局实证诊断、修复、离线回归和真实模型静音样本验证完成；修复后实战与声音效果待复验。

## 使用

1. 启动 BoxAgent，在聊天小窗底部点「LoL」，BoxAgent 会同时开启麦克风并切到 LOL。控制台的开关也可使用。默认关闭，Engine 重启后需要重新开启。
2. 进入 LOL 对局后即可接收语音陪玩。小窗标题下显示语音状态，鼠标移到「LoL」可查看对局数据状态。
3. 已开启的屏幕总结仅允许 LOL 游戏窗口作为补充，沿用原有截图和 45 秒新鲜度验证；不另建截图循环。切到其他应用或游戏最小化时暂停，不回退到浏览器、桌宠或整屏。关闭陪玩后恢复普通前台窗口观察。
4. 陪玩开启时暂停语音空闲断线，明确告别仍关闭语音。退出陪玩停止轮询并清空对局参考，两种陪玩均关闭时恢复普通空闲计时。已开启的屏幕总结恢复正常范围。

## 数据与职责

- `infrastructure/games/lol.py` 只读 Riot 官方本地 `GET https://127.0.0.1:2999/liveclientdata/allgamedata`。只在开关打开时每约 2 秒读取；无游戏时等待，不申请云端 Riot API Key。
- 本地接口使用自签名证书：仅该固定 loopback 地址使用独立 TLS 上下文，禁用代理和重定向，不改变系统证书、代理或游戏配置。超时 1 秒，单次正文上限 2 MiB。
- 边界解析一次，输出有类型的状态和事件；缺失字段保留未知，不猜测本人身份、不把缺失数值当作零。事件中的 riotIdGameName/summonerName/riotId 在解析入口映射到唯一的完整身份；同名歧义不归因，原始 KillerName/VictimName 保存在事件 raw_killer/raw_victim。不读取进程内存，不注入，不访问 LCU 登录接口，不操作游戏。
- `domain/proactivity/lol.py` 从当前对局状态推导候选事件，按优先级选择一句。接入已有对局只问候，不重播历史击杀。对局时钟回退超过 5 秒视为新局并重置事件游标与冷却。
- `application/lol_companion.py` 拥有轮询任务和开关；任务取消不发布晚到数据。临时不可用或无游戏时清空参考并继续等待。
- `bootstrap/engine.py` 装配，Engine 的 `toggle_lol_companion` 命令由 Electron 控制页调用。状态快照发布 enabled/status/text，可重连恢复显示。

## 主动性和上下文

候选包括进入对局、本人击杀/阵亡、本人多杀、低血量、小龙/塔/高地水晶公共事件、对局结束。多杀只接受本人 `Multikill` 的 `KillStreak`，普通击杀不借用累计 KDA。只有 `GameEnd` 能触发对局结束播报，`InhibKilled` 表示高地水晶被摧毁。公共事件不推断双方归属或路线。单个事件只消费一次，已经过去超过 10 个游戏秒的事件略过。普通评论从实际起播计全局间隔 20 秒，同类 45 秒，低血量 90 秒；本人阵亡和对局结束不受普通评论的全局冷却压制。血量恢复后再次跌到 25% 以下才产生新候选。

语音调度器优先处理用户交流，并在语音空闲时选择下一条主动播报。LoL 只保留最新候选，检测后 3 秒内才允许起播。过期、切换对局或退出陪玩时丢弃候选。LoL 直接唤醒调度器，微信沿用现有 Inbox 和 Outbox，已经等过一轮的微信优先。微信在实际起播时记为送达，用户插话可打断后续播放。后台操作任务和待授权期间，LoL 跳过提醒。语音开启意愿独立于底层连接，断链后按现有退避策略重连，用户手动关闭时撤销连接意愿。 [语音调度评测](../BoxAgent-实践记录.md#2026-11-21--统一语音播报调度)

陪玩请求带独立 `lol_companion` 来源，工具派发在 Runtime 边界拒绝。事件策略先生成核对过的短句，再通过 Qwen/AOQ 请求朗读。临时参考只传这一句，不传累计 KDA 或旧屏幕摘要，下次用户 Query 或提醒前按已有带回执机制删除。独立主动回复目前不写入正式交互或长期记忆，Runtime 诊断可追溯。

用户主动问当前对局时，6 秒内有效的 LOL 摘要进入现有 Query 参考包的独立 game 字段；game 与 screen 各自有 320 字符上限，仍受全包 1600 字符预算约束。提示模型以 game 的实时游戏数值优先于更旧的 screen；无游戏、接口失败或关闭时不提供旧数据。仅发送选定的本人状态，不上传账号标识、完整玩家列表或完整历史事件。语音模型仍按原配置接收这些选定事实及已授权的屏幕摘要。

用户问明确的英雄技能或装备效果时，`lookup_lol_knowledge` 从 Riot Data Dragon 查询，支持正式名称、英雄英文 ID 和装备 ID。资料按版本、语言保存在数据目录 `games/data-dragon/`，来源在对话小窗显示。可在本机设置 `BOXAGENT_LOL_DATA_VERSION` 固定目录版本、用 `BOXAGENT_LOL_DATA_LOCALE` 选择 `zh_CN`、`zh_TW` 或 `en_US`。未指定版本时采用 Riot 公布的最新静态目录版本，并明确标为资料版本。主动播报仍只依赖本地对局，不调用资料 CDN。

## 限制与验证

- 只支持进入实际对局后的 Live Client Data API；选人、大厅、客户端聊天不在本次范围。本机国服客户端已完成真实对局读取；其他客户端版本仍需实测。不可用时明确等待，不回退到内存读取。
- 事件与观察存在采样延迟；持续不可用可能漏掉事件，不从连接断开推断胜负。
- 策略是可读的本地规则，尚无策略编辑器、持久化开关、素材播放或跨游戏适配。
- 回归覆盖实际解析边界、未知与歧义身份、旧事件与重复事件、低血量边沿、起播冷却、对局切换、不可用恢复、Engine 开关与关闭、语音优先级、参考过期路径与自动工具抑制。
- 最终执行结果见本次交付说明；模拟回归不代表真实游戏/模型已验证。

2026-11-18 修复后验证：相关 Python 回归 141 passed / 63 subtests passed；项目 Pyright 0 errors（已有 pyaudio 源码解析 warning），新增三个模块单独检查 0 errors / 0 warnings。最近一次完整 Python 执行为 468 passed / 20 skipped / 93 subtests passed，2 项失败：macOS AOQ fixture 在 Windows 不满足预检，以及 Windows 删除仍被日志线程占用的文件。前者已用 HEAD 原始工厂确认，后者属于本次未改动的日志测试。首版前端构建通过，前端测试有既有 POSIX 路径断言失败。使用 PYTHONUTF8=1 执行，避免默认 GBK 对已有 UTF-8 fixture 的解码影响。

首次探测为 unavailable；随后用户开启真实对局，完整观察到 GameEnd=Win，时长约 26:29，接口关闭后状态恢复等待。旧版本实际暴露本人姓名格式不一致漏事件、5 次回复确认超时断线、屏幕旧金币覆盖实时值及部分回复偏离事件。上述修复已有离线回归；新版本还使用生产策略生成的合成事件进行真实模型静音验证（不开麦、不播放声音）：阵亡回复明确安慰，推塔回复“防御塔被摧毁了”，低血量回复明确提醒血线，没有沿用历史 4283 金币。样本记录在本地 logs/lol-live-check/production-acceptance.json；小样本不构成所有回复都准确的保证。新版本真实对局与音频效果仍待复验。

官方接口说明：https://developer.riotgames.com/docs/lol#game-client-api_live-client-data-api

BoxAgent is not endorsed by Riot Games and does not reflect the views or opinions of Riot Games or anyone officially involved in producing or managing Riot Games properties. Riot Games and all associated properties are trademarks or registered trademarks of Riot Games, Inc.
