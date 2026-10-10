// 现有素材包的导入快捷方式；显示与行为均使用通用 VRM 框架。
import path from 'node:path';
import os from 'node:os';
import { importVrm } from './import_vrm.mjs';
const source = path.resolve(process.argv[2] || path.join(os.homedir(),'Downloads/vrm_asset'));
const modelRoot = path.join(source,'models/Q版小企鹅v1.0.3');
importVrm({model:path.join(modelRoot,'企鹅v1.0.3.vrm'),motions:path.join(source,'mate_engine_motions'),
  id:'local-penguin',name:'小企鹅',hiddenMeshes:['面具'],readme:path.join(modelRoot,'ReadMe.txt')});
