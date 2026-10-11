import * as THREE from 'three';
const rad = THREE.MathUtils.degToRad;
const clamp = THREE.MathUtils.clamp;
export const TRACKING_DEFAULTS = Object.freeze({headYawLimit:45,headPitchLimit:30,headSmoothness:10,
  spineMinRotation:-15,spineMaxRotation:15,spineSmoothness:25,spineFadeSpeed:5,
  eyeYawLimit:12,eyePitchLimit:12,eyeSmoothness:10,headBlend:0.7,spineBlend:0.5,eyeBlend:1});

// 对齐 AvatarMouseTracking 的三层跟随；在动画之后、弹簧骨之前运行。
export class MateTracking {
  constructor(vrm, camera, viewport, settings = {}) {
    this.vrm=vrm; this.camera=camera; this.viewport=viewport;
    this.settings={...TRACKING_DEFAULTS,...settings};
    this.headDriver=new THREE.Quaternion();this.spineDriver=new THREE.Quaternion();this.eyeDriver=new THREE.Quaternion();
    this.spineWeight=0;
  }
  node(name){return this.vrm.humanoid.getNormalizedBoneNode(name);}
  target(node, world, yawLimit, pitchLimit) {
    const dir=world.clone().sub(node.getWorldPosition(new THREE.Vector3())).normalize();
    dir.applyQuaternion(node.parent.getWorldQuaternion(new THREE.Quaternion()).invert());
    // VRM 0 标准人形朝 -Z，转换到跟随器使用的 +Z 方向。
    if(this.vrm.meta.metaVersion==='0'){dir.x*=-1;dir.z*=-1;}
    const yaw=clamp(Math.atan2(dir.x,dir.z),-rad(yawLimit),rad(yawLimit));
    const pitch=clamp(Math.asin(clamp(dir.y,-1,1)),-rad(pitchLimit),rad(pitchLimit));
    const q=new THREE.Quaternion().setFromEuler(new THREE.Euler(-pitch,yaw,0,'YXZ'));
    if(this.vrm.meta.metaVersion==='0'){q.x*=-1;q.z*=-1;}
    return q;
  }
  apply(dt,pointer,allowed=true) {
    const s=this.settings,{width,height}=this.viewport();
    const p=pointer || [0,0];
    const world=new THREE.Vector3(p[0]*2/width,p[1]*2/height,-1).unproject(this.camera);
    const head=this.node('head');
    if(allowed && head){
      this.headDriver.slerp(this.target(head,world,s.headYawLimit,s.headPitchLimit),Math.min(1,dt*s.headSmoothness));
      const base=head.quaternion.clone();
      head.quaternion.slerp(this.headDriver.clone().multiply(base),s.headBlend);
    }
    this.spineWeight=clamp(this.spineWeight+(allowed?1:-1)*dt*s.spineFadeSpeed,0,1);
    const yaw=-rad(THREE.MathUtils.lerp(s.spineMinRotation,s.spineMaxRotation,clamp(p[0]/width+0.5,0,1)));
    this.spineDriver.slerp(new THREE.Quaternion().setFromAxisAngle(THREE.Object3D.DEFAULT_UP,yaw),Math.min(1,dt*s.spineSmoothness));
    for(const [name,factor] of [['spine',1],['chest',0.8],['upperChest',0.6]]){
      this.node(name)?.quaternion.premultiply(new THREE.Quaternion().slerp(this.spineDriver,this.spineWeight*s.spineBlend*factor));
    }
    if(allowed){
      const eye=this.node('leftEye') || this.node('rightEye');
      if(eye){
        this.eyeDriver.slerp(this.target(eye,world,s.eyeYawLimit,s.eyePitchLimit),Math.min(1,dt*s.eyeSmoothness));
        for(const name of ['leftEye','rightEye'])this.node(name)?.quaternion.slerp(this.eyeDriver,s.eyeBlend);
      }
    }
  }
}
