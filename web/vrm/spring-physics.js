import * as THREE from 'three';

// 对齐 AvatarGravityController 与 CustomVRM.prefab；不按角色或头发长度修改物理参数。
export class SpringPhysics {
  constructor(vrm, options = {}) {
    this.version = vrm.meta.metaVersion;
    this.impactMultiplier = options.impactMultiplier ?? 0.35;
    this.pending = new THREE.Vector3();
    this.force = new THREE.Vector3();
    this.entries = [...(vrm.springBoneManager?.joints || [])].map(joint => ({
      joint, power:joint.settings.gravityPower, direction:joint.settings.gravityDir.clone(),
    }));
    // 模型转向后重新建立世界空间尾端历史。
    vrm.scene.updateMatrixWorld(true);
    vrm.springBoneManager?.reset();
  }
  interact({kind, velocity = [0,0]}) {
    if (kind === 'drag-move' || kind === 'drag-start') {
      // macOS 屏幕 Y 向上，与 GetWindowRect 的 Y 相反。
      this.pending.set(-velocity[0], -velocity[1], 0);
    } else if (kind === 'drag-end') this.pending.set(0,0,0);
  }
  beginFrame() {
    this.force.copy(this.pending).normalize().multiplyScalar(this.impactMultiplier);
    this.pending.set(0,0,0);
    for (const {joint,power,direction} of this.entries) {
      const settings = joint.settings;
      // UniVRM 0 外力叠加作者重力；原版 VRM 1 分支覆盖重力向量。
      settings.gravityDir.copy(this.force);
      if (this.version === '0') settings.gravityDir.addScaledVector(direction,power);
      settings.gravityPower = settings.gravityDir.length();
      if (settings.gravityPower > 0) settings.gravityDir.normalize();
    }
  }
  debug() { return {joints:this.entries.length,impactMultiplier:this.impactMultiplier,externalForce:this.force.toArray()}; }
  dispose() {
    for (const {joint,power,direction} of this.entries) {
      joint.settings.gravityPower = power;
      joint.settings.gravityDir.copy(direction);
    }
  }
}
