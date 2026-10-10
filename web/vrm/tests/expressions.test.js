import {test} from 'node:test';
import assert from 'node:assert/strict';
import {ExpressionAdapter} from '../expressions.js';
const pose = {blink:0.8,jaw:0.3,smile:0.4,surprised:0.1};
test('VRM 标准绑定优先于同名 morph，并保留未支持的表情', () => {
  const values = {};
  const mesh = {morphTargetDictionary:{jawOpen:0},morphTargetInfluences:[0]};
  const vrm = {expressionManager:{getExpression:n=>n==='aa'?{binds:[{}]}:null,setValue:(n,v)=>values[n]=v},
    scene:{traverse:visit=>visit(mesh)}};
  const adapter = new ExpressionAdapter(vrm);
  adapter.update(pose); adapter.flush();
  assert.equal(values.aa,0.3); assert.equal(mesh.morphTargetInfluences[0],0);
});
test('非标准左右表情分别写入，且在管理器更新后才应用', () => {
  const mesh = {morphTargetDictionary:{eyeBlinkLeft:0,eyeBlinkRight:1,jawOpen:2,unrelated:3},morphTargetInfluences:[0,0,0,0.7]};
  const adapter = new ExpressionAdapter({scene:{traverse:visit=>visit(mesh)}});
  adapter.update(pose); assert.deepEqual(mesh.morphTargetInfluences,[0,0,0,0.7]);
  adapter.flush(); assert.deepEqual(mesh.morphTargetInfluences,[0.8,0.8,0.3,0.7]);
});
test('缺失表情的模型可以继续运行', () => {
  const adapter = new ExpressionAdapter({scene:{traverse:()=>{}}});
  adapter.update(pose); adapter.flush(); assert.deepEqual(adapter.debug(),{});
});
