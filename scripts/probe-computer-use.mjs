import { spawn } from 'node:child_process';
import { homedir } from 'node:os';
import { join } from 'node:path';

// 默认只握手和列出工具；显式参数允许读取计算器界面。
const readCalculator = process.argv.includes('--calculator-state');
const allowCalculatorOnce = process.argv.includes('--allow-calculator-once');
const client = join(process.env.CODEX_HOME || join(homedir(), '.codex'),
  'computer-use/Codex Computer Use.app/Contents/SharedSupport/SkyComputerUseClient.app/Contents/MacOS/SkyComputerUseClient');
// 不继承当前 Codex 会话的令牌或运行时变量，模拟普通第三方程序。
const env = Object.fromEntries(['HOME', 'PATH', 'TMPDIR', 'LANG', 'CODEX_HOME']
  .filter(key => process.env[key]).map(key => [key, process.env[key]]));
const child = spawn(client, ['mcp'], { env, stdio: ['pipe', 'pipe', 'pipe'] });
let buffer = '';
let finished = false;
let count = 0;
const started = Date.now();
const log = (stage, data) => console.log(JSON.stringify({ elapsedMs: Date.now() - started, stage, ...data }));
const send = message => child.stdin.write(JSON.stringify(message) + '\n');
const finish = code => {
  if (finished) return;
  finished = true;
  clearTimeout(timer);
  process.exitCode = code;
  child.stdin.end();
  child.kill('SIGTERM');
  const force = setTimeout(() => child.kill('SIGKILL'), 1500);
  force.unref();
};
const timer = setTimeout(() => { log('timeout', { message: '20 秒内未完成 MCP 探测' }); finish(1); }, 20000);
child.on('error', error => { log('spawn_error', { message: error.message }); finish(1); });
child.stdin.on('error', error => { log('stdin_error', { message: error.message }); finish(1); });
child.stderr.on('data', data => process.stderr.write(data));
child.on('exit', (code, signal) => {
  log('exit', { code, signal });
  if (!finished) finish(1);
});
child.stdout.on('data', data => {
  buffer += data.toString();
  let newline;
  while ((newline = buffer.indexOf('\n')) !== -1) {
    const line = buffer.slice(0, newline);
    buffer = buffer.slice(newline + 1);
    if (!line.trim()) continue;
    let message;
    try { message = JSON.parse(line); } catch { log('non_json', { text: line.slice(0, 500) }); continue; }
    if (message.error) { log('rpc_error', { id: message.id, error: message.error }); finish(1); return; }
    if (message.id === 1) {
      log('initialize', { result: message.result });
      send({ jsonrpc: '2.0', method: 'notifications/initialized' });
      send({ jsonrpc: '2.0', id: 2, method: 'tools/list', params: {} });
    } else if (message.id === 2) {
      const result = message.result || {};
      count += (result.tools || []).length;
      log('tools', { tools: (result.tools || []).map(tool => ({ name: tool.name, inputSchema: tool.inputSchema })) });
      if (result.nextCursor) send({ jsonrpc: '2.0', id: 2, method: 'tools/list', params: { cursor: result.nextCursor } });
      else if (readCalculator) {
        send({ jsonrpc: '2.0', id: 3, method: 'tools/call', params: {
          name: 'get_app_state', arguments: { app: 'com.apple.calculator' },
        } });
      } else { log('complete', { toolCount: count }); finish(0); }
    } else if (message.id === 3) {
      const result = message.result || {};
      // 图片只记录格式与长度，避免把截图的 base64 写入日志。
      log('calculator_state', { ...result, content: (result.content || []).map(item =>
        item.type === 'image' ? { type: item.type, mimeType: item.mimeType, encodedLength: item.data?.length } : item) });
      finish(result.isError ? 1 : 0);
    } else if (message.method && message.id !== undefined) {
      log('server_request', { method: message.method, params: message.params });
      if (message.method === 'elicitation/create') {
        // 仅在用户明确授权后接受这个精确匹配的计算器请求，不保存永久许可。
        const allowed = readCalculator && allowCalculatorOnce &&
          message.params?.message === 'Allow ChatGPT to use Calculator?';
        send({ jsonrpc: '2.0', id: message.id, result: allowed
          ? { action: 'accept', content: {} } : { action: 'decline' } });
      } else send({ jsonrpc: '2.0', id: message.id, error: { code: -32601, message: '探针不提供此宿主回调' } });
    }
  }
});
send({ jsonrpc: '2.0', id: 1, method: 'initialize', params: {
  protocolVersion: '2024-11-05', capabilities: {},
  clientInfo: { name: 'boxagent-independent-probe', version: '0.1.0' },
} });
