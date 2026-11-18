const { test } = require('node:test');
const assert = require('node:assert/strict');
const { Permissions } = require('../startup/permissions.cjs');

function fixture(requestScreen) {
  const calls = [], statuses = { screen: 'not-determined', microphone: 'not-determined', accessibility: false };
  const permissions = new Permissions({
    getMediaAccessStatus: key => statuses[key],
    isTrustedAccessibilityClient: prompt => { if (prompt) calls.push('accessibility-request'); return statuses.accessibility; },
    askForMediaAccess: async key => { calls.push(key); statuses[key] = 'granted'; },
  }, { openExternal: async url => calls.push(url) }, { requestScreen: async () => {
    calls.push('screen-request');
    await requestScreen?.(statuses);
  } });
  return { permissions, calls, statuses };
}

test('snapshot never requests permission and screen click requests from the app before settings', async () => {
  const { permissions, calls } = fixture();
  permissions.snapshot();
  assert.deepEqual(calls, []);
  const result = await permissions.request('screen');
  assert.equal(result.screen, 'not-determined');
  assert.equal(calls[0], 'screen-request');
  assert.match(calls[1], /Privacy_ScreenCapture$/);
});
test('already granted screen permission does not capture or open settings', async () => {
  const { permissions, calls, statuses } = fixture();
  statuses.screen = 'granted';
  await permissions.request('screen');
  assert.deepEqual(calls, []);
});
test('granting during request returns the new status without opening settings', async () => {
  const { permissions, calls } = fixture(statuses => { statuses.screen = 'granted'; });
  assert.equal((await permissions.request('screen')).screen, 'granted');
  assert.deepEqual(calls, ['screen-request']);
});
test('denied or failed requests keep a settings route and can be retried', async () => {
  const { permissions, calls, statuses } = fixture(() => { throw Error('screen capture denied'); });
  statuses.screen = 'denied';
  await permissions.request('screen');
  await permissions.request('screen');
  assert.equal(calls.filter(call => call === 'screen-request').length, 2);
  assert.equal(calls.filter(call => call.endsWith('Privacy_ScreenCapture')).length, 2);
});
test('a stalled native screen picker does not block onboarding or spawn duplicate captures', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { permissions, calls } = fixture(() => new Promise(() => {}));
  const first = permissions.request('screen'), second = permissions.request('screen');
  await Promise.resolve();
  t.mock.timers.tick(2500);
  await Promise.all([first, second]);
  assert.equal(calls.filter(call => call === 'screen-request').length, 1);
  assert.equal(calls.filter(call => call.endsWith('Privacy_ScreenCapture')).length, 2);
});
test('accessibility and microphone requests only happen on their explicit actions', async () => {
  const { permissions, calls } = fixture();
  await permissions.request('accessibility');
  assert.equal(calls[0], 'accessibility-request');
  assert.match(calls[1], /Privacy_Accessibility$/);
  assert.equal((await permissions.request('microphone')).microphone, 'granted');
  await assert.rejects(permissions.request('invalid'), /invalid_permission/);
});
