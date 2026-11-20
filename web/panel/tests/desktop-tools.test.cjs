const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { detectDesktopTools } = require('../startup/desktop-tools.cjs');

async function fixture(t) {
  const home = await fs.mkdtemp(path.join(os.tmpdir(), 'boxagent-tools-'));
  t.after(() => fs.rm(home, { recursive: true, force: true }));
  const codexHome = path.join(home, 'custom-codex');
  const root = path.join(home, 'resources');
  const codex = path.join(codexHome, 'plugins/.plugin-appserver/codex-cli/bin/codex');
  const host = path.join(path.dirname(codex), 'codex-code-mode-host');
  const client = path.join(codexHome, 'computer-use/Codex Computer Use.app/Contents/SharedSupport/SkyComputerUseClient.app/Contents/MacOS/SkyComputerUseClient');
  async function executable(file) { await fs.mkdir(path.dirname(file), { recursive: true }); await fs.writeFile(file, '#!/bin/sh\nexit 0\n', { mode: 0o755 }); }
  return { root, codex, host, client, executable, options: { home, codexHome, applications: [] } };
}
test('App Server plugin runtime and Computer Use are automatically found without a Codex.app', async t => {
  const f = await fixture(t);
  await f.executable(f.codex); await f.executable(f.host); await f.executable(f.client);
  assert.deepEqual(await detectDesktopTools(f.root, {}, f.options), { codex: f.codex, client: f.client });
});
test('a CLI alone is insufficient: the executable code mode host is also required', async t => {
  const f = await fixture(t);
  await f.executable(f.codex);
  assert.equal((await detectDesktopTools(f.root, {}, f.options)).codex, null);
  await f.executable(f.host); await fs.chmod(f.host, 0o644);
  assert.equal((await detectDesktopTools(f.root, {}, f.options)).codex, null);
});
test('explicitly selected valid tools take precedence over automatic discovery', async t => {
  const f = await fixture(t);
  const selected = path.join(f.root, 'selected/codex');
  await f.executable(f.codex); await f.executable(f.host);
  await f.executable(selected); await f.executable(path.join(path.dirname(selected), 'codex-code-mode-host'));
  assert.equal((await detectDesktopTools(f.root, { BOXAGENT_CODEX_BIN: selected }, f.options)).codex, selected);
});
