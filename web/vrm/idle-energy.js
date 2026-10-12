import {Quaternion, Vector3} from 'three';

// 近似可见运动消耗，不是生理热量；躯干和近端关节权重更高，手指不参与。
export const BONE_WEIGHTS = Object.freeze({hips:3,spine:2,chest:2,upperChest:2,neck:0.5,head:1,
  leftShoulder:0.5,rightShoulder:0.5,leftUpperArm:1.5,rightUpperArm:1.5,
  leftLowerArm:0.75,rightLowerArm:0.75,leftHand:0.25,rightHand:0.25,
  leftUpperLeg:2,rightUpperLeg:2,leftLowerLeg:1,rightLowerLeg:1,
  leftFoot:0.5,rightFoot:0.5,leftToes:0.25,rightToes:0.25});
const totalWeight = Object.values(BONE_WEIGHTS).reduce((a,b)=>a+b,0);
export const DEFAULT_IDLE_TEMPERATURE = 0.8;

export function measureIdleEnergy(animation, timeScale = 1, loop = true) {
  const duration = animation.duration / timeScale;
  if (!(duration > 0) || !Number.isFinite(duration)) throw new Error('耗能计算需要有效动作时长');
  // 以实际播放时间等距采样，避免不同关键帧密度改变分数；长动作最多采样 3600 段。
  const segments = Math.max(4,Math.min(3600,Math.ceil(duration*30))), dt = duration/segments;
  let amplitude = 0, speed = 0, peak = 0;
  const analyze = (track, rotational, weight, scale = 1) => {
    const interpolant = track.createInterpolant();
    const samples = Array.from({length:segments+1},(_,i)=> {
      const value = interpolant.evaluate(i/segments*animation.duration);
      return rotational ? new Quaternion().fromArray(value).normalize() : new Vector3().fromArray(value).multiplyScalar(scale);
    });
    let center;
    if (rotational) {
      const sum=[0,0,0,0], reference=samples[0];
      for (const q of samples) q.toArray().forEach((v,i)=>sum[i]+=v*(q.dot(reference)<0?-1:1));
      center=new Quaternion().fromArray(sum).normalize();
    } else center=samples.reduce((sum,v)=>sum.add(v),new Vector3()).divideScalar(samples.length);
    const distance = (a,b) => rotational ? a.angleTo(b) : a.distanceTo(b);
    let variance=0, maximum=0, velocity=0;
    for(let i=0;i<samples.length;i++) {
      const offset=distance(samples[i],center);variance+=offset**2;maximum=Math.max(maximum,offset);
      if(i)velocity+=(distance(samples[i],samples[i-1])/dt)**2;
    }
    // 循环末尾的姿势跳变也会产生可见动作，按一个采样间隔计入速度。
    if(loop)velocity+=(distance(samples.at(-1),samples[0])/dt)**2;
    const radius=rotational?0.35:0.05, rate=rotational?0.5:0.1;
    amplitude+=weight*variance/samples.length/radius**2;
    peak+=weight*maximum**2/radius**2;
    speed+=weight*velocity/segments/rate**2;
  };
  for(const [name,track] of animation.humanoidTracks.rotation) {
    if(BONE_WEIGHTS[name])analyze(track,true,BONE_WEIGHTS[name]/totalWeight);
  }
  const hips=animation.humanoidTracks.translation.get('hips');
  if(hips)analyze(hips,false,1,1/Math.max(0.001,Math.abs(animation.restHipsPosition.y)));
  return {energy:amplitude+0.25*peak+0.25*speed,amplitude,peak,speed,duration};
}

// Boltzmann 分布：保持重复抽到轻微动作的可能，避免排除当前动作后放大大动作概率。
export function idleDistribution(actions, temperature = DEFAULT_IDLE_TEMPERATURE) {
  if(!actions.length)return [];
  const energies=actions.map(item=>Number.isFinite(item.energy) && item.energy>=0?item.energy:Infinity);
  const minimum=Math.min(...energies);
  const t=Number.isFinite(temperature) && temperature>0?temperature:DEFAULT_IDLE_TEMPERATURE;
  const weights=energies.map(e=>minimum===Infinity?1:Math.exp(-(e-minimum)/t));
  const total=weights.reduce((a,b)=>a+b,0);
  return actions.map((item,i)=>({...item,probability:weights[i]/total}));
}

export function sampleIdle(distribution, random) {
  let remaining=random();
  for(let i=0;i<distribution.length;i++) {
    remaining-=distribution[i].probability;
    if(remaining<0)return i;
  }
  return Math.max(0,distribution.length-1);
}
