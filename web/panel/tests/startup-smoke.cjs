// Actual launch, isolated storage and offline sockets; no product mutations.
const { app } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');
const second = process.argv.includes('--second-open');
const directory = process.env.BOXAGENT_STARTUP_FIXTURE || fs.mkdtempSync(path.join(os.tmpdir(), 'boxagent-startup-'));
process.env.BOXAGENT_DATA_DIR = path.join(directory, 'offline-data');
app.setPath('userData', path.join(directory, 'preferences'));
const timeout = setTimeout(() => { console.error('Panel startup fixture timed out'); app.exit(1); }, 15000);
if (!second) app.once('browser-window-created', (_event, window) => {
  let loads = 0;
  window.webContents.on('did-finish-load', () => { loads++; });
  window.webContents.once('did-finish-load', async () => {
    try {
      const coldLoadMs = Date.now() - Number(process.env.BOXAGENT_LAUNCH_STARTED || Date.now());
      assert(await window.webContents.executeJavaScript('Boolean(document.getElementById("content"))'));
      const identity = window.id;
      await new Promise(resolve => setTimeout(resolve, 150));
      window.hide();
      const started = Date.now();
      const reopened = new Promise(resolve => app.once('second-instance', resolve));
      const child = spawn(process.execPath, [__filename, '--second-open'], {
        env: { ...process.env, BOXAGENT_STARTUP_FIXTURE: directory }, stdio: ['ignore', 'pipe', 'pipe'] });
      child.stdout.on('data', chunk => process.stdout.write(chunk));
      child.stderr.on('data', chunk => process.stderr.write(chunk));
      await reopened;
      assert(window.isVisible());
      assert.equal(window.id, identity);
      assert.equal(loads, 1);
      console.log(JSON.stringify({ coldLoadMs, reopenMs: Date.now() - started,
        checks: ['built page loads directly', 'second launch shows same window', 'second launch does not reload page'] }));
      child.on('exit', () => {});
      clearTimeout(timeout); app.quit();
    } catch (error) { console.error(error); app.exit(1); }
  });
});
require('../main.cjs');
app.on('will-quit', () => clearTimeout(timeout));
