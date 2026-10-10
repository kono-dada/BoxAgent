// 导入失败不得留下半成品，也不能覆盖用户已有的模型。
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {importVrm} from '../../../scripts/import_vrm.mjs';

function fixture(t, extensions={VRM:{}}) {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'boxagent-vrm-import-'));
  t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
  let json=JSON.stringify({asset:{version:'2.0'},extensions});
  json+=' '.repeat((4-json.length%4)%4);
  const buffer=Buffer.alloc(20+Buffer.byteLength(json));
  buffer.writeUInt32LE(0x46546c67,0);buffer.writeUInt32LE(2,4);buffer.writeUInt32LE(buffer.length,8);
  buffer.writeUInt32LE(buffer.length-20,12);buffer.writeUInt32LE(0x4e4f534a,16);buffer.write(json,20);
  const model=path.join(root,'model.vrm');fs.writeFileSync(model,buffer);
  return {root,options:{model,id:'local-test',name:'测试形象',outputRoot:path.join(root,'pets')}};
}

test('默认导入引用共享动作，不生成每个角色一份的动作目录',t=>{
  const {options}=fixture(t),target=importVrm(options);
  const manifest=JSON.parse(fs.readFileSync(path.join(target,'pet.json')));
  assert.equal(manifest.motionPack,'mate-engine');
  assert.equal(Object.keys(manifest.actions).length,128);
  assert.equal(fs.existsSync(path.join(target,'motions')),false);
});

test('已有形象标识拒绝覆盖，保留原文件',t=>{
  const {options}=fixture(t),target=path.join(options.outputRoot,options.id);
  fs.mkdirSync(target,{recursive:true});fs.writeFileSync(path.join(target,'model.vrm'),'existing');
  assert.throws(()=>importVrm(options),/已存在/);
  assert.equal(fs.readFileSync(path.join(target,'model.vrm'),'utf8'),'existing');
});

test('重复导入相同模型和配置复用原目录，不留下隐藏副本',t=>{
  const {options}=fixture(t),target=importVrm(options);
  assert.equal(importVrm({...options,id:'local-duplicate',name:'其他名称'}),target);
  assert.deepEqual(fs.readdirSync(options.outputRoot),['local-test']);
});

test('非 VRM 和未下载的 LFS 指针在写入前被拒绝',t=>{
  const {options}=fixture(t,{});
  assert.throws(()=>importVrm(options),/VRM 扩展/);
  fs.writeFileSync(options.model,'version https://git-lfs.github.com/spec/v1\noid sha256:abc\n');
  assert.throws(()=>importVrm(options),/git lfs pull/);
  assert.equal(fs.existsSync(options.outputRoot),false);
});

test('复制附件失败清理暂存目录，不留下菜单可见的半成品',t=>{
  const {root,options}=fixture(t);
  assert.throws(()=>importVrm({...options,readme:path.join(root,'missing.txt')}),/ENOENT/);
  assert.deepEqual(fs.readdirSync(options.outputRoot),[]);
});
