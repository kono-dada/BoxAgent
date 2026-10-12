import { t } from './i18n';
import type { RecordData } from './types';
export interface EventRecord extends RecordData {
  type: string;
}
const categoryLabels: Record<string, string> = {
  instructions: '系统指令',
  persona: '角色设定',
  tools: '工具定义',
  profile: '用户画像',
  checkpoint: '上下文检查点',
  history: '恢复历史',
  environment: '环境包',
  memory: '记忆包',
  reference: '本轮参考包',
  user: '用户消息',
  assistant: '助手回复',
  tool_call: '工具调用',
  tool_result: '工具结果',
  notification: '任务通知',
  system_other: '其他系统消息',
};
const eventLabels: Record<string, string> = {
  'turn/start': '回合开始',
  'turn/end': '回合结束',
  'user/message': '你说',
  'assistant/message': '伙伴回复',
  'task/start': '任务开始',
  'task/started': '任务开始',
  'task/end': '任务结束',
  'task/completed': '任务完成',
  'task/status': '任务状态',
  'task/result': '任务结果',
  'context/rebuild': '上下文恢复',
  'context/item': '上下文条目',
  'context/bound': '参考包绑定',
  'context/expired': '参考包到期',
  'context/deleted': '参考包删除回执',
  'context/response/requested': '请求生成回复',
  'context/response/created': '开始生成回复',
  'context/turn/start': '模型回合开始',
  'context/turn/prepare': '回合前准备',
  'migration/source': '历史迁移',
  'tool/call': '调用工具',
  'tool/result': '工具返回',
  'task.accepted': '任务已接收',
  'task.started': '任务开始',
  'task.completed': '任务完成',
  'task.failed': '任务失败',
  'task.cancelled': '任务已停止',
  'task.progress': '任务进展',
  'task.heartbeat': '任务运行中',
  'task.approval': '等待授权',
  'task.approval_resolved': '授权已处理',
  'task.cancelling': '正在停止任务',
  'compaction/created': '上下文检查点',
};
function record(value: unknown): RecordData {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as RecordData) : {};
}
const legacyTypes: Record<string, string> = {
  'interaction.started': 'turn/start',
  'interaction.finalized': 'turn/end',
  'context.checkpoint.created': 'compaction/created',
};
export function normalizeEvent(row: EventRecord) {
  const metadata = record(row.data),
    productEvent = metadata.product_event;
  const data = productEvent ? record(productEvent) : { ...row, ...metadata };
  const nested = record(data.data);
  const legacyType = row.type === 'message.final' ? `${row.role}/message` : legacyTypes[row.type] || row.type;
  const type = row.type === 'context/item' || productEvent ? row.type : legacyType;
  const category = typeof data.category === 'string' ? data.category : '';
  const label = type === 'context/item' ? t(categoryLabels[category] || '上下文条目') : t(eventLabels[type] || type);
  let preview = data.content || data.task_text || nested.task_text || data.status || data.reference || '';
  if (!preview && category) preview = t('分类：{0}', category);
  if (!preview && type.includes('prepare')) {
    const fields = ['memory', 'jev', 'intent', 'environment'].filter((name) => data[name]);
    preview = fields.length ? t('准备项目：{0}', fields.join(' · ')) : t('记录当前回合参考信息准备状态');
  }
  return {
    original: row,
    type,
    label,
    seq:
      typeof (row.seq ?? row.sequence) === 'number'
        ? Number(row.seq ?? row.sequence)
        : String(row.seq ?? row.sequence ?? ''),
    time: row.time ?? data.occurred_at ?? row.occurred_at,
    runtime: String(data.runtime || row.runtime || ''),
    query: String(data.query_id || data.interaction_id || nested.query_id || ''),
    preview: typeof preview === 'string' ? preview.trim() : JSON.stringify(preview),
  };
}
