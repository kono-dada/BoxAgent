import * as THREE from 'three';
// 参数来源：Mate-Engine-Web/src/runtime/app/MateEngineWebApp.ts 的 configureScene。
export const WEB_RENDER_PROFILE=Object.freeze({
  key:{color:0xffffff,intensity:2.4,position:[2.5,4.5,4]},
  fill:{sky:0xcfd8ff,ground:0x2b2b2b,intensity:2.1},
  shadow:{mapSize:1024,near:0.1,far:8,left:-1.8,right:1.8,top:2.2,bottom:-1.2,bias:-0.00008,normalBias:0.025},
});
export function configureRenderer(renderer) {
  renderer.setClearColor(0x000000,0);
  renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;
  renderer.outputColorSpace=THREE.SRGBColorSpace;
}
export function configureLights(scene) {
  const p=WEB_RENDER_PROFILE;
  const key=new THREE.DirectionalLight(p.key.color,p.key.intensity);key.position.fromArray(p.key.position);key.castShadow=true;
  key.shadow.mapSize.set(p.shadow.mapSize,p.shadow.mapSize);
  for(const property of ['near','far','left','right','top','bottom'])key.shadow.camera[property]=p.shadow[property];
  key.shadow.bias=p.shadow.bias;key.shadow.normalBias=p.shadow.normalBias;
  const fill=new THREE.HemisphereLight(p.fill.sky,p.fill.ground,p.fill.intensity);
  scene.add(fill,key,key.target);
}
export function configureAvatar(root) {
  root.traverse(object=>{if(object.isMesh)object.castShadow=true;});
}
