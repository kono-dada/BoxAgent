// 从本地动作包与 Unity 控制器生成可审查的配置，不修改参考仓库或素材。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
export function walk(directory) {
  return fs.readdirSync(directory,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name,'en')).flatMap(entry => {
    const file=path.join(directory,entry.name);
    return entry.isDirectory()?walk(file):entry.isFile()?[file]:[];
  });
}
const scalar=(text,key,fallback)=>{
  const value=text.match(new RegExp(`^  ${key}: (.+)$`,'m'))?.[1];
  return value===undefined?fallback:Number(value);
};
export function inspectVrma(buffer) {
  if(buffer.readUInt32LE(0)!==0x46546c67)throw new Error('动作不是 GLB 格式');
  const size=buffer.readUInt32LE(12),json=JSON.parse(buffer.subarray(20,20+size));
  let duration=0;
  for(const animation of json.animations || [])for(const sampler of animation.samplers || []){
    const accessor=json.accessors[sampler.input],view=json.bufferViews[accessor.bufferView];
    if(accessor.componentType!==5126)throw new Error('动作时间必须使用浮点数');
    const start=28+size+(view.byteOffset||0)+(accessor.byteOffset||0),stride=view.byteStride||4;
    for(let i=0;i<accessor.count;i++)duration=Math.max(duration,buffer.readFloatLE(start+i*stride));
  }
  return {duration:Number(duration.toFixed(6)),generator:json.asset.generator,
    channels:(json.animations || []).reduce((sum,a)=>sum+a.channels.length,0)};
}
export function extractProfile(motions,mate) {
  const animationRoot=path.join(mate,'Assets/MATE ENGINE - Animations');
  const controllerName='AvatarAnimatorController.controller';
  const controller=fs.readFileSync(path.join(animationRoot,controllerName),'utf8');
  const prefab=fs.readFileSync(path.join(mate,'Assets/MATE ENGINE - Scripts/CustomVRM.prefab'),'utf8');
  const guidMap=new Map(walk(animationRoot).filter(p=>p.endsWith('.anim.meta')).map(file=>[
    fs.readFileSync(file,'utf8').match(/^guid: (\w+)/m)?.[1],file.slice(0,-5)]));
  const documents=controller.split(/^--- /m);
  const states=documents.filter(b=>b.startsWith('!u!1102')).map(b=>({
    name:b.match(/^  m_Name: (.*)$/m)?.[1],motion:b.match(/^  m_Motion: \{fileID: (-?\d+)/m)?.[1],
    guid:b.match(/^  m_Motion:.*guid: (\w+)/m)?.[1],speed:scalar(b,'m_Speed',1),
  }));
  const idle=states.find(s=>s.name==='Idle');
  if(!idle)throw new Error('Unity 控制器缺少 Idle 状态');
  const idleTree=documents.find(b=>b.startsWith(`!u!206 &${idle.motion}\n`));
  if(!idleTree)throw new Error('找不到 Idle 混合树');
  const references=new Map();
  const add=(guid,value)=>{if(!guidMap.has(guid))return;const name=path.relative(animationRoot,guidMap.get(guid)).replace(/\.anim$/,'.vrma');if(!references.has(name))references.set(name,[]);references.get(name).push(value);return name;};
  for(const b of documents.filter(b=>b.startsWith('!u!206'))){
    const parameter=b.match(/^  m_BlendParameter: (.*)$/m)?.[1] || 'Blend';
    for(const child of b.split('  - serializedVersion: 2').slice(1))add(child.match(/guid: (\w+)/)?.[1],{
      kind:'blendTree',parameter,threshold:scalar(child,'  m_Threshold',0),timeScale:scalar(child,'  m_TimeScale',1),cycleOffset:scalar(child,'  m_CycleOffset',0),
    });
  }
  for(const s of states)add(s.guid,{kind:'state',state:s.name,timeScale:s.speed});
  const entries=walk(motions).filter(p=>p.endsWith('.vrma')).map(file=>{
    const relative=path.relative(motions,file),buffer=fs.readFileSync(file);
    const animPath=path.join(animationRoot,relative.replace(/\.vrma$/,'.anim'));
    const anim=fs.existsSync(animPath)?fs.readFileSync(animPath,'utf8'):'';
    return {id:relative.slice(0,-5),file:relative,category:relative.split('/')[0],bytes:buffer.length,
      sha256:crypto.createHash('sha256').update(buffer).digest('hex'),...inspectVrma(buffer),
      loop:anim.match(/^    m_LoopTime: (\d)$/m)?.[1]==='1',references:references.get(relative)||[]};
  });
  const available=new Set(entries.map(e=>e.file));
  const idleChildren=idleTree.split('  - serializedVersion: 2').slice(1).map(child=>{
    const anim=guidMap.get(child.match(/guid: (\w+)/)?.[1]);
    if(!anim)return null;
    const file=path.relative(animationRoot,anim).replace(/\.anim$/,'.vrma');
    return {id:file.slice(0,-5),file,index:scalar(child,'  m_Threshold',0),timeScale:scalar(child,'  m_TimeScale',1),cycleOffset:scalar(child,'  m_CycleOffset',0)};
  }).filter(Boolean);
  const idleCount=scalar(prefab,'totalIdleAnimations',11);
  const active=idleChildren.filter(c=>c.index<idleCount && available.has(c.file));
  const stateRole=name=>{
    const state=states.find(s=>s.name===name);const anim=guidMap.get(state?.guid);
    const file=anim?path.relative(animationRoot,anim).replace(/\.anim$/,'.vrma'):'';
    return available.has(file)?file.slice(0,-5):null;
  };
  const motionSet={idle:active.map(c=>c.id),dragging:stateRole('Drag'),speaking:stateRole('Talk'),sleeping:stateRole('Sleeping')};
  if(available.has('PET_MISC/PET_HAPPY.vrma'))motionSet.success='PET_MISC/PET_HAPPY';
  const motionSettings={};
  for(const entry of entries){
    const reference=entry.references.find(r=>r.parameter==='IdleIndex') || entry.references.find(r=>r.kind==='state') || entry.references[0];
    motionSettings[entry.id]={timeScale:reference?.timeScale ?? 1,cycleOffset:reference?.cycleOffset ?? 0,loop:entry.loop};
  }
  return {version:1,source:{motionFolder:'mate_engine_motions',controller:`Assets/MATE ENGINE - Animations/${controllerName}`,
    controllerSha256:crypto.createHash('sha256').update(controller).digest('hex'),prefab:'Assets/MATE ENGINE - Scripts/CustomVRM.prefab'},
    behaviorSettings:{idleSwitchTime:scalar(prefab,'IDLE_SWITCH_TIME',10),idleTransitionTime:scalar(prefab,'IDLE_TRANSITION_TIME',1)},
    idleCount,idleChildren,motionSet,motionSettings,entries};
}
if(process.argv[1] && path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  const [motions,mate]=process.argv.slice(2);
  if(!motions || !mate)throw new Error('用法：node scripts/sync_mate_motion_profile.mjs 动作包目录 Mate-Engine目录');
  const profile=extractProfile(path.resolve(motions),path.resolve(mate));
  fs.writeFileSync(path.join(root,'web/vrm/mate-motion-profile.json'),JSON.stringify(profile,null,2)+'\n');
  const lines=['# 本地动作目录','',`动作范围来自 \`mate_engine_motions\` 中的 ${profile.entries.length} 个 VRMA 文件。自动待机使用原版预制体的 ${profile.idleCount} 项范围，并只保留本地实际存在的动作。其它动作登记为可用资源，不会混入自动待机。`,'','## 自动待机顺序','','| 顺序 | 动作 | 播放速度 |','| --- | --- | --- |'];
  for(const [index,id] of profile.motionSet.idle.entries())lines.push(`| ${index+1} | ${id} | ${profile.motionSettings[id].timeScale} × |`);
  lines.push('','## 全部可用资源','','速度取自原版对应的待机混合树或状态；同一素材在不同状态有不同配置时，完整引用保存在 `web/vrm/mate-motion-profile.json`。没有控制器引用的素材按 1 倍作为手动预览默认值，不冒充原版状态配置。','','| 文件 | 时长（秒） | 控制器引用 |','| --- | --- | --- |');
  for(const e of profile.entries)lines.push(`| ${e.file} | ${e.duration} | ${e.references.map(r=>`${r.state||r.parameter}：${r.timeScale} ×`).join('；') || '未引用'} |`);
  fs.writeFileSync(path.join(root,'docs/vrm-motion-catalog.md'),lines.join('\n')+'\n');
  console.log(JSON.stringify({available:profile.entries.length,idle:profile.motionSet.idle,settings:profile.behaviorSettings},null,2));
}
