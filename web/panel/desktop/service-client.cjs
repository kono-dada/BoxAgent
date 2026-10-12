const { spawn } = require("node:child_process");
const readline = require("node:readline");
const { EventEmitter } = require("node:events");
class ServiceClient extends EventEmitter {
  constructor({
    root,
    data,
    log,
    args = [],
    python = process.env.BOXAGENT_DESKTOP_PYTHON,
  }) {
    super();
    this.pending = new Map();
    this.seq = 0;
    this.closed = false;
    if (!python) throw Error("请通过 ./scripts/run-pet.sh 启动完整桌宠。");
    this.child = spawn(
      python,
      ["-m", "boxagent.entrypoints.desktop_service", ...args],
      {
        cwd: root,
        env: { ...process.env, BOXAGENT_DATA_DIR: data, PYTHONUNBUFFERED: "1" },
        stdio: ["pipe", "pipe", log],
      },
    );
    readline
      .createInterface({ input: this.child.stdout })
      .on("line", (line) => {
        let row;
        try {
          row = JSON.parse(line);
        } catch {
          this.emit("diagnostic", line);
          return;
        }
        const item = this.pending.get(row.id);
        if (!item) return;
        clearTimeout(item.timer);
        this.pending.delete(row.id);
        row.ok ? item.resolve(row.result) : item.reject(Error(row.error));
      });
    this.child.stdin.on("error", (error) => this.fail(error));
    this.child.on("error", (error) => this.fail(error));
    this.child.on("exit", () => this.fail(Error("桌面本机服务已退出")));
  }
  fail(error) {
    for (const item of this.pending.values()) {
      clearTimeout(item.timer);
      item.reject(error);
    }
    this.pending.clear();
    if (!this.closed) this.emit("diagnostic", error.message);
  }
  request(method, params = {}) {
    if (this.closed || !this.child.stdin.writable)
      return Promise.reject(Error("桌面服务未连接"));
    return new Promise((resolve, reject) => {
      const id = String(++this.seq),
        timer = setTimeout(() => {
          this.pending.delete(id);
          reject(Error("桌面服务响应超时"));
        }, 45000);
      this.pending.set(id, { resolve, reject, timer });
      this.child.stdin.write(JSON.stringify({ id, method, params }) + "\n");
    });
  }
  async close() {
    if (this.closed) return;
    this.closed = true;
    if (
      !this.child.pid ||
      this.child.exitCode !== null ||
      this.child.signalCode !== null
    ) {
      this.fail(Error("桌面服务已关闭"));
      return;
    }
    const exited = new Promise((resolve) => this.child.once("exit", resolve));
    this.child.stdin.end();
    const timeout = setTimeout(() => this.child.kill("SIGTERM"), 14000);
    const forced = setTimeout(() => this.child.kill("SIGKILL"), 17000);
    await exited;
    clearTimeout(timeout);
    clearTimeout(forced);
    this.fail(Error("桌面服务已关闭"));
  }
}
module.exports = { ServiceClient };
