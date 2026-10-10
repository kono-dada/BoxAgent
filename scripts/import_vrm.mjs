// 所有角色共享同一动作范围；默认行为来自 Unity 配置，不再人工挑选八个待机动作。
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {walk} from './sync_mate_motion_profile.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const profile=JSON.parse(fs.readFileSync(path.join(root,'web/vrm/mate-motion-profile.json')));
export function importVrm({model,motions,id,name,hiddenMeshes=[],readme}) {
  if(!/^local-[a-z0-9-]+$/.test(id))throw new Error('形象标识必须为 local- 开头的英文小写字母、数字或短横线');
  if(!fs.statSync(model).isFile())throw new Error('模型不是普通文件');
  const target=path.join(root,'.runtime/pets',id);
  const shared=path.resolve(motions)===path.join(root,'assets/vrm/motions/mate-engine');
  const files=walk(motions).filter(file=>file.endsWith('.vrma'));
  const actions=Object.fromEntries(files.map(file=>{const relative=path.relative(motions,file);return [relative.slice(0,-5),`motions/${relative}`];}));
  const motionSet=Object.fromEntries(Object.entries(profile.motionSet).map(([role,value])=>[
    role,Array.isArray(value)?value.filter(key=>actions[key]):actions[value]?value:null,
  ]));
  if(!motionSet.idle.length || !motionSet.dragging)throw new Error('动作包缺少原版待机或拖拽动作');
  fs.mkdirSync(target,{recursive:true});
  fs.copyFileSync(model,path.join(target,'model.vrm'));
  if(readme)fs.copyFileSync(readme,path.join(target,'ReadMe.txt'));
  if(!shared)for(const file of files){const destination=path.join(target,'motions',path.relative(motions,file));fs.mkdirSync(path.dirname(destination),{recursive:true});fs.copyFileSync(file,destination);}
  const motionSettings=Object.fromEntries(Object.keys(actions).map(key=>[key,profile.motionSettings[key] || {timeScale:1,cycleOffset:0}]));
  const manifest={type:'vrm',version:1,displayName:name,model:'model.vrm',hiddenMeshes,
    motionSet,motionSettings,behaviorSettings:profile.behaviorSettings,actions,
    motionProfile:{source:profile.source.controller,sha256:profile.source.controllerSha256,available:Object.keys(actions).length}};
  if(shared)manifest.motionPack='mate-engine';
  fs.writeFileSync(path.join(target,'pet.json'),JSON.stringify(manifest,null,2)+'\n');
  console.log(`已导入：${target}，${files.length} 个动作，${motionSet.idle.length} 个自动待机`);
  return target;
}
if(process.argv[1] && path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  const [model,motions,id,name=id]=process.argv.slice(2);
  if(!model || !motions || !id)throw new Error('用法：node scripts/import_vrm.mjs 模型.vrm 动作目录 local-标识 显示名称');
  importVrm({model:path.resolve(model),motions:path.resolve(motions),id,name});
}
