const { test } = require('node:test');
const assert = require('node:assert/strict');
const { LocalPermissions } = require('../desktop/local-permissions.cjs');

function fixture(snapshot = { microphone: 'granted', speech: 'denied', screen: 'granted', accessibility: true }) {
  const requests = [], records = [], dialogs = [];
  const controller = new LocalPermissions({
    permissions: { snapshot: () => snapshot, refresh: async () => snapshot,
      request: async key => { requests.push(key); }, close: () => {} },
    record: value => records.push(value),
    dialog: { showMessageBox: async options => { dialogs.push(options); return { response: 5 }; } },
  });
  return { controller, requests, records, dialogs };
}

test('source permission refresh records real state without requesting any authorization', async () => {
  const { controller, requests, records } = fixture();
  assert.equal((await controller.refresh()).speech, 'denied');
  assert.equal(controller.missing(), true);
  assert.deepEqual(requests, []);
  assert.equal(records.length, 1);
});

test('closing a local review never starts onboarding or requests permissions', async () => {
  const { controller, requests, dialogs } = fixture();
  await controller.show();
  assert.match(dialogs[0].detail, /语音识别：未授权/);
  assert.deepEqual(requests, []);
});

test('explicit selection requests only that permission and returns to system settings', async () => {
  const { controller, requests } = fixture();
  controller.dialog.showMessageBox = async () => ({ response: 1 });
  await controller.show();
  assert.deepEqual(requests, ['speech']);
});

test('repeated open requests share one dialog while it is visible', async () => {
  const { controller } = fixture();
  let finish;
  controller.dialog.showMessageBox = () => new Promise(resolve => { finish = resolve; });
  const first = controller.show(), second = controller.show();
  assert.equal(first, second);
  await new Promise(resolve => setImmediate(resolve));
  finish({ response: 5 });
  await first;
});

test('all four grants are required for a complete permission report', () => {
  const { controller } = fixture({ microphone: 'granted', speech: 'granted', screen: 'granted', accessibility: true });
  assert.equal(controller.missing(), false);
});
