// 与运行时共用修复、耗能计算和概率函数，生成全部待机素材的可复核结果。
import fs from 'node:fs';
import crypto from 'node:crypto';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {VRMAnimationLoaderPlugin} from '@pixiv/three-vrm-animation';
import {repairMateAnimation} from './mate-animation-compat.js';
import {measureIdleEnergy,idleDistribution,DEFAULT_IDLE_TEMPERATURE} from './idle-energy.js';
const root=new URL('../../',import.meta.url);
const profile=JSON.parse(fs.readFileSync(new URL('web/vrm/mate-motion-profile.json',root)));
const loader=new GLTFLoader();loader.register(parser=>new VRMAnimationLoaderPlugin(parser));
const entries=[];
const originalWarn=console.warn;
let legacyPoseWarnings=0;
console.warn=(...args)=> {
  if(String(args[0]).startsWith('VRMAnimationLoaderPlugin: The loaded VRM Animation might violate the VRM T-pose'))legacyPoseWarnings++;
  else originalWarn(...args);
};
try {
  for(const item of profile.entries.filter(e=>e.category==='PET_IDLE' || /^UPDATE_2\/PET_IDLE/.test(e.id))) {
    const buffer=fs.readFileSync(new URL(`assets/vrm/motions/mate-engine/${item.file}`,root));
    const gltf=await loader.parseAsync(buffer.buffer.slice(buffer.byteOffset,buffer.byteOffset+buffer.byteLength),'');
    const animation=gltf.userData.vrmAnimations[0];
    repairMateAnimation(animation,gltf.parser.json);
    const settings=profile.motionSettings[item.id];
    entries.push({name:item.id,sha256:crypto.createHash('sha256').update(buffer).digest('hex'),
      timeScale:settings.timeScale,automatic:profile.motionSet.idle.includes(item.id),
      ...measureIdleEnergy(animation,settings.timeScale,settings.loop)});
  }
} finally {console.warn=originalWarn;}
if(legacyPoseWarnings)console.warn(`旧导出素材的静止髋高提示共 ${legacyPoseWarnings} 项；位移按髋高绝对值归一化，与重定向比例的幅值一致。`);
const automatic=idleDistribution(profile.motionSet.idle.map(name=>entries.find(e=>e.name===name)));
const uniformMean=automatic.reduce((sum,e)=>sum+e.energy,0)/automatic.length;
const weightedMean=automatic.reduce((sum,e)=>sum+e.probability*e.energy,0);
const report={version:1,temperature:DEFAULT_IDLE_TEMPERATURE,uniformMean,weightedMean,
  reduction:1-weightedMean/uniformMean,automatic,entries};
