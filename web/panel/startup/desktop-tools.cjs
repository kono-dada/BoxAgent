const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const clientRelative = 'Contents/SharedSupport/SkyComputerUseClient.app/Contents/MacOS/SkyComputerUseClient';
async function regularExecutable(file) {
  try { const stat = await fs.stat(file); return stat.isFile() && Boolean(stat.mode & 0o111); } catch { return false; }
}
async function detectDesktopTools(root, config = {}, { home = os.homedir(), codexHome = process.env.CODEX_HOME || path.join(home, '.codex'),
  applications = ['/Applications', path.join(home, 'Applications')] } = {}) {
  const candidates = [config.BOXAGENT_CODEX_BIN, path.join(root, '.runtime/codex-0.153.0/codex'),
    path.join(codexHome, 'plugins/.plugin-appserver/codex-cli/bin/codex'),
    path.join(home, '.local/bin/codex'), ...applications.map(directory => path.join(directory, 'Codex.app/Contents/Resources/codex'))].filter(Boolean);
  const codex = (await Promise.all(candidates.map(async file => await regularExecutable(file) &&
    await regularExecutable(path.join(path.dirname(file), 'codex-code-mode-host')) ? file : null))).find(Boolean);
  const client = config.BOXAGENT_COMPUTER_USE_CLIENT || path.join(codexHome, 'computer-use/Codex Computer Use.app', clientRelative);
  return { codex: codex || null, client: await regularExecutable(client) ? client : null };
}
async function chooseDesktopTool(kind, directory) {
  if (kind === 'codex') {
    for (const file of [path.join(directory, 'codex'), path.join(directory, 'Contents/Resources/codex')])
      if (await regularExecutable(file) && await regularExecutable(path.join(path.dirname(file), 'codex-code-mode-host'))) return { BOXAGENT_CODEX_BIN: file };
  } else if (kind === 'computer-use') {
    const file = path.join(directory, clientRelative);
    if (await regularExecutable(file)) return { BOXAGENT_COMPUTER_USE_CLIENT: file };
  }
  throw Error('desktop_tool_invalid');
}
module.exports = { detectDesktopTools, chooseDesktopTool };
