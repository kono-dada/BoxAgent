import { spawn } from 'node:child_process';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { mkdir, writeFile } from 'node:fs/promises';
import { connectComputerUse } from './computer-use-mcp-client.mjs';

// 用真实 App Server 验证自然语言到 Computer Use 的链路。
const bridgeMode = process.argv.includes('--bridge');
const output = new URL(`../.runtime/private/artifacts/computer-use/${bridgeMode ? 'agent-bridge' : 'agent'}/`, import.meta.url);
await mkdir(output, { recursive: true });
const computerClient = join(process.env.CODEX_HOME || join(homedir(), '.codex'), 'computer-use/Codex Computer Use.app/Contents/SharedSupport/SkyComputerUseClient.app/Contents/MacOS/SkyComputerUseClient');
const env = Object.fromEntries(['HOME', 'PATH', 'TMPDIR', 'LANG', 'CODEX_HOME'].filter(k => process.env[k]).map(k => [k, process.env[k]]));
const mcpConfig = `mcp_servers.boxagent_cua={command=${JSON.stringify(computerClient)},args=["mcp"],enabled_tools=["get_app_state","click","type_text","press_key"]}`;
const child = spawn('codex', ['app-server', '--stdio', '-c', mcpConfig], { env, stdio: ['pipe', 'pipe', 'pipe'] });
let directComputer;
let buffer = '', sequence = 0, threadId;
const records = [], pending = new Map();
const started = Date.now();
let complete;
const completed = new Promise(resolve => { complete = resolve; });
const log = (stage, data = {}) => {
  const record = JSON.parse(JSON.stringify({ elapsedMs: Date.now() - started, stage, ...data }, (key, value) =>
    value?.type === 'image' && value.data ? { type: 'image', mimeType: value.mimeType, encodedLength: value.data.length } : value));
  records.push(record); console.log(JSON.stringify(record));
};
const send = message => child.stdin.write(JSON.stringify(message) + '\n');
function rpc(method, params) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`${method} 超时`)); }, 30000);
    pending.set(id, { resolve, reject, timer }); send({ id, method, params });
  });
}
child.stdout.on('data', data => {
  buffer += data.toString();
  let pos;
  while ((pos = buffer.indexOf('\n')) >= 0) {
    const line = buffer.slice(0, pos); buffer = buffer.slice(pos + 1);
    if (!line.trim()) continue;
    let message;
    try { message = JSON.parse(line); } catch { continue; }
    if (message.method && message.id !== undefined) {
      if (bridgeMode && message.method === 'item/tool/call' && message.params?.tool === 'calculator_ui') {
        const args = message.params.arguments;
        if (!['get_app_state', 'click', 'type_text', 'press_key'].includes(args?.operation)) {
          send({ id: message.id, result: { success: false, contentItems: [{ type: 'inputText', text: '不允许的操作' }] } });
          continue;
        }
        const { operation, ...toolArguments } = args;
        log('bridge_call', { operation, arguments: toolArguments });
        directComputer.call(operation, toolArguments)
          .then(result => {
            log('bridge_result', { operation, result });
            send({ id: message.id, result: { success: !result.isError, contentItems: result.content.filter(x => x.type === 'text').map(x => ({ type: 'inputText', text: x.text })) } });
          }).catch(error => send({ id: message.id, result: { success: false, contentItems: [{ type: 'inputText', text: error.message }] } }));
        continue;
      }
      const allowed = message.method === 'mcpServer/elicitation/request' && message.params?.serverName === 'boxagent_cua' && message.params?.message === 'Allow ChatGPT to use Calculator?';
      log('server_request', { method: message.method, params: message.params, accepted: allowed });
      send({ id: message.id, ...(message.method === 'mcpServer/elicitation/request'
        ? { result: { action: allowed ? 'accept' : 'decline', content: allowed ? {} : null } }
        : { error: { code: -32601, message: '本实验仅支持已授权的计算器 MCP 请求' } }) });
    } else if (pending.has(message.id)) {
      const item = pending.get(message.id); pending.delete(message.id); clearTimeout(item.timer);
      message.error ? item.reject(new Error(JSON.stringify(message.error))) : item.resolve(message.result);
    } else if (message.method) {
      if (!message.method.startsWith('codex/event/')) log('event', { method: message.method, params: message.params });
      if (message.method === 'turn/completed') complete(message.params);
    }
  }
});
function terminatePending(error) { for (const item of pending.values()) { clearTimeout(item.timer); item.reject(error); } pending.clear(); complete({ error: error.message }); }
child.on('error', terminatePending);
child.on('exit', () => terminatePending(new Error('App Server 已退出')));
child.stdin.on('error', terminatePending);
child.stderr.on('data', data => process.stderr.write(data));
let timer;
try {
  if (bridgeMode) directComputer = await connectComputerUse(computerClient, env);
  await rpc('initialize', { clientInfo: { name: 'boxagent-integration-probe', version: '0.1.0' }, capabilities: { experimentalApi: true } });
  send({ method: 'initialized' });
  const thread = await rpc('thread/start', {
    cwd: process.cwd(), ephemeral: true, approvalPolicy: 'on-request', sandbox: 'read-only',
    developerInstructions: `本轮是固定计算器接入实验。只使用 ${bridgeMode ? 'calculator_ui 动态工具' : 'boxagent_cua MCP 工具'} 操作 com.apple.calculator。禁止 shell、文件修改、其他应用和子代理。用户已授权本次计算器读取、清除输入和计算。每组操作后重新读取界面；遇到权限或工具错误如实停止。`,
    ...(bridgeMode ? { dynamicTools: [{ type: 'function', name: 'calculator_ui', description: '通过本机 Computer Use 操作已授权的计算器。先用 get_app_state 读取真实界面。click 的 element_index 必须来自最新界面。每组操作后再次读取。', inputSchema: {
      type: 'object', properties: { operation: { type: 'string', enum: ['get_app_state', 'click', 'type_text', 'press_key'] }, element_index: { type: 'string' }, text: { type: 'string' }, key: { type: 'string' } }, required: ['operation'], additionalProperties: false,
    } }] } : {}),
  });
  threadId = thread.thread.id;
  log('thread', { threadId, model: thread.model });
  const servers = await rpc('mcpServerStatus/list', {});
  const computer = servers.data?.find(server => server.name === 'boxagent_cua');
  log('computer_server', { server: computer });
  if (!computer || !Object.keys(computer.tools || {}).length) throw new Error('App Server 未加载计算器 MCP 工具，停止模型调用');
  await rpc('turn/start', { threadId, input: [{ type: 'text', text: `请使用 ${bridgeMode ? 'calculator_ui' : 'boxagent_cua'} 在 macOS 计算器中计算 219+436。先读取界面再清除当前输入，输入算式并计算，最后重新读取计算器界面确认结果。不要自行心算代替实际操作。用中文简短报告界面显示的结果。`, text_elements: [] }] });
  const result = await Promise.race([completed, new Promise(resolve => { timer = setTimeout(() => resolve({ timeout: true }), 120000); })]);
  if (result.timeout) { await rpc('turn/interrupt', { threadId, turnId: records.findLast(x => x.params?.turn?.id)?.params.turn.id }); process.exitCode = 1; }
  if (result.error || result.turn?.status !== 'completed') process.exitCode = 1;
  const toolCalls = records.filter(x => x.method === 'item/completed' &&
    x.params?.item?.type === 'mcpToolCall' && x.params.item.server === 'boxagent_cua');
  const finalText = (result.turn?.items || []).filter(x => x.type === 'agentMessage').map(x => x.text).join('\n');
  const reads = records.filter(x => x.stage === 'bridge_result' && x.operation === 'get_app_state' && !x.result.isError);
  const passed = (toolCalls.some(x => x.params.item.tool === 'get_app_state') || reads.some(x => JSON.stringify(x.result).includes('655'))) && /655/.test(finalText);
  log('agent_assertion', { passed, completedComputerToolCalls: toolCalls.length, finalText });
  if (!passed) process.exitCode = 1;
  log('finished', { result });
} catch (error) { log('error', { message: error.message }); process.exitCode = 1; }
finally {
  clearTimeout(timer);
  directComputer?.close();
  await writeFile(new URL('events.jsonl', output), records.map(x => JSON.stringify(x)).join('\n') + '\n');
  child.stdin.end(); child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 1500); force.unref();
}
