import * as THREE from 'three';
import { VRMHumanBoneParentMap } from '@pixiv/three-vrm';

// 旧 Unity 导出包把 parent 索引写进 children，断开的骨骼保存了世界旋转。
// 只适配该格式的明确结构特征，标准 VRMA 完全不改；不依赖目标角色。
export function repairMateAnimation(animation, json) {
  const bones = json.extensions?.VRMC_vrm_animation?.humanoid?.humanBones;
  if (json.asset?.generator !== 'MateEngineAnimToVrma (Unity Editor)' || !bones) return [];
  const parentOf = name => {
    let parent = VRMHumanBoneParentMap[name];
    while (parent && !bones[parent]) parent = VRMHumanBoneParentMap[parent];
    return parent;
  };
  const reversed = Object.keys(bones).filter(name => {
    const parent = parentOf(name);
    return parent && json.nodes[bones[name].node].children?.includes(bones[parent].node);
  });
  if (reversed.length < 5) return [];
  const rotations = animation.humanoidTracks.rotation;
  const original = new Map([...rotations].map(([name, track]) => [name,track.clone()]));
  const interpolants = new Map([...original].map(([name,track]) => [name,track.createInterpolant()]));
  const detached = new Set(Object.keys(bones).filter(name => parentOf(name) &&
    !(json.nodes[bones[name].node].children || []).includes(bones[parentOf(name)].node)));
  const worldAt = (name,time) => {
    if (!name) return new THREE.Quaternion();
    const q = interpolants.has(name) ? new THREE.Quaternion().fromArray(interpolants.get(name).evaluate(time)) : new THREE.Quaternion();
    return detached.has(name) ? q : worldAt(parentOf(name),time).multiply(q);
  };
  const repaired = [];
  for (const name of detached) {
    const track = rotations.get(name);
    if (!track) continue;
    const values = new Float32Array(track.values.length);
    track.times.forEach((time,index) => {
      worldAt(parentOf(name),time).invert().multiply(worldAt(name,time)).normalize().toArray(values,index*4);
    });
    track.values = values;
    repaired.push(name);
  }
  return repaired;
}
