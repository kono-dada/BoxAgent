const fs = require('node:fs');
const { createHash } = require('node:crypto');
const { spawnSync } = require('node:child_process');

// Review bookkeeping only: this never grants access or changes a code requirement.
function permissionIdentity(executable, version, { platform = process.platform, execute = spawnSync } = {}) {
  if (platform !== 'darwin') return '';
  const result = execute('/usr/bin/codesign', ['-d', '-r-', executable], { encoding: 'utf8', timeout: 3000 });
  const requirement = `${result.stdout || ''}\n${result.stderr || ''}`.match(/^#?\s*designated => (.+)$/m)?.[1];
  let value = requirement;
  if (result.status !== 0 || !value) {
    const stat = fs.statSync(executable);
    value = `${executable}:${version}:${stat.size}:${stat.mtimeMs}`;
  }
  return createHash('sha256').update(value).digest('hex');
}
module.exports = { permissionIdentity };
