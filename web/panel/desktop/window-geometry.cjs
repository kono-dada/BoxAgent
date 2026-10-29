// Keep requested DIP sizes separate from native, DPI-rounded window bounds.
// At fractional Windows scaling, feeding getBounds() back through setPosition()
// can grow a frameless window on every move.
class WindowGeometry {
  constructor() {
    this.sizes = new WeakMap();
  }
  remember(window, size) {
    this.sizes.set(window, { width: size.width, height: size.height });
  }
  getBounds(window) {
    return { ...window.getBounds(), ...this.sizes.get(window) };
  }
  setBounds(window, bounds) {
    const size = this.sizes.get(window) || window.getBounds();
    const frame = {
      ...bounds,
      width: bounds.width ?? size.width,
      height: bounds.height ?? size.height,
    };
    this.remember(window, frame);
    window.setBounds(frame);
  }
  setPosition(window, x, y) {
    this.setBounds(window, { x: Math.round(x), y: Math.round(y) });
  }
}
module.exports = { WindowGeometry };
