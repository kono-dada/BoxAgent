const { spawn } = require('node:child_process');

function run(executable, args, { env = process.env, cwd, signal, timeout = 120000, progress = () => {} } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, args, { cwd, env, stdio: ['ignore', 'pipe', 'pipe'], signal });
    let output = '', settled = false, forced, failure;
    const finish = (error) => {
      if (settled) return; settled = true;
      clearTimeout(timer); clearTimeout(forced);
      error ? reject(error) : resolve(output);
    };
    // Never return raw subprocess stderr: it may contain provider credentials.
    const stop = () => {
      child.kill('SIGTERM');
      if (!forced) forced = setTimeout(() => child.kill('SIGKILL'), 2000);
    };
    const timer = setTimeout(() => { failure = Error('component_process_timeout'); stop(); }, timeout);
    signal?.addEventListener('abort', stop, { once: true });
    child.stdout.on('data', data => {
      output += data.toString();
      if (output.length > 1024 * 1024) { output = ''; failure = Error('process_output_limit'); stop(); }
    });
    child.stderr.on('data', () => progress());
    child.on('error', error => {
      if (error.name === 'AbortError' && child.pid) return; // Reap the cancelled child before settling.
      finish(Error(signal?.aborted ? 'cancelled' : 'process_start_failed'));
    });
    child.on('close', code => {
      signal?.removeEventListener('abort', stop);
      finish(signal?.aborted ? Error('cancelled') : failure || (code === 0 ? null : Error('component_process_failed')));
    });
  });
}
module.exports = { run };
