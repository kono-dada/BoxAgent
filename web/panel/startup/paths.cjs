const path = require('node:path');

function resolvePaths({ packaged = false, resources = process.resourcesPath, panel = path.resolve(__dirname, '..'), appData, env = process.env }) {
  const root = packaged ? path.join(resources, 'boxagent') : path.resolve(panel, '../..');
  const data = path.resolve(env.BOXAGENT_DATA_DIR || (packaged ? path.join(appData, 'BoxAgent') : path.join(root, '.runtime/pet')));
  const runtime = packaged ? path.join(data, 'runtimes') : path.join(root, '.runtime');
  return { packaged, root, panel, data, runtime,
    pythonSource: packaged ? root : path.join(root, 'src'),
    resources: packaged ? resources : path.join(root, '.runtime/package-resources'),
    python: env.BOXAGENT_DESKTOP_PYTHON || path.join(runtime, 'core', 'bin/python3'),
    jevPython: env.BOXAGENT_JEV_MEM_PYTHON || path.join(runtime, packaged ? 'jev' : 'jev-mem-venv', 'bin/python3'),
    visionPython: env.BOXAGENT_PERCEPTION_PYTHON || path.join(runtime, packaged ? 'vision' : 'vision-venv', 'bin/python3'),
    model: env.BOXAGENT_PERCEPTION_MODEL || path.join(packaged ? data : runtime, 'models/qwen3.5-0.8b-mlx'),
  };
}
module.exports = { resolvePaths };
