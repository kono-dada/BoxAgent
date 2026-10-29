const test = require('node:test');
const assert = require('node:assert/strict');
const net = require('node:net');
const path = require('node:path');
const os = require('node:os');
const fs = require('node:fs');
const socketPath = name => process.platform === 'win32' ? `\\\\.\\pipe\\${name}` : path.join(os.tmpdir(), name);
const { EngineClient } = require('../engine-client.cjs');

test('Windows client reads endpoint and sends token before requests', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'boxagent-tcp-'));
  const endpoint = path.join(directory, 'engine.sock');
  const token = 'a'.repeat(64);
  const lines = [];
  const server = net.createServer(connection => {
    let buffer = '';
    connection.on('data', chunk => {
      buffer += chunk.toString();
      let end;
      while ((end = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, end); buffer = buffer.slice(end + 1);
        lines.push(line);
        if (lines.length === 1) {
          assert.equal(line, token);
          continue;
        }
        const row = JSON.parse(line);
        setTimeout(() => connection.write(JSON.stringify({id: row.id, ok: true, result: 'ready'}) + '\n'),
          row.method === 'camera_start' ? 40 : 0);
      }
    });
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  fs.writeFileSync(endpoint, JSON.stringify({port: server.address().port, token}));
  const client = new EngineClient(endpoint, {platform: 'win32', requestTimeout: 20, voiceTimeout: 200});
  try {
    client.connect();
    assert.equal(await client.request('health'), 'ready');
    assert.equal(lines.length, 2);
    assert.equal(await client.request('camera_start'), 'ready', 'Windows camera startup also uses the longer voice budget');
    assert.equal(lines.length, 3);
  } finally {
    client.close();
    await new Promise(resolve => server.close(resolve));
    fs.rmSync(directory, {recursive: true, force: true});
  }
});

test('Windows client reports an unavailable endpoint and can retry', () => {
  const client = new EngineClient(path.join(os.tmpdir(), 'missing-boxagent-endpoint'), {platform: 'win32'});
  let offline = 0;
  client.on('offline', () => offline++);
  client.connect(); client.connect();
  assert.equal(offline, 2);
  assert.equal(client.online, false);
  client.close();
});

test('ready event and split response are not confused with request result', async () => {
  const socket = socketPath(`boxagent-panel-${process.pid}.sock`);
  const server = net.createServer(client => { client.write('{"type":"event","event":{"state":{"voice":"ready"}}}\n');
    client.on('data', chunk => { const request = JSON.parse(chunk); const row = JSON.stringify({ type: 'response', id: request.id, ok: true, result: { answer: 42 } }) + '\n'; client.write(row.slice(0, 10)); client.write(row.slice(10)); }); });
  await new Promise(resolve => server.listen(socket, resolve));
  const client = new EngineClient(socket, {platform: 'darwin'}); client.connect();
  try { assert.deepEqual(await client.request('state'), { answer: 42 }); }
  finally { client.close(); await new Promise(resolve => server.close(resolve)); }
});

test('requests wait through startup and are sent once after a delayed connection', async () => {
  const socket = socketPath(`boxagent-panel-delayed-${process.pid}.sock`);
  const requests = [];
  const server = net.createServer(connection => {
    connection.on('data', chunk => {
      for (const line of String(chunk).trim().split('\n')) {
        const request = JSON.parse(line); requests.push(request);
        connection.write(JSON.stringify({id: request.id, ok: true, result: request.method}) + '\n');
      }
    });
  });
  const client = new EngineClient(socket, {connectionTimeout: 1000, platform: 'darwin'});
  // Keep the fixture quiet while the socket is absent; mirror production reconnect.
  client.on('offline', () => {});
  const ready = Promise.all([client.request('state'), client.request('list_sessions')]);
  try {
    const offline = new Promise(resolve => client.once('offline', resolve));
    client.connect(); await offline;
    await new Promise(resolve => server.listen(socket, resolve));
    client.connect();
    assert.deepEqual(await ready, ['state', 'list_sessions']);
    assert.deepEqual(requests.map(row => row.method), ['state', 'list_sessions']);
  } finally { client.close(); await new Promise(resolve => server.close(resolve)); }
});

test('unavailable connection times out and shutdown cancels waiting requests', async () => {
  const client = new EngineClient('unused', {connectionTimeout: 30});
  await assert.rejects(client.request('state'), /Engine 未连接/);
  const waiting = assert.rejects(client.request('state'), /Engine 已断开/);
  client.close(); await waiting;
  assert.equal(client.waiting.size, 0);
});

test('an operation already sent is rejected on disconnect rather than replayed', async () => {
  const socket = socketPath(`boxagent-panel-drop-${process.pid}.sock`);
  let requests = 0;
  const server = net.createServer(connection => connection.on('data', () => { requests++; connection.destroy(); }));
  await new Promise(resolve => server.listen(socket, resolve));
  const client = new EngineClient(socket, {platform: 'darwin'});
  try {
    client.connect(); await assert.rejects(client.request('submit_text'), /Engine 已断开/);
    assert.equal(requests, 1); assert.equal(client.pending.size, 0);
  } finally { client.close(); await new Promise(resolve => server.close(resolve)); }
});

test('voice startup waits beyond normal RPC timeout and preserves provider errors', async () => {
  const socket = socketPath(`boxagent-panel-voice-${process.pid}.sock`);
  const requests = [];
  const server = net.createServer(connection => connection.on('data', chunk => {
    for (const line of String(chunk).trim().split('\n')) {
      const request = JSON.parse(line); requests.push(request.method);
      if (request.method === 'state') continue;
      setTimeout(() => connection.write(JSON.stringify({id: request.id, ok: false, error: 'AOQ 服务器连接超时'}) + '\n'), 40);
    }
  }));
  await new Promise(resolve => server.listen(socket, resolve));
  const client = new EngineClient(socket, {requestTimeout: 10, voiceTimeout: 200, platform: 'darwin'});
  try {
    client.connect();
    await assert.rejects(client.request('state'), /Engine 响应超时/);
    for (const method of ['toggle_voice', 'camera_start'])
      await assert.rejects(client.request(method), /^Error: AOQ 服务器连接超时$/);
    assert.deepEqual(requests, ['state', 'toggle_voice', 'camera_start']);
    assert.equal(client.pending.size, 0);
  } finally { client.close(); await new Promise(resolve => server.close(resolve)); }
});
