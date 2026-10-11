import { MateTracking } from './mate-tracking.js';
import * as THREE from 'three';
import { PetBehavior } from './behavior.js';
import { ExpressionAdapter } from './expressions.js';

// 通用 VRM 人形动画层：动作状态、视线和附加摆动分别控制，不识别角色名称。
export class AvatarAnimator {
  constructor(vrm, scene, box, bank, manifest, camera, viewport) {
    this.tracking = camera ? new MateTracking(vrm,camera,viewport,manifest.trackingSettings) : null;
    this.vrm = vrm;
    this.bank = bank;
    this.behavior = new PetBehavior(Math.random, {idleActions:bank.idleActions(),roles:bank.roles,settings:manifest.behaviorSettings});
    this.expressions = new ExpressionAdapter(vrm, manifest.expressionAliases);
    this.height = box.max.y - box.min.y;
    const center = box.getCenter(new THREE.Vector3());
    this.bones = {};
    this.rest = {};
    this.base = {};
    vrm.scene.updateMatrixWorld(true);
    for (const [name, bone] of Object.entries(vrm.humanoid.normalizedHumanBones)) {
      this.bones[name] = bone.node;
      this.rest[name] = bone.node.quaternion.clone();
    }
    // 从上臂到前臂的真实方向计算自然垂手姿势，不假定 T/A pose 或四肢比例。
    for (const side of ['left', 'right']) {
      const arm = this.bones[`${side}UpperArm`], elbow = this.bones[`${side}LowerArm`];
      if (!arm || !elbow) continue;
      const a = arm.getWorldPosition(new THREE.Vector3()), b = elbow.getWorldPosition(new THREE.Vector3());
      const current = b.sub(a).normalize();
      const outward = new THREE.Vector3(current.x, 0, current.z).normalize();
      const desired = outward.multiplyScalar(0.22).add(new THREE.Vector3(0, -1, 0)).normalize();
      const world = arm.getWorldQuaternion(new THREE.Quaternion());
      const parent = arm.parent.getWorldQuaternion(new THREE.Quaternion());
      this.rest[`${side}UpperArm`] = new THREE.Quaternion().setFromUnitVectors(current, desired).multiply(world).premultiply(parent.invert());
    }
    const tracks = [];
    for (const [name, bone] of Object.entries(this.bones)) {
      const values = this.rest[name].toArray();
      tracks.push(new THREE.QuaternionKeyframeTrack(`${bone.name}.quaternion`, [0, 1], [...values, ...values]));
      bone.quaternion.copy(this.rest[name]);
      this.base[name] = this.rest[name].clone();
    }
    this.mixer = new THREE.AnimationMixer(vrm.scene);
    this.restClip = new THREE.AnimationClip('rest', 1, tracks);
    this.current = 'rest';
    this.active = this.mixer.clipAction(this.restClip).play();
    this.quaternion = new THREE.Quaternion();
    this.euler = new THREE.Euler();
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 128;
    const context = canvas.getContext('2d');
    const gradient = context.createRadialGradient(64, 64, 2, 64, 64, 62);
    gradient.addColorStop(0, 'rgba(25,31,40,0.24)');
    gradient.addColorStop(0.45, 'rgba(25,31,40,0.12)');
    gradient.addColorStop(1, 'rgba(25,31,40,0)');
    context.fillStyle = gradient; context.fillRect(0, 0, 128, 128);
    this.shadow = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(canvas), depthWrite: false }));
    this.shadow.position.set(center.x, box.min.y - this.height * 0.006, center.z + this.height * 0.05);
    // 原版没有此程序生成的地面阴影，默认隐藏，仅兼容显式开启。
    this.shadow.visible = manifest.contactShadow === true;
    scene.add(this.shadow);
    if (vrm.lookAt) vrm.lookAt.autoUpdate = false;
  }
  play(name, loop = false, kind = 'idle') {
    if (name === this.current) return;
    const clip = name === 'rest' ? this.restClip : this.bank.clips.get(name);
    if (!clip) { if (this.current !== 'rest') this.play('rest'); return; }
    const next = this.mixer.clipAction(clip);
    next.reset().setLoop(loop || name === 'rest' ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
    next.clampWhenFinished = true;
    next.enabled = true;
    const settings = this.bank.settings?.(name) || {timeScale:1,cycleOffset:0};
    next.setEffectiveWeight(1).setEffectiveTimeScale(settings.timeScale).play();
    next.time = settings.cycleOffset * clip.duration;
    const idleBlend = kind === 'idle' && this.currentKind === 'idle';
    const duration = idleBlend ? (this.behavior.idleIndex === 0 ? 0 : this.behavior.settings.idleTransitionTime) : this.behavior.settings.dragTransitionTime;
    // Idle BlendTree 共用归一化播放相位；Drag 进入时从动作起点播放。
    if (idleBlend) next.time = (this.active.time % this.active.getClip().duration) / this.active.getClip().duration * clip.duration;
    next.crossFadeFrom(this.active, duration, false);
    this.currentKind = kind;
    this.active = next;
    this.current = name;
  }
  add(name, angles, weight = 1) {
    const node = this.bones[name];
    if (!node) return;
    this.euler.set(angles[0] * weight, angles[1] * weight, angles[2] * weight, 'XYZ');
    this.quaternion.setFromEuler(this.euler);
    if (this.vrm.meta.metaVersion === '0') { this.quaternion.x *= -1; this.quaternion.z *= -1; }
    node.quaternion.multiply(this.quaternion);
  }
  update(dt) {
    const b = this.behavior.step(dt), h = this.height;
    const motion = this.behavior.motion();
    this.play(motion.name,motion.loop,motion.kind);
    // 清除上一帧附加旋转，再求值动画；对应原版 Update → Animator → LateUpdate。
    for (const [name, node] of Object.entries(this.bones)) node.quaternion.copy(this.base[name]);
    this.mixer.update(dt);
    for (const [name, node] of Object.entries(this.bones)) {
      this.base[name].copy(node.quaternion);
    }
    this.vrm.scene.updateMatrixWorld(true);
    this.tracking?.apply(dt,this.behavior.pointer,!this.behavior.sleep);
    // 原版 AvatarSwayController 在动画 hips.localRotation 后乘摆动，不改写四肢动作。
    this.add('hips',[b.pitch,0,b.roll]);
    this.shadow.scale.set(h * 0.34,h * 0.045,1);
    this.shadow.material.opacity = 1 - b.drag * 0.65;
    this.expressions.update(b);
  }
  afterUpdate() { this.expressions.flush(); }
  debug() {
    return { ...this.behavior.debug(), action: this.current, actionTime:this.active.time, actionSpeed:this.active.getEffectiveTimeScale(), actionLoop:this.active.loop, transitionKind:this.currentKind, idleActions:this.bank.idleActions().map(x => x.name),
      expressions: this.expressions.debug(), rejectedClips:this.bank.rejected, animationRepairs:this.bank.repairs, boneCount:Object.keys(this.bones).length };
  }
  dispose() {
    this.mixer.stopAllAction();
    this.mixer.uncacheRoot(this.vrm.scene);
    this.shadow.material.map.dispose(); this.shadow.material.dispose();
    this.shadow.removeFromParent();
  }
}
