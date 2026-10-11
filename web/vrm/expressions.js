// 优先使用 VRM 标准表情；缺失时按常见语义名称发现 morph，配置可补充别名。
const ALIASES = {
  blinkLeft: ['eyeBlinkLeft', 'blink_L', 'Fcl_EYE_Close_L'],
  blinkRight: ['eyeBlinkRight', 'blink_R', 'Fcl_EYE_Close_R'],
  blink: ['blink', 'Fcl_EYE_Close'],
  aa: ['jawOpen', 'mouthOpen', 'Fcl_MTH_A'],
  happy: ['mouthSmileLeft', 'mouthSmileRight', 'Fcl_ALL_Joy'],
  surprised: ['eyeWideLeft', 'eyeWideRight'],
};
const normalize = name => name.toLowerCase().replace(/[^a-z0-9]/g, '');
export class ExpressionAdapter {
  constructor(vrm, overrides = {}) {
    this.manager = vrm.expressionManager;
    this.bindings = {};
    this.direct = [];
    for (const [semantic, aliases] of Object.entries(ALIASES)) {
      const standard = this.manager?.getExpression(semantic);
      if (standard && standard.binds.length) {
        this.bindings[semantic] = { standard: true };
        continue;
      }
      const wanted = (overrides[semantic] || aliases).map(normalize);
      const matches = [];
      vrm.scene.traverse(mesh => {
        if (!mesh.morphTargetDictionary) return;
        for (const [name, index] of Object.entries(mesh.morphTargetDictionary)) {
          if (wanted.includes(normalize(name))) matches.push({ mesh, index });
        }
      });
      if (matches.length) this.bindings[semantic] = { matches };
    }
  }
  set(semantic, value) {
    const binding = this.bindings[semantic];
    if (!binding) return;
    if (binding.standard) this.manager.setValue(semantic, value);
    else this.direct.push({binding, value});
  }
  update(pose) {
    this.direct = [];
    if (this.bindings.blinkLeft && this.bindings.blinkRight) {
      this.set('blinkLeft', pose.blink); this.set('blinkRight', pose.blink);
    } else this.set('blink', pose.blink);
    this.set('aa', pose.jaw);
    this.set('happy', pose.smile);
    this.set('surprised', pose.surprised);
  }
  flush() {
    // VRM 管理器更新后再写非标准 morph，避免被其它表情的清零步骤覆盖。
    for (const {binding, value} of this.direct) {
      for (const {mesh, index} of binding.matches) mesh.morphTargetInfluences[index] = value;
    }
  }
  debug() { return Object.fromEntries(Object.entries(this.bindings).map(([key, value]) => [key, value.standard ? 'vrm' : 'morph'])); }
}
