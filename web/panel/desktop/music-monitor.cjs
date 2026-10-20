const { execFile } = require('node:child_process');
const path = require('node:path');

function probeWindowsMusic() {
  return new Promise((resolve, reject) => {
    execFile('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
      '-File', path.resolve(__dirname, '../../../scripts/windows-media.ps1')],
    {windowsHide:true, timeout:8000, maxBuffer:16384, encoding:'utf8'}, (error, stdout) => {
      if (error) { reject(error); return; }
      try {
        const result = JSON.parse(stdout.replace(/^\uFEFF/, '').trim());
        if (typeof result.music !== 'boolean') throw Error('Invalid music status');
        resolve(result.music);
      } catch (failure) { reject(failure); }
    });
  });
}

class MusicMonitor {
  constructor(onChange, {probe = probeWindowsMusic, interval = 3000} = {}) {
    this.onChange=onChange; this.probe=probe; this.interval=interval;
    this.active=false; this.stopped=false; this.busy=false;
  }
  start() {
    if (this.timer || this.stopped) return;
    void this.poll();
    this.timer=setInterval(()=>void this.poll(),this.interval);
    this.timer.unref?.();
  }
  async poll() {
    if(this.busy || this.stopped)return;
    this.busy=true;
    try {
      const active=await this.probe();
      if(!this.stopped && active!==this.active) {
        this.active=active; this.onChange(active);
      }
    } catch {
      // A failed query must not leave the avatar dancing indefinitely.
      if(!this.stopped && this.active) {this.active=false;this.onChange(false);}
    } finally {this.busy=false;}
  }
  stop() {this.stopped=true;clearInterval(this.timer);}
}
module.exports={MusicMonitor,probeWindowsMusic};
