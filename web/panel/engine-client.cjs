const net = require('node:net');
const { EventEmitter } = require('node:events');

class EngineClient extends EventEmitter {
  constructor(path, { connectionTimeout = 5000 } = {}) {
    super(); this.path = path; this.pending = new Map(); this.seq = 0;
    this.online = false; this.closed = false; this.waiting = new Set(); this.connectionTimeout = connectionTimeout;
  }
  connect() {
    if (this.closed || (this.socket && !this.socket.destroyed)) return;
    const socket = this.socket = net.createConnection(this.path);
    let buffer = '';
    this.socket.setEncoding('utf8');
    this.socket.on('connect', () => {
      this.online = true;
      for (const item of this.waiting) { clearTimeout(item.timer); item.resolve(); }
      this.waiting.clear(); this.emit('online');
    });
    this.socket.on('data', chunk => {
      buffer += chunk;
      if (buffer.length > 16 * 1024 * 1024) return this.socket.destroy(new Error('响应过大'));
      let end;
      while ((end = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, end); buffer = buffer.slice(end + 1);
        try {
          const row = JSON.parse(line);
          if (row.type === 'event') this.emit('state', row.event.state || {}, row.event);
          else if (this.pending.has(row.id)) {
            const { resolve, reject, timer } = this.pending.get(row.id);
            clearTimeout(timer); this.pending.delete(row.id);
            row.ok ? resolve(row.result) : reject(new Error(row.error));
          }
        } catch (error) { this.emit('diagnostic', error.message); }
      }
    });
    this.socket.on('error', error => this.emit('diagnostic', error.message));
    this.socket.on('close', () => {
      if (this.socket !== socket) return;
      this.online = false;
      for (const item of this.pending.values()) { clearTimeout(item.timer); item.reject(new Error('Engine 已断开')); }
      this.pending.clear(); this.emit('offline');
    });
  }
  waitForConnection() {
    if (this.closed) return Promise.reject(new Error('Engine 已断开'));
    if (this.online && !this.socket.destroyed) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const item = { resolve, reject, timer: undefined };
      item.timer = setTimeout(() => { this.waiting.delete(item); reject(new Error('Engine 未连接')); }, this.connectionTimeout);
      this.waiting.add(item);
    });
  }
  async request(method, params = {}) {
    // Wait only before sending. Never replay an operation whose result is unknown.
    await this.waitForConnection();
    return new Promise((resolve, reject) => {
      if (!this.online || !this.socket || this.socket.destroyed) return reject(new Error('Engine 未连接'));
      const id = String(++this.seq);
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error('Engine 响应超时')); }, 20000);
      this.pending.set(id, { resolve, reject, timer });
      this.socket.write(JSON.stringify({ version: 1, type: 'request', id, method, params }) + '\n');
    });
  }
  close() {
    this.closed = true; this.online = false;
    for (const item of this.waiting) { clearTimeout(item.timer); item.reject(new Error('Engine 已断开')); }
    this.waiting.clear(); this.socket?.destroy();
  }
}
module.exports = { EngineClient };
