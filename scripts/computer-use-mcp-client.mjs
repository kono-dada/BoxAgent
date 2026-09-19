import { spawn } from 'node:child_process';

// 兼容已安装执行器的 MCP 客户端；只承接本次计算器授权。
export async function connectComputerUse(executable, env) {
  const child = spawn(executable, ['mcp'], { env, stdio: ['pipe', 'pipe', 'pipe'] });
  const pending = new Map();
  let buffer = '', sequence = 0;
  const send = message => child.stdin.write(JSON.stringify(message) + '\n');
  function rpc(method, params) {
    return new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error(`${method} 超时`)); }, 15000);
      pending.set(id, { resolve, reject, timer }); send({ jsonrpc: '2.0', id, method, params });
    });
  }
  function fail(error) { for (const p of pending.values()) { clearTimeout(p.timer); p.reject(error); } pending.clear(); }
  child.on('error', fail);
  child.on('exit', () => fail(new Error('Computer Use 客户端已退出')));
  child.stdin.on('error', fail);
  child.stderr.on('data', data => process.stderr.write(data));
  child.stdout.on('data', data => {
    buffer += data.toString();
    let pos;
    while ((pos = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, pos); buffer = buffer.slice(pos + 1);
      if (!line.trim()) continue;
      let message;
      try { message = JSON.parse(line); } catch { continue; }
      if (message.method && message.id !== undefined) {
        const allowed = message.method === 'elicitation/create' && message.params?.message === 'Allow ChatGPT to use Calculator?';
        send({ jsonrpc: '2.0', id: message.id, ...(message.method === 'elicitation/create'
          ? { result: allowed ? { action: 'accept', content: {} } : { action: 'decline' } }
          : { error: { code: -32601, message: '不支持的回调' } }) });
      } else if (pending.has(message.id)) {
        const p = pending.get(message.id); pending.delete(message.id); clearTimeout(p.timer);
        message.error ? p.reject(new Error(JSON.stringify(message.error))) : p.resolve(message.result);
      }
    }
  });
  const close = () => { child.stdin.end(); child.kill('SIGTERM'); const force = setTimeout(() => child.kill('SIGKILL'), 1500); force.unref(); };
  try {
    await rpc('initialize', { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'boxagent-computer-bridge', version: '0.1.0' } });
    send({ jsonrpc: '2.0', method: 'notifications/initialized' });
  } catch (error) { close(); throw error; }
  return { close, call: (name, args) => rpc('tools/call', { name, arguments: { ...args, app: 'com.apple.calculator' } }) };
}
