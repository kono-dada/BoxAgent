const path = require('node:path');
const fs = require('node:fs/promises');
const { run } = require('./process.cjs');
const STATUSES = new Set(['granted', 'denied', 'restricted', 'not-determined', 'unavailable']);

class SpeechPermissions {
  constructor({ helper = path.join(path.dirname(process.execPath), 'BoxAgentWakeListener'),
    execute = run, platform = process.platform } = {}) {
    Object.assign(this, { helper, execute, platform });
    this.status = 'unavailable'; this.refreshing = null; this.requesting = null; this.lastPromptRequested = false;
    this.abort = new AbortController();
  }
  async query(flag) {
    if (this.platform !== 'darwin') return 'unavailable';
    await fs.access(this.helper);
    const value = (await this.execute(this.helper, [flag], {
      timeout: flag === '--request-speech' ? 120000 : 5000, signal: this.abort.signal,
    })).trim();
    if (!STATUSES.has(value)) throw Error('speech_permission_status_invalid');
    this.status = value;
    return value;
  }
  refresh() {
    if (!this.refreshing) this.refreshing = this.query('--speech-status').catch(() => {
      this.status = 'unavailable'; return this.status;
    }).finally(() => { this.refreshing = null; });
    return this.refreshing;
  }
  request() {
    if (!this.requesting) this.requesting = (async () => {
      const status = await this.refresh();
      this.lastPromptRequested = status === 'not-determined';
      return status === 'not-determined' ? this.query('--request-speech') : status;
    })().finally(() => { this.requesting = null; });
    return this.requesting;
  }
  close() { this.abort.abort(); }
}
module.exports = { SpeechPermissions };
