const net = require('node:net');
const { EventEmitter } = require('node:events');

class EngineClient extends EventEmitter {
  constructor(path) { super(); this.path = path; this.pending = new Map(); this.seq = 0; }
  connect() {
    this.socket = net.createConnection(this.path);
    let buffer = '';
    this.socket.setEncoding('utf8');
    this.socket.on('connect', () => this.emit('online'));
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
      for (const item of this.pending.values()) { clearTimeout(item.timer); item.reject(new Error('Engine 已断开')); }
      this.pending.clear(); this.emit('offline');
    });
  }
  request(method, params = {}) {
    return new Promise((resolve, reject) => {
      if (!this.socket || this.socket.destroyed) return reject(new Error('Engine 未连接'));
      const id = String(++this.seq);
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error('Engine 响应超时')); }, 20000);
      this.pending.set(id, { resolve, reject, timer });
      this.socket.write(JSON.stringify({ version: 1, type: 'request', id, method, params }) + '\n');
    });
  }
  close() { this.socket?.destroy(); }
}
module.exports = { EngineClient };
