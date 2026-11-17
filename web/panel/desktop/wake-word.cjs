const { spawn } = require("node:child_process");
const path = require("node:path");
const readline = require("node:readline");

function wakeReply(state, now = Date.now()) {
  const context = state.context_text?.trim().replace(/\s+/g, " ");
  const goal = state.task_goal?.trim().replace(/\s+/g, " ");
  if (state.task === "awaiting_approval")
    return "我在，手头的任务正在等你确认。有什么需要帮助的吗？";
  if (state.task === "running" && goal && goal.length <= 48)
    return `我在，正在处理「${goal}」。有什么需要帮助的吗？`;
  const age = now - state.context_at * 1000;
  if (state.context_enabled && age >= 0 && age <= 30_000 && context && context.length <= 48)
    return `我在，看起来你正在处理这些内容：${context}。有什么需要帮助的吗？`;
  return "我在，有什么需要帮助的吗？";
}

class WakeWord {
  constructor({ onError, onWake }) {
    Object.assign(this, { onError, onWake });
    this.enabled = true;
  }
  setEnabled(enabled) {
    this.enabled = enabled;
    if (!enabled) {
      this.stopListening();
    }
  }
  setName(name) {
    if (name === this.name) return;
    this.name = name;
    this.stopListening();
    this.sync(this.canListen);
  }
  sync(canListen) {
    this.canListen = canListen;
    if (!canListen) this.stopListening();
    if (!this.enabled || !canListen || !this.name || this.listener || this.replying) return;
    const helper = path.join(path.dirname(process.execPath), "BoxAgentWakeListener");
    const child = spawn(helper, [this.name], { stdio: ["ignore", "pipe", "pipe"] });
    this.listener = child;
    let detected = false;
    let diagnostic = "";
    child.stderr.setEncoding("utf8").on("data", (chunk) => {
      diagnostic = (diagnostic + chunk).slice(-512);
    });
    readline.createInterface({ input: child.stdout }).on("line", (line) => {
      if (line === "wake") detected = true;
    });
    child.on("error", () => {});
    child.on("close", () => {
      this.listener = undefined;
      if (child.killed) {
        this.sync(this.canListen);
        return;
      }
      if (!detected) {
        this.setEnabled(false);
        this.onError(diagnostic.trim() || "无法启动本机语音唤醒，请检查麦克风和语音识别权限");
        return;
      }
      if (!this.enabled || !this.canListen) return;
      this.replying = true;
      Promise.resolve().then(() => this.onWake()).catch((error) => {
        this.onError(error.message);
      }).finally(() => {
        this.replying = false;
        this.sync(this.canListen);
      });
    });
  }
  stopListening() {
    this.listener?.kill();
  }
}

module.exports = { WakeWord, wakeReply };
