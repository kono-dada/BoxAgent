async function validateProviders(config, { fetcher = fetch, signal } = {}) {
  const result = { qwen: { state: 'missing', code: 'qwen_configuration_missing' }, task: { state: 'missing', code: 'task_configuration_missing' } };
  const workspace = config.BOXAGENT_DASHSCOPE_WORKSPACE_ID || '';
  if (config.DASHSCOPE_API_KEY && /^[a-zA-Z0-9-]{1,128}$/.test(workspace)) {
    try {
      const response = await fetcher(`https://${workspace}.cn-beijing.maas.aliyuncs.com/api/v1/webrtc/realtime?model=qwen3.8-omni-flash-realtime`, {
        method: 'POST', body: '{}', headers: { Authorization: `Bearer ${config.DASHSCOPE_API_KEY}`, 'Content-Type': 'application/json', 'x-dashscope-rtc-transport': 'moq' },
        signal: AbortSignal.any([signal || new AbortController().signal, AbortSignal.timeout(15000)]),
      });
      if (!response.ok) result.qwen = { state: 'invalid', code: [401, 403].includes(response.status) ? 'qwen_auth_failed' : 'qwen_service_failed' };
      else {
        const body = await response.json();
        result.qwen = body?.aoqTokenForClient && body?.sid ? { state: 'ready', code: '' } : { state: 'invalid', code: 'qwen_response_invalid' };
      }
    } catch { result.qwen = { state: 'unavailable', code: 'provider_network_failed' }; }
  }
  if ((config.BOXAGENT_TASK_PROVIDER || 'deepseek') === 'deepseek' && config.DEEPSEEK_API_KEY) {
    try {
      const response = await fetcher('https://api.deepseek.com/models', { headers: { Authorization: `Bearer ${config.DEEPSEEK_API_KEY}` },
        signal: AbortSignal.any([signal || new AbortController().signal, AbortSignal.timeout(15000)]) });
      const body = response.ok ? await response.json() : null;
      result.task = response.ok && Array.isArray(body?.data) ? { state: 'ready', code: '' } :
        { state: 'invalid', code: [401, 403].includes(response.status) ? 'deepseek_auth_failed' : 'task_service_failed' };
    } catch { result.task = { state: 'unavailable', code: 'provider_network_failed' }; }
  }
  return result;
}
module.exports = { validateProviders };