fs.writeFileSync(new URL('web/vrm/idle-energy-report.json',root),JSON.stringify(report,null,2)+'\n');
const lines=['# 待机动作耗能与抽样','',
  '耗能值是骨骼运动的无量纲近似，不表示真实热量。所有动作先经过 Mate 导出格式修复，再按实际播放时间以 30 Hz 等距采样（最多 3600 段），与运行时使用相同的插值轨迹。',
  '', '## 耗能值', '',
  '对每个主要关节，计算相对动作平均姿态的角度 θ，以及相邻采样之间的角速度 ω。四元数使用最短旋转角，q 与 −q 视为同一姿态；平均姿态采用对齐四元数符号后求平均并归一化的近似。关节角度以 0.35 rad 归一化，角速度以 0.5 rad/s 归一化。定义 `A = Σ w_b × mean((θ / 0.35)²)`、`P = Σ w_b × max((θ / 0.35)²)`、`V = Σ w_b × mean((ω / 0.5)²)`，再加上髋部平移的对应项。',
  '', '骨骼权重固定为：髋部 3；脊柱、胸部、上胸及左右大腿各 2；左右上臂各 1.5；头部及左右小腿各 1；左右前臂各 0.75；颈部、左右肩及左右脚各 0.5；左右手及左右脚趾各 0.25。归一化后总和为 1，缺失轨道贡献为零。手指不参与，固定权重总和避免轨道数量影响分数。',
  '', '髋部位移先除以素材静止髋高的绝对值，再用 0.05 倍髋高和每秒 0.1 倍髋高分别归一化幅度和速度，加入同样的 A、P、V。旧导出包的静止髋高可能为负数，取绝对值不改变重定向位移的幅值；接近零时以 0.001 为下限。循环首尾的姿势跳变计入速度项。',
  '', '`E = A + 0.25 × P + 0.25 × V`。幅度占主要权重，较慢的大动作也会受到抑制；动作速度只影响 V，不降低幅度项。时间平均避免长动作仅因时长较长而得分更高。恒定姿势消耗为零，因此本指标不评价静态姿势是否舒适，也不包含表情、视线和切换动作的消耗。',
  '', '## 出现概率', '',
  `\`p(i) = exp(−(Eᵢ − E_min) / T) / Σⱼ exp(−(Eⱼ − E_min) / T)\`，默认 \`T = ${DEFAULT_IDLE_TEMPERATURE}\`。耗能每增加 \`T × ln(10) ≈ ${(DEFAULT_IDLE_TEMPERATURE*Math.log(10)).toFixed(3)}\`，抽样权重降为十分之一；降低 T 会更偏向安静动作。可在形象的 \`behaviorSettings.idleEnergyTemperature\` 中调整。`,
  '', '启动选择最低耗能动作。每 10 秒自主待机时间重新抽样，允许抽到当前动作，连续抽到时继续播放、不重新开始。拖拽、说话和睡眠期间暂停抽样计时；恢复时延续先前动作。所有待机之间使用 1 秒混合过渡。手动预览仍可播放所有资源，未启用的素材不会自动加入待机。',
  '', '概率表示每次抽样以及长期待机时间的占比，短期序列会波动。这里只改变动作分布，不改变原来的播放速度或资源。',
  '', '## 当前 11 个自动待机动作', '',
  '| 动作 | 播放速度 | 耗能值 | 抽样概率 |','| --- | ---: | ---: | ---: |'];
for(const e of automatic) {
  const percent=100*e.probability;
  lines.push(`| ${e.name} | ${e.timeScale} × | ${e.energy.toFixed(4)} | ${percent<0.001?percent.toExponential(2):percent.toFixed(3)}% |`);
}
lines.push('',`相同动作库下，等概率抽样的平均耗能为 ${uniformMean.toFixed(4)}，调整后为 ${weightedMean.toFixed(4)}，降低 ${(100*report.reduction).toFixed(1)}%。这是分数的期望变化，不代表真实热量或实际画面幅度降低同样的百分比。`,
  '', '## 全部待机类别素材', '',
  '包含 PET_IDLE 目录下的全部素材（其中也有舞蹈和拖拽素材）及 UPDATE2 的四个 idle 动作；只有上表的 11 项参与自主待机。','',
  '| 动作 | 实际时长（秒） | 均方幅度 A | 峰值平方 P | 均方速度 V | 耗能值 | 自动待机 |','| --- | ---: | ---: | ---: | ---: | ---: | --- |');
for(const e of entries)lines.push(`| ${e.name} | ${e.duration.toFixed(3)} | ${e.amplitude.toFixed(4)} | ${e.peak.toFixed(4)} | ${e.speed.toFixed(4)} | ${e.energy.toFixed(4)} | ${e.automatic?'是':'否'} |`);
lines.push('', '## 复核', '',
  '在 `web/vrm` 执行 `pnpm idle:report` 可从仓库实际 VRMA 重新生成此表及 `idle-energy-report.json`（含素材 SHA-256、未舍入分数和概率）。运行时根据当前形象加载的动作、速度和循环设置重新计算，而不是依赖这份报告。渲染器调试信息的 `behavior.idleDistribution` 提供当前实际分数和概率。','');
fs.writeFileSync(new URL('docs/vrm-idle-energy.md',root),lines.join('\n'));
console.log(JSON.stringify({actions:entries.length,uniformMean,weightedMean,reduction:report.reduction,
  automatic:automatic.map(e=>({name:e.name,energy:e.energy,probability:e.probability}))},null,2));
