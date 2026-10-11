// 对齐 Mate-Engine：预制体覆盖后的待机参数、Drag 状态与独立摆动控制。
export const MATE_DEFAULTS = Object.freeze({
  idleSwitchTime: 10, idleTransitionTime: 1, dragTransitionTime: 0.25,
  dragMinimumHold: 0.30, enableSleep: false, sleepTimer: 60,
});
export const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const damp = (value, target, speed, dt) => value + (target - value) * (1 - Math.exp(-speed * dt));
class Spring {
  constructor() { this.x = 0; this.v = 0; }
  step(target, dt) {
    // 保留原版频率/阻尼，子步积分避免 WebKit 慢帧时发散。
    const steps = Math.max(1, Math.ceil(dt * 120)), h = dt / steps, w = 2 * Math.PI * 2.6;
    for (let i = 0; i < steps; i++) {
      this.v += (w * w * (target - this.x) - 2 * 0.35 * w * this.v) * h;
      this.x += this.v * h;
    }
    return this.x;
  }
}
export class DragSway {
  constructor() { this.roll = new Spring(); this.pitch = new Spring(); this.filtered = [0,0]; this.weight = 0; }
  step(velocity, active, dt) {
    // 原版非 Windows 分支使用鼠标帧位移 × 0.6；原生速度统一换算成 60 Hz 位移。
    this.filtered = this.filtered.map((v,i) => damp(v, active ? velocity[i] / 60 * 0.6 : 0, 12, dt));
    const roll = this.roll.step(active ? clamp(-this.filtered[0] * 0.25, -25, 25) : 0, dt);
    const pitch = this.pitch.step(active ? clamp(this.filtered[1] * 0.15, -12, 12) : 0, dt);
    this.weight = clamp(this.weight + (active ? 8 : -16) * dt, 0, 1);
    return {roll:roll * this.weight * Math.PI / 180, pitch:pitch * this.weight * Math.PI / 180, weight:this.weight};
  }
}
export class MateController {
  constructor(options = {}) {
    this.settings = {...MATE_DEFAULTS, ...options.settings};
    this.idleActions = options.idleActions || [];
    this.roles = options.roles || {};
    this.time = 0; this.state = 'idle'; this.stateSince = 0;
    this.idleIndex = 0; this.idleTimer = 0;
    this.mouseHeld = false; this.dragging = false; this.dragUntil = 0;
    this.dragVelocity = [0,0]; this.dragAt = -100;
    this.sleep = 0; this.sleepElapsed = 0;
    this.gesture = null; this.history = []; this.events = [];
    this.sway = new DragSway();
  }
  presentState(state) {
    if (state === this.state) return;
    this.state = state; this.stateSince = this.time; this.gesture = null;
  }
  wake() { this.sleepElapsed = 0; this.sleep = 0; }
  interact({kind,velocity = [0,0]}) {
    this.wake();
    if (kind === 'drag-start' || kind === 'drag-move') {
      if (!this.mouseHeld) {
        this.dragUntil = this.time + this.settings.dragMinimumHold;
        this.events.push({type:'pickup',at:this.time});
      }
      this.mouseHeld = true; this.dragging = true; this.gesture = null;
      this.dragVelocity = velocity.map(v => clamp(Number(v) || 0,-2400,2400));
      this.dragAt = this.time;
    } else if (kind === 'drag-end') {
      this.mouseHeld = false; this.dragVelocity = [0,0];
      this.events.push({type:'release',at:this.time});
    }
    this.events = this.events.slice(-24);
  }
  startGesture(name) {
    const spec = this.idleActions.find(item => item.name === name);
    if (spec) this.gesture = {...spec,start:this.time};
  }
  advanceState(dt) {
    this.time += dt;
    if (!this.mouseHeld && this.time >= this.dragUntil) this.dragging = false;
    // 原版独立更新 IdleIndex；拖拽结束后恢复当前索引，而不是重置第一个动作。
    this.idleTimer += dt;
    if (this.idleTimer >= this.settings.idleSwitchTime && this.idleActions.length) {
      this.idleTimer %= this.settings.idleSwitchTime;
      this.idleIndex = (this.idleIndex + 1) % this.idleActions.length;
      this.history.push(this.idleActions[this.idleIndex].name);
      this.history = this.history.slice(-24);
    }
    if (this.gesture && this.time - this.gesture.start > this.gesture.duration) this.gesture = null;
    const allowed = this.state === 'idle' && !this.dragging;
    if (!this.settings.enableSleep || !allowed) this.wake();
    else {
      this.sleepElapsed += dt;
      this.sleep = this.sleepElapsed >= this.settings.sleepTimer ? 1 : 0;
    }
    const fade = Math.exp(-Math.max(0,this.time-this.dragAt-0.04)*13);
    return this.sway.step(this.dragVelocity.map(v=>v*fade),this.dragging,dt);
  }
  motion() {
    const idle = this.gesture?.name || this.idleActions[this.idleIndex]?.name || 'rest';
    if (this.dragging) return {name:this.roles.dragging || 'rest',kind:'drag',loop:true};
    if (this.sleep) return {name:this.roles.sleeping || 'rest',kind:'sleep',loop:true};
    if (this.state === 'speaking') return {name:this.roles.speaking || idle,kind:'speaking',loop:true};
    if (this.state === 'success' && this.time-this.stateSince < 3) return {name:this.roles.success || idle,kind:'success',loop:false};
    return {name:idle,kind:'idle',loop:true};
  }
}
