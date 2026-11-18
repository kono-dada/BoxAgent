const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const { WakeWord } = require('../desktop/wake-word.cjs');

function fixture() {
  const children = [], timers = [];
  const listener = new WakeWord({ onError() {}, onWake() {},
    spawnListener() {
      const child = new EventEmitter();
      child.stdout = new PassThrough(); child.stderr = new PassThrough();
      child.kill = () => { child.killed = true; };
      children.push(child); return child;
    },
    schedule(callback, delay) { const timer = { callback, delay }; timers.push(timer); return timer; },
    cancel(timer) { if (timer) timer.cancelled = true; },
  });
  listener.setName('书书'); listener.sync(true);
  return { listener, children, timers };
}

test('a wake helper failure retries once without permanently disabling wake', () => {
  const { listener, children, timers } = fixture();
  children[0].emit('close', 1);
  assert.equal(listener.enabled, true);
  for (let i = 0; i < 10; i++) listener.sync(true);
  assert.equal(children.length, 1);
  assert.equal(timers[0].delay, 2000);
  timers[0].callback();
  assert.equal(children.length, 2);
  children[1].emit('close', 1);
  assert.equal(timers[1].delay, 4000);
  listener.setEnabled(false);
  assert.equal(timers[1].cancelled, true);
  timers[1].callback();
  assert.equal(children.length, 2);
});

test('starting a voice session cancels a queued wake restart', () => {
  const { listener, children, timers } = fixture();
  children[0].emit('close', 1);
  listener.sync(false);
  assert.equal(timers[0].cancelled, true);
  timers[0].callback();
  assert.equal(children.length, 1);
  listener.sync(true);
  assert.equal(children.length, 2);
});
