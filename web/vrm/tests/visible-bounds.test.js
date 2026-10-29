import test from 'node:test';
import assert from 'node:assert/strict';
import {visibleBounds} from '../visible-bounds.js';

test('透明空白被裁掉，左下角坐标完整包含所有非透明像素', () => {
  const data = new Uint8ClampedArray(100 * 100 * 4);
  for (const [x, y] of [[35, 10], [65, 85], [40, 40]]) data[(y * 100 + x) * 4 + 3] = 1;
  for (const scale of [0.5, 1, 6]) {
    const bounds = visibleBounds(data, 100, 100, 200 * scale, 200 * scale);
    assert.ok(bounds.width < 200 * scale);
    for (const [x, y] of [[35, 10], [65, 85], [40, 40]]) {
      assert.ok(bounds.x <= x * 2 * scale);
      assert.ok(bounds.x + bounds.width >= (x + 1) * 2 * scale);
      assert.ok(bounds.y <= (99 - y) * 2 * scale);
      assert.ok(bounds.y + bounds.height >= (100 - y) * 2 * scale);
    }
  }
});

test('边缘像素与完全透明的帧不产生无效边界', () => {
  const data = new Uint8ClampedArray(10 * 10 * 4);
  assert.equal(visibleBounds(data, 10, 10, 100, 100), null);
  data[3] = 255; data[data.length - 1] = 255;
  assert.deepEqual(visibleBounds(data, 10, 10, 100, 100), {x:0, y:0, width:100, height:100});
});
