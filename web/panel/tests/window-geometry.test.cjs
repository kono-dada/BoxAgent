const test = require('node:test');
const assert = require('node:assert/strict');
const { WindowGeometry } = require('../desktop/window-geometry.cjs');

function roundedWindow(width, height) {
  let actual = { x: 10, y: 20, width: width + 1, height: height + 1 };
  return {
    getBounds: () => ({ ...actual }),
    setBounds: frame => { actual = { ...actual, ...frame, width: frame.width + 1, height: frame.height + 1 }; },
    setPosition: () => { throw new Error('Moving must not reuse native-rounded sizes'); },
  };
}

test('repeated drag gestures preserve intended size despite native DPI rounding', () => {
  const geometry = new WindowGeometry();
  const window = roundedWindow(440, 270);
  geometry.remember(window, { width: 440, height: 270 });
  for (let gesture = 0; gesture < 50; gesture++) {
    const start = geometry.getBounds(window);
    for (let move = 0; move < 30; move++)
      geometry.setPosition(window, start.x + move, start.y + move);
    // Repositioning linked windows must use the same intended size as dragging.
    geometry.setBounds(window, geometry.getBounds(window));
  }
  assert.equal(window.getBounds().width, 441);
  assert.equal(window.getBounds().height, 271);
  assert.equal(geometry.getBounds(window).width, 440);
});

test('content height changes update height while moves preserve width', () => {
  const geometry = new WindowGeometry();
  const window = roundedWindow(440, 270);
  geometry.remember(window, { width: 440, height: 270 });
  geometry.setBounds(window, { x: 30, y: 40, height: 380 });
  for (let index = 0; index < 100; index++) geometry.setPosition(window, index, index);
  assert.deepEqual(geometry.getBounds(window), { x: 99, y: 99, width: 440, height: 380 });
});

test('model crop resize replaces dimensions without affecting other windows', () => {
  const geometry = new WindowGeometry();
  const pet = roundedWindow(252, 182), bubble = roundedWindow(440, 270);
  geometry.remember(pet, { width: 252, height: 182 });
  geometry.remember(bubble, { width: 440, height: 270 });
  geometry.setBounds(pet, { x: 15, y: 25, width: 130, height: 160 });
  geometry.setPosition(pet, 100.4, 200.6);
  assert.deepEqual(geometry.getBounds(pet), { x: 100, y: 201, width: 130, height: 160 });
  assert.equal(geometry.getBounds(bubble).width, 440);
});
