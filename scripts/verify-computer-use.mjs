import { spawn } from 'node:child_process';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { mkdir, writeFile } from 'node:fs/promises';

// 固定计算器实验，不接受任意 App 或任意操作参数。
const cancelMode = process.argv.includes('--cancel');
const output = new URL(`../.runtime/private/artifacts/computer-use/${cancelMode ? 'cancel' : 'arithmetic'}/`, import.meta.url);
await mkdir(output, { recursive: true });
const records = [];
const start = Date.now();
const withoutImageData = value => JSON.parse(JSON.stringify(value, (key, item) =>
  item?.type === 'image' && item.data ? { type: 'image', mimeType: item.mimeType, encodedLength: item.data.length } : item));
const log = (stage, data = {}) => { const record = withoutImageData({ elapsedMs: Date.now() - start, stage, ...data }); records.push(record); console.log(JSON.stringify(record)); };
const env = Object.fromEntries(['HOME', 'PATH', 'TMPDIR', 'LANG', 'CODEX_HOME'].filter(k => process.env[k]).map(k => [k, process.env[k]]));
const executable = join(process.env.CODEX_HOME || join(homedir(), '.codex'), 'computer-use/Codex Computer Use.app/Contents/SharedSupport/SkyComputerUseClient.app/Contents/MacOS/SkyComputerUseClient');
const child = spawn(executable, ['mcp'], { env, stdio: ['pipe', 'pipe', 'pipe'] });
const pending = new Map();
let buffer = '', nextId = 0;
const send = message => child.stdin.write(JSON.stringify(message) + '\n');
function request(method, params) {
  const id = ++nextId;
  const promise = new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`请求 ${id} 超时`)); }, 15000);
    pending.set(id, { resolve, reject, timer });
    send({ jsonrpc: '2.0', id, method, params });
  });
  return { id, promise };
}
child.stdout.on('data', data => {
  buffer += data.toString();
  let position;
  while ((position = buffer.indexOf('\n')) >= 0) {
    const line = buffer.slice(0, position); buffer = buffer.slice(position + 1);
    if (!line.trim()) continue;
    let message;
    try { message = JSON.parse(line); } catch { log('non_json'); continue; }
    if (message.method && message.id !== undefined) {
      const accepted = message.method === 'elicitation/create' && message.params?.message === 'Allow ChatGPT to use Calculator?';
      log('approval', { method: message.method, accepted });
      send({ jsonrpc: '2.0', id: message.id, ...(message.method === 'elicitation/create'
        ? { result: accepted ? { action: 'accept', content: {} } : { action: 'decline' } }
        : { error: { code: -32601, message: '不支持的宿主回调' } }) });
    } else if (pending.has(message.id)) {
      const item = pending.get(message.id); pending.delete(message.id); clearTimeout(item.timer);
      message.error ? item.reject(new Error(JSON.stringify(message.error))) : item.resolve(message.result);
    }
  }
});
function rejectPending(error) { for (const item of pending.values()) { clearTimeout(item.timer); item.reject(error); } pending.clear(); }
child.on('error', rejectPending);
child.on('exit', (code, signal) => rejectPending(new Error(`客户端退出 ${code} ${signal}`)));
child.stdin.on('error', rejectPending);
child.stderr.on('data', data => process.stderr.write(data));
const call = (name, args = {}) => request('tools/call', { name, arguments: { app: 'com.apple.calculator', ...args } });
async function tool(name, args) {
  const result = await call(name, args).promise;
  if (result.isError) throw new Error(JSON.stringify(result));
  return result;
}
async function state(label) {
  const result = await tool('get_app_state');
  const text = (result.content || []).filter(x => x.type === 'text').map(x => x.text).join('\n');
  await writeFile(new URL(`${label}.txt`, output), text);
  for (const item of result.content || []) if (item.type === 'image') {
    await writeFile(new URL(`${label}.${item.mimeType === 'image/png' ? 'png' : 'jpg'}`, output), Buffer.from(item.data, 'base64'));
  }
  log('state', { label, text });
  return text;
}
try {
  log('initialize', { result: await request('initialize', { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'boxagent-calculator-validation', version: '0.1.0' } }).promise });
  send({ jsonrpc: '2.0', method: 'notifications/initialized' });
  const catalog = await request('tools/list', {}).promise;
  for (const name of ['get_app_state', 'click', 'type_text', 'press_key']) if (!catalog.tools?.some(x => x.name === name)) throw new Error(`缺少工具 ${name}`);
  const initial = await state('before');
  const clear = initial.match(/^\s*(\d+) 按钮 Description: 全部清除,/m)?.[1];
  if (!clear) throw new Error('当前界面未找到全部清除按钮，停止实验');
  await tool('click', { element_index: clear });
  await state('cleared');
  if (cancelMode) {
    const action = call('type_text', { text: '8642' });
    log('action_sent', { id: action.id, text: '8642' });
    send({ jsonrpc: '2.0', method: 'notifications/cancelled', params: { requestId: action.id, reason: '用户中止实验任务' } });
    log('cancel_sent', { requestId: action.id });
    try { log('cancelled_request_result', { result: await action.promise }); }
    catch (error) { log('cancelled_request_error', { message: error.message }); }
    await state('after-cancel');
    await new Promise(resolve => setTimeout(resolve, 2000));
    await state('after-cancel-settled');
    log('queue_stopped', { equalsWasSent: false });
  } else {
    await tool('type_text', { text: '137+248' });
    await state('typed');
    await tool('press_key', { key: 'Return' });
    const final = await state('result');
    const normalized = final.replace(/[\u200e\u200f\u2066-\u2069]/g, '');
    const passed = /^\s*\d+ 文本 385\s*$/m.test(normalized);
    log('assertion', { expected: '385', passed });
    if (!passed) process.exitCode = 1;
  }
} catch (error) { log('error', { message: error.message }); process.exitCode = 1; }
finally {
  await writeFile(new URL('events.jsonl', output), records.map(x => JSON.stringify(x)).join('\n') + '\n');
  child.stdin.end(); child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 1500); force.unref();
}
