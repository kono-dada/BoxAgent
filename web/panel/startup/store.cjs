const fs = require('node:fs/promises');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const STEPS = ['welcome', 'providers', 'components', 'permissions', 'wechat', 'ready'];
const defaults = () => ({ version: 1, completed: false, step: 'welcome', completedAt: null });

async function readJson(file, fallback) {
  try { return JSON.parse(await fs.readFile(file, 'utf8')); }
  catch (error) {
    if (error.code === 'ENOENT') return fallback;
    // A corrupt configuration must be repairable, never silently overwritten.
    throw new Error('configuration_unreadable');
  }
}
async function atomicWrite(file, value) {
  await fs.mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
  const temporary = `${file}.${randomUUID()}.tmp`;
  try {
    const handle = await fs.open(temporary, 'wx', 0o600);
    try { await handle.writeFile(`${JSON.stringify(value)}\n`); await handle.sync(); }
    finally { await handle.close(); }
    await fs.rename(temporary, file);
  } finally { await fs.rm(temporary, { force: true }); }
}
async function readState(file) {
  const state = await readJson(file, defaults());
  if (state?.version !== 1 || typeof state.completed !== 'boolean' || !STEPS.includes(state.step) ||
      !(state.completedAt === null || typeof state.completedAt === 'string')) throw Error('configuration_unreadable');
  if (state.permissionReviewIdentity !== undefined &&
      (typeof state.permissionReviewIdentity !== 'string' || !/^[a-f0-9]{64}$/.test(state.permissionReviewIdentity)))
    throw Error('configuration_unreadable');
  return { version: 1, completed: state.completed, step: state.step, completedAt: state.completedAt,
    ...(state.permissionReviewIdentity ? { permissionReviewIdentity: state.permissionReviewIdentity } : {}) };
}
module.exports = { STEPS, defaults, readJson, readState, atomicWrite };
