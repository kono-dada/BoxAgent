import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import {VRMAnimation} from '@pixiv/three-vrm-animation';
import {MotionBank} from '../motion-bank.js';
import {measureIdleEnergy} from '../idle-energy.js';

test('重定向保留髋部位移并按模型身高换算，不丢失身体相对脚的运动', async () => {
  const hips = new THREE.Bone(), head = new THREE.Bone(); hips.name = 'hips'; head.name = 'head';
  const bones = {hips: {node: hips}, head: {node: head}};
  const vrm = {meta: {metaVersion: '1'}, humanoid: {normalizedHumanBones: bones,
    normalizedRestPose: {hips: {position: [0, 1, 0]}}, getNormalizedBoneNode: name => bones[name]?.node}};
  const animation = new VRMAnimation(); animation.duration = 1; animation.restHipsPosition.y = 2;
  animation.humanoidTracks.translation.set('hips', new THREE.VectorKeyframeTrack('hips', [0, 1], [0, 2, 0, 0.2, 1.8, 0]));
  animation.humanoidTracks.translation.set('head', new THREE.VectorKeyframeTrack('head', [0, 1], [0, 3, 0, 0, 4, 0]));
  animation.humanoidTracks.rotation.set('head', new THREE.QuaternionKeyframeTrack('head', [0, 1], [0, 0, 0, 1, 0, 0, 0, 1]));
  const bank = new MotionBank(vrm, {actions: {idle: 'idle.vrma'},motionSettings:{idle:{timeScale:0.5}}}, () => {});
  bank.loader.loadAsync = async () => ({userData: {vrmAnimations: [animation]}, parser: {json: {}}});
  const clip = await bank.loadMotion('idle');
  assert.deepEqual(clip.tracks.map(track => track.name), ['hips.position', 'head.quaternion']);
  assert.ok(Math.abs(clip.tracks[0].values[3] - 0.1) < 1e-6);
  assert.ok(Math.abs(clip.tracks[0].values[4] - 0.9) < 1e-6);
  const spec=bank.idleActions()[0];
  assert.equal(spec.duration,2);
  assert.ok(spec.energy>0);
  assert.equal(spec.energy,measureIdleEnergy(animation,0.5).energy);
});
