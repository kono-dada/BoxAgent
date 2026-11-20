const { test } = require('node:test');
const assert = require('node:assert/strict');
const { SpeechPermissions } = require('../startup/speech.cjs');
const { Permissions } = require('../startup/permissions.cjs');

function fixture(initial = 'not-determined') {
  const calls = [];
  let value = initial;
  const speech = new SpeechPermissions({ helper: __filename, platform: 'darwin', execute: async (_helper, [flag]) => {
    calls.push(flag);
    if (flag === '--request-speech') value = 'granted';
    return value + '\n';
  } });
  return { speech, calls };
}
test('speech refresh is read-only and never starts audio or authorization', async () => {
  const { speech, calls } = fixture();
  assert.equal(await speech.refresh(), 'not-determined');
  assert.deepEqual(calls, ['--speech-status']);
});
test('concurrent speech actions share one system request', async () => {
  const { speech, calls } = fixture();
  assert.deepEqual(await Promise.all([speech.request(), speech.request()]), ['granted', 'granted']);
  assert.deepEqual(calls, ['--speech-status', '--request-speech']);
});
test('existing grant or denial is not requested again', async () => {
  for (const value of ['granted', 'denied', 'restricted']) {
    const { speech, calls } = fixture(value);
    assert.equal(await speech.request(), value);
    assert.deepEqual(calls, ['--speech-status']);
  }
});
test('missing helper does not break setup or attempt permission requests', async () => {
  const speech = new SpeechPermissions({ helper: __filename + '.missing', platform: 'darwin',
    execute: () => { throw Error('must not execute'); } });
  assert.equal(await speech.refresh(), 'unavailable');
});
test('speech is included in snapshots and explicit grant enables wake without requesting the microphone', async () => {
  const { speech, calls } = fixture();
  let enabled = 0;
  const permissions = new Permissions({ getMediaAccessStatus: () => 'granted', isTrustedAccessibilityClient: () => true },
    { openExternal: () => { throw Error('unexpected settings'); } }, { speech, onVoiceGranted: () => enabled++ });
  assert.equal((await permissions.refresh()).speech, 'not-determined');
  assert.equal((await permissions.request('speech')).speech, 'granted');
  assert.equal(enabled, 1);
  assert.equal(calls.filter(flag => flag === '--request-speech').length, 1);
});
test('denied speech opens the right settings pane without another system request', async () => {
  const { speech, calls } = fixture('denied');
  const opened = [];
  const permissions = new Permissions({ getMediaAccessStatus: () => 'granted', isTrustedAccessibilityClient: () => true },
    { openExternal: async value => opened.push(value) }, { speech });
  await permissions.request('speech');
  assert.match(opened[0], /Privacy_SpeechRecognition$/);
  assert.deepEqual(calls, ['--speech-status']);
});
test('declining the native prompt is respected without opening settings immediately', async () => {
  const opened = [];
  const speech = new SpeechPermissions({ helper: __filename, platform: 'darwin',
    execute: async (_helper, [flag]) => flag === '--request-speech' ? 'denied' : 'not-determined' });
  const permissions = new Permissions({ getMediaAccessStatus: () => 'granted', isTrustedAccessibilityClient: () => true },
    { openExternal: async value => opened.push(value) }, { speech });
  assert.equal((await permissions.request('speech')).speech, 'denied');
  assert.deepEqual(opened, []);
});
test('refresh after system settings can resume wake without another authorization prompt', async () => {
  const { speech, calls } = fixture('granted');
  let resumed = false;
  const permissions = new Permissions({ getMediaAccessStatus: () => 'granted', isTrustedAccessibilityClient: () => true },
    { openExternal() {} }, { speech, onVoiceGranted: () => { resumed = true; } });
  await permissions.refresh();
  assert.equal(resumed, true);
  assert.deepEqual(calls, ['--speech-status']);
});
test('closing setup cancels a pending native permission helper', async () => {
  let began;
  const started = new Promise(resolve => { began = resolve; });
  const speech = new SpeechPermissions({ helper: __filename, platform: 'darwin',
    execute: async (_helper, [flag], { signal }) => {
      if (flag === '--speech-status') return 'not-determined';
      return new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(Error('cancelled')), { once: true });
        began();
      });
    } });
  const request = speech.request();
  await started;
  speech.close();
  await assert.rejects(request, /cancelled/);
});
