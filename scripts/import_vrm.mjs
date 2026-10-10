// 通用本地模型导入；默认引用仓库动作包，避免复制共享素材。
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {walk} from './sync_mate_motion_profile.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const sharedMotions=path.join(root,'assets/vrm/motions/mate-engine');
const profile=JSON.parse(fs.readFileSync(path.join(root,'web/vrm/mate-motion-profile.json')));

const stable=value=>JSON.stringify(value,(_,item)=>item && !Array.isArray(item) && typeof item==='object'
  ? Object.fromEntries(Object.entries(item).sort(([a],[b])=>a.localeCompare(b))) : item);
const settings=manifest=>Object.fromEntries(Object.entries(manifest).filter(([key])=>
  !['displayName','description','model','actions','motionPack','motionProfile'].includes(key)));

function equivalentModel(model, manifest, files, motions, outputRoot) {
  const hashes=new Map();
  const hash=file=>{if(!hashes.has(file))hashes.set(file,crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex'));return hashes.get(file);};
  const safeFile=(directory,relative)=>{
    const resolved=fs.realpathSync(path.join(directory,relative));
    if(!resolved.startsWith(fs.realpathSync(directory)+path.sep))throw new Error('资源超出目录');
    return resolved;
  };
  const expected=stable(settings(manifest));
  const actions=Object.fromEntries(files.map(file=>[path.relative(motions,file).slice(0,-5),hash(file)]));
  for(const directory of [path.join(root,'assets/vrm/models'),outputRoot]){
    if(!fs.existsSync(directory))continue;
    for(const entry of fs.readdirSync(directory,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name))){
      if(!entry.isDirectory() || entry.name.startsWith('.'))continue;
      const candidate=path.join(directory,entry.name);
      try {
        const existing=JSON.parse(fs.readFileSync(path.join(candidate,'pet.json')));
        if(stable(settings(existing))!==expected || hash(safeFile(candidate,existing.model))!==hash(model))continue;
        if(existing.motionPack && existing.motionPack!=='mate-engine')continue;
        const actual=Object.fromEntries(Object.entries(existing.actions || {}).map(([key,file])=>{
          if(existing.motionPack && !file.startsWith('motions/'))throw new Error('动作路径无效');
          return [key,hash(safeFile(existing.motionPack?sharedMotions:candidate,
            existing.motionPack?file.slice(8):file))];
        }));
        if(stable(actual)===stable(actions))return candidate;
      } catch { /* 不复用损坏的已有资源。 */ }
    }
  }
  return null;
}

function readGlb(file) {
  const data=fs.readFileSync(file);
  if(data.subarray(0,40).toString().startsWith('version https://git-lfs'))throw new Error('资源尚未下载，请运行 git lfs pull');
  if(data.length<20 || data.readUInt32LE(0)!==0x46546c67 || data.readUInt32LE(4)!==2
      || data.readUInt32LE(8)!==data.length || data.readUInt32LE(16)!==0x4e4f534a)throw new Error(`不是有效的 GLB 资源：${file}`);
  return JSON.parse(data.subarray(20,20+data.readUInt32LE(12)).toString());
}

export function importVrm({model,motions=sharedMotions,id,name=id,hiddenMeshes=[],readme,outputRoot=path.join(root,'.runtime/pets')}) {
  if(!/^local-[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id))throw new Error('形象标识必须为 local- 开头的英文小写字母、数字或短横线');
  const gltf=readGlb(model);
  if(!gltf.extensions?.VRM && !gltf.extensions?.VRMC_vrm)throw new Error('模型缺少 VRM 扩展');
  const target=path.join(outputRoot,id);
  const shared=path.resolve(motions)===sharedMotions;
  const files=walk(motions).filter(file=>file.endsWith('.vrma'));
  for(const file of files){if(!readGlb(file).extensions?.VRMC_vrm_animation)throw new Error(`动作缺少 VRMA 扩展：${file}`);}
  const actions=Object.fromEntries(files.map(file=>{const relative=path.relative(motions,file);return [relative.slice(0,-5),`motions/${relative}`];}));
  const motionSet=Object.fromEntries(Object.entries(profile.motionSet).map(([role,value])=>[
    role,Array.isArray(value)?value.filter(key=>actions[key]):actions[value]?value:null,
  ]));
  if(!motionSet.idle.length || !motionSet.dragging)throw new Error('动作包缺少原版待机或拖拽动作');
  const motionSettings=Object.fromEntries(Object.keys(actions).map(key=>[key,profile.motionSettings[key] || {timeScale:1,cycleOffset:0}]));
  const manifest={type:'vrm',version:1,displayName:name,model:'model.vrm',hiddenMeshes,
    motionSet,motionSettings,behaviorSettings:profile.behaviorSettings,actions,
    motionProfile:{source:profile.source.controller,sha256:profile.source.controllerSha256,available:Object.keys(actions).length}};
  if(shared)manifest.motionPack='mate-engine';
  const existing=equivalentModel(model,manifest,files,motions,outputRoot);
  if(existing){console.log(`已存在相同模型、动作和配置，直接复用：${existing}`);return existing;}
  if(fs.existsSync(target))throw new Error('形象标识已存在，请使用新的标识；不会覆盖已有资源');
  fs.mkdirSync(outputRoot,{recursive:true});
  const staging=fs.mkdtempSync(path.join(outputRoot,'.import-'));
  try {
    fs.copyFileSync(model,path.join(staging,'model.vrm'));
    if(readme)fs.copyFileSync(readme,path.join(staging,'ReadMe.txt'));
    if(!shared)for(const file of files){const destination=path.join(staging,'motions',path.relative(motions,file));fs.mkdirSync(path.dirname(destination),{recursive:true});fs.copyFileSync(file,destination);}
    fs.writeFileSync(path.join(staging,'pet.json'),JSON.stringify(manifest,null,2)+'\n');
    fs.renameSync(staging,target);
  } finally {fs.rmSync(staging,{recursive:true,force:true});}
  console.log(`已导入：${target}，${files.length} 个动作，${motionSet.idle.length} 个自动待机`);
  return target;
}
if(process.argv[1] && path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  const [model,second,third,fourth]=process.argv.slice(2);
  const legacy=second && !second.startsWith('local-');
  const id=legacy?third:second,name=legacy?fourth:third;
  if(!model || !id)throw new Error('用法：node scripts/import_vrm.mjs 模型.vrm local-标识 [显示名称]；也支持在标识前指定自定义动作目录');
  importVrm({model:path.resolve(model),motions:legacy?path.resolve(second):sharedMotions,id,name});
}
