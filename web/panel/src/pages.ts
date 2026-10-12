import { t } from './i18n';
const definitions = {
  interface: {
    title: '界面偏好',
    eyebrow: 'SETTINGS / INTERFACE',
    description: '选择你习惯的语言和界面明暗。',
    search: '',
    footnote: '设置会自动保存，下次打开时继续使用。',
  },
  settings: {
    title: '设置',
    eyebrow: 'SETTINGS',
    description: '调整伙伴的形象、性格和运行方式。',
    search: '',
    footnote: '设置改变伙伴的配置；对话、记忆与任务记录仍在主导航中。',
  },
  conversation: {
    title: '对话与会话',
    eyebrow: 'CONVERSATION',
    description: '继续相处，也可以从一个新话题开始。',
    search: '查找消息',
    footnote: '左侧选择查看的会话；发送前须将它设为当前对话。',
  },
  appearance: {
    title: '3D 形象',
    eyebrow: 'AVATAR',
    description: '选择陪在桌面上的伙伴，预览它的表情与动作。',
    search: '查找形象',
    footnote: '预览不会替换桌宠；应用后加载成功才保存选择。',
  },
  persona: {
    title: '角色设定',
    eyebrow: 'PERSONA',
    description: '名字、性格、语气和你们相处的方式。',
    search: '',
    footnote: '保存到原来的 SOUL.md；后台在安全时机同步。',
  },
  skills: {
    title: 'Skill 管理',
    eyebrow: 'SKILLS',
    description: '管理伙伴可以使用的能力与方法。',
    search: '查找 Skill',
    footnote: '内置 Skill 可启停，用户 Skill 可新建、编辑和删除。',
  },
  runs: {
    title: '任务与运行记录',
    eyebrow: 'RUNS',
    description: '查看后台任务状态、授权和每次执行的产物。',
    search: '查找运行记录',
    footnote: '按左侧选中会话读取运行记录，打开按钮使用原生 Finder。',
  },
  controls: {
    title: '语音与桌面',
    eyebrow: 'SETTINGS / CONTROLS',
    description: '管理麦克风、屏幕总结与桌面小窗。',
    search: '',
    footnote: '退出伙伴会关闭桌宠和 Engine；面板可单独关闭。',
  },
  context: {
    title: '有效上下文',
    eyebrow: 'CONTEXT',
    description: '看看这个会话向模型提供了哪些信息。',
    search: '查找条目摘要或分类',
    footnote: '展示宿主记录的有效输入。Token 为估算；服务端截断与 KV 缓存命中不可由本页判断。',
  },
  timeline: {
    title: '事件时间线',
    eyebrow: 'TIMELINE',
    description: '把对话、参考与任务，放回它们发生的时刻。',
    search: '查找最近 150 条事件',
    footnote: '展示选中产品会话的最近 150 条记录，按时间排列；点击事件查看完整字段。',
  },
  memory: {
    title: '长期记忆',
    eyebrow: 'MEMORY',
    description: '从一次相处，到一个相互关联的记忆世界。',
    search: '查找已载入的记忆',
    footnote: '初始载入最近 100 个节点和最多 500 条关系。展开邻居读取选中节点的直接关联；筛选仅作用于已载入图。',
  },
  errors: {
    title: '诊断日志',
    eyebrow: 'DIAGNOSTICS',
    description: '集中查看最近的问题与线索。',
    search: '查找已载入的日志',
    footnote: '展示本机诊断日志的最近 100 条记录，原始字段保留在详情中。',
  },
};

export type View = keyof typeof definitions;
export const settingViews = new Set<View>(['settings', 'appearance', 'persona', 'controls', 'errors', 'interface']);
export const globalViews = new Set<View>([
  'memory',
  'errors',
  'appearance',
  'persona',
  'skills',
  'controls',
  'settings',
  'interface',
]);
export const mainNavigation: { view: View; icon: string }[] = [
  { view: 'conversation', icon: 'chat' },
  { view: 'memory', icon: 'memory' },
  { view: 'skills', icon: 'task' },
  { view: 'runs', icon: 'timeline' },
  { view: 'context', icon: 'context' },
  { view: 'timeline', icon: 'timeline' },
];

// Translate on read so a preference change cannot leave cached navigation text.
export function pageInfo(view: View) {
  const page = definitions[view];
  return {
    ...page,
    title: t(page.title),
    description: t(page.description),
    search: t(page.search),
    footnote: t(page.footnote),
  };
}
