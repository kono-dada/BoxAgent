const test = require('node:test');
const assert = require('node:assert/strict');
const net = require('node:net');
const path = require('node:path');
const os = require('node:os');
const { EngineClient } = require('../engine-client.cjs');

test('ready event and split response are not confused with request result', async () => {
  const socket = path.join(os.tmpdir(), `boxagent-panel-${process.pid}.sock`);
  const server = net.createServer(client => { client.write('{"type":"event","event":{"state":{"voice":"ready"}}}\n');
    client.on('data', chunk => { const request = JSON.parse(chunk); const row = JSON.stringify({ type: 'response', id: request.id, ok: true, result: { answer: 42 } }) + '\n'; client.write(row.slice(0, 10)); client.write(row.slice(10)); }); });
  await new Promise(resolve => server.listen(socket, resolve));
  const client = new EngineClient(socket); client.connect();
  try { assert.deepEqual(await client.request('state'), { answer: 42 }); }
  finally { client.close(); await new Promise(resolve => server.close(resolve)); }
});
