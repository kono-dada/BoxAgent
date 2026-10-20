import {test} from 'node:test';
import assert from 'node:assert/strict';
import {MusicDance} from '../music-dance.js';
import {PetBehavior} from '../behavior.js';
function fixture(loader=async()=>({duration:2})) {
  const behavior=new PetBehavior(()=>0,{idleActions:[{name:'idle',duration:1}],roles:{dragging:'drag'}});
  const bank={manifest:{actions:{'PET_DANCING/a':'a','PET_DANCING/b':'b','PET_IDLE/x':'x'}},
    loadMotion:loader,settings:()=>({timeScale:0.5})};
  return {behavior,dance:new MusicDance(bank,behavior,()=>0)};
}
test('music uses random non-repeating full clips over speech and gestures; stop resumes idle',async()=>{
  const {behavior:b,dance}=fixture();b.present({state:'speaking'});
  b.gesture={name:'happy',start:0,duration:10};dance.setActive(true);await dance.update();
  assert.deepEqual(b.motion(),{name:'PET_DANCING/a',kind:'music',loop:false});
  assert.equal(b.musicGesture.duration,4);
  b.present({state:'idle'});assert.equal(b.motion().kind,'music');
  b.time=4;b.step(0.01);await dance.update();assert.equal(b.motion().name,'PET_DANCING/b');
  dance.setActive(false);assert.equal(b.motion().kind,'idle');
});
test('drag overrides music and music resumes afterwards',async()=>{
  const {behavior:b,dance}=fixture();dance.setActive(true);await dance.update();
  b.interact({kind:'drag-start'});assert.equal(b.motion().kind,'drag');
  await dance.update();assert.equal(b.musicGesture,null);
  b.interact({kind:'drag-end'});for(let i=0;i<5;i++)b.step(0.08);
  await dance.update();assert.equal(b.motion().kind,'music');
});
test('paused music cannot start a clip whose async loading completes late',async()=>{
  let resolve;const {behavior:b,dance}=fixture(()=>new Promise(done=>{resolve=done;}));
  dance.setActive(true);const pending=dance.update();dance.setActive(false);
  resolve({duration:2});await pending;assert.equal(b.musicGesture,null);
});
test('bad clips are skipped and empty catalogs remain idle',async()=>{
  const {dance,behavior:b}=fixture(async()=>{throw Error('bad clip');});
  dance.setActive(true);await dance.update();await dance.update();
  assert.equal(dance.rejected.size,2);assert.equal(dance.update(),undefined);
  assert.equal(b.motion().kind,'idle');
});
