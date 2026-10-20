import { useState } from 'react';
import { t } from '../i18n';
import type { Bridge, RecordData, RuntimeState } from '../types';

export type Refresh = (sessionId?: string) => Promise<void>;
export interface ActionState {
  busy: boolean;
  status: string;
  act<T = RecordData>(method: string, params?: RecordData): Promise<T>;
  setStatus(value: string): void;
  clearConnectionError(): void;
}
export interface PageProps<T> extends ActionState {
  data: T;
  bridge: Bridge;
  runtime: RuntimeState;
  query: string;
  refresh: Refresh;
}
export function useAction(bridge: Bridge): ActionState {
  const [busy, setBusy] = useState(false),
    [status, setStatus] = useState('');
  async function act<T = RecordData>(method: string, params: RecordData = {}): Promise<T> {
    setBusy(true);
    setStatus('');
    try {
      const result = await bridge.request<T>(method, params);
      setStatus(t('操作已完成'));
      return result;
    } catch (reason) {
      setStatus(reason instanceof Error ? reason.message : String(reason));
      throw reason;
    } finally {
      setBusy(false);
    }
  }
  function clearConnectionError() {
    setStatus((current) => /Engine (?:未连接|已断开|连接超时)|connect (?:ENOENT|ECONNREFUSED|ECONNRESET)/.test(current) ? '' : current);
  }
  return { busy, status, act, setStatus, clearConnectionError };
}
// Event handlers use this only after useAction has surfaced the error to the user.
export function run(operation: Promise<unknown>): void {
  void operation.catch(() => {});
}
const states: Record<string, string> = {
  off: '关闭',
  connecting: '连接中',
  ready: '已开启',
  idle: '空闲',
  accepted: '已接收',
  running: '执行中',
  awaiting_approval: '等待授权',
  cancelling: '停止中',
  succeeded: '完成',
  completed: '完成',
  failed: '失败',
  cancelled: '已停止',
  blocked: '受阻',
  needs_input: '待补充',
  connected: '已连接',
  disconnected: '未连接',
  listening: '聆听中',
  speaking: '回复中',
  thinking: '思考中',
  stopped: '已停止',
  error: '异常',
};
export function runtimeLabel(value: unknown): string {
  const key = String(value || '—');
  return t(states[key] || key);
}
