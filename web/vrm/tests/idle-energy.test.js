import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import * as THREE from 'three';
import {VRMAnimation} from '@pixiv/three-vrm-animation';
import {measureIdleEnergy,idleDistribution,sampleIdle} from '../idle-energy.js';
import {PetBehavior} from '../behavior.js';

function motion(angle=0.2,translation=0,hipsHeight=1) {
  const a=new VRMAnimation();a.duration=2;a.restHipsPosition.y=hipsHeight;
  const values=[0,angle,0].flatMap(v=>new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,0,1),v).toArray());
  a.humanoidTracks.rotation.set('leftUpperArm',new THREE.QuaternionKeyframeTrack('arm',[0,1,2],values));
  a.humanoidTracks.translation.set('hips',new THREE.VectorKeyframeTrack('hips',[0,1,2],
    [0,hipsHeight,0,translation,hipsHeight,0,0,hipsHeight,0]));
  return a;
}
test('静态姿势能量为零，四元数符号与静态偏移不会产生虚假动作',()=>{
  const a=motion(0);assert.equal(measureIdleEnergy(a).energy,0);
  a.humanoidTracks.rotation.get('leftUpperArm').values.set([0,0,0,1,0,0,0,-1,0,0,0,1]);
  assert.equal(measureIdleEnergy(a).energy,0);
  const moving=motion(), shifted=motion();
  shifted.humanoidTracks.translation.get('hips').values.forEach((v,i,values)=>values[i]=v+5);
  assert.ok(Math.abs(measureIdleEnergy(moving).energy-measureIdleEnergy(shifted).energy)<1e-10);
});
test('摆幅翻倍时耗能近似四倍，降低速度只减少速度项',()=>{
  const small=measureIdleEnergy(motion()),large=measureIdleEnergy(motion(0.4));
  assert.ok(Math.abs(large.energy/small.energy-4)<0.01);
  const slow=measureIdleEnergy(motion(),0.5);
  assert.ok(Math.abs(slow.amplitude-small.amplitude)<0.001);
  assert.ok(Math.abs(slow.speed/small.speed-0.25)<0.001);
  assert.ok(slow.energy<small.energy);
});
test('髋部位移按素材髋高归一化，循环首尾跳变计入能量',()=>{
  const a=measureIdleEnergy(motion(0,0.1)),b=measureIdleEnergy(motion(0,0.2,2));
  assert.ok(a.energy>0);assert.ok(Math.abs(a.energy-b.energy)<1e-6);
  const open=motion();open.humanoidTracks.rotation.get('leftUpperArm').values.set(
    new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0,0,1),0.4).toArray(),8);
  assert.ok(measureIdleEnergy(open,1,true).energy>measureIdleEnergy(open,1,false).energy);
});
test('Boltzmann 概率归一化、单调下降，低温偏向安静且极值不溢出',()=>{
  const actions=[0,1,2].map((energy,i)=>({name:String(i),energy}));
  const p=idleDistribution(actions,1);
  assert.ok(Math.abs(p.reduce((s,x)=>s+x.probability,0)-1)<1e-12);
  assert.ok(Math.abs(p[0].probability/p[1].probability-Math.E)<1e-12);
  assert.ok(idleDistribution(actions,0.1)[0].probability>p[0].probability);
  assert.equal(idleDistribution([{energy:1e9},{energy:1e9+1e6}])[0].probability,1);
  assert.deepEqual(idleDistribution([]),[]);
  assert.equal(idleDistribution([{},{energy:NaN}])[0].probability,0.5);
  assert.equal(idleDistribution([{energy:0},{}])[1].probability,0);
  assert.equal(sampleIdle([{probability:0},{probability:1}],()=>0),1);
});
test('十万次固定种子抽样符合实际素材分布，所有自动待机均有耗能',()=>{
  const report=JSON.parse(fs.readFileSync(new URL('../idle-energy-report.json',import.meta.url)));
  assert.equal(report.entries.length,39);assert.equal(report.automatic.length,11);
  const p=idleDistribution(report.automatic),counts=p.map(()=>0);
  let seed=42;const random=()=>((seed=(Math.imul(1664525,seed)+1013904223)>>>0)/2**32);
  for(let i=0;i<100000;i++)counts[sampleIdle(p,random)]++;
  for(const [i,item] of p.entries()) {
    assert.ok(Number.isFinite(item.energy) && item.energy>=0);
    assert.ok(Math.abs(counts[i]/100000-item.probability)<0.005);
  }
  const sorted=[...p].sort((a,b)=>a.energy-b.energy);
  assert.equal(sorted[0].name,'PET_IDLE/PET_IDLE_3');
  assert.ok(sorted[0].probability>0.7 && sorted[0].probability<0.8);
  assert.ok(report.weightedMean<report.uniformMean*0.15);
});
test('启动选择最低耗能动作，说话时保留待机动作和剩余抽样时间',()=>{
  const actions=[{name:'big',energy:10},{name:'quiet',energy:0}];
  const b=new PetBehavior(()=>0,{idleActions:actions,roles:{speaking:'talk'}});
  assert.equal(b.motion().name,'quiet');b.step(0.08);const timer=b.idleTimer;
  b.present({state:'speaking'});for(let i=0;i<200;i++)b.step(0.08);
  assert.equal(b.idleTimer,timer);assert.equal(b.history.length,0);
  b.present({state:'idle'});assert.equal(b.motion().name,'quiet');
});
