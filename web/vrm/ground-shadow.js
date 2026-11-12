import * as THREE from 'three';

// 角色脚下的落地阴影。
// 渲染器是接近水平视角（仰角约 9°），地面平面上的真实阴影会被压成一条线，
// 因此改用始终朝向相机的柔和椭圆投影，贴在脚边营造落地感。
export function createGroundShadow(scene, box, size) {
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 256;
  const context = canvas.getContext('2d');
  const gradient = context.createRadialGradient(128, 128, 0, 128, 128, 128);
  gradient.addColorStop(0, 'rgba(40, 33, 48, 0.82)');
  gradient.addColorStop(0.5, 'rgba(40, 33, 48, 0.38)');
  gradient.addColorStop(1, 'rgba(40, 33, 48, 0)');
  context.fillStyle = gradient;
  context.fillRect(0, 0, 256, 256);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const material = new THREE.SpriteMaterial({
    map: texture,
    transparent: true,
    depthWrite: false,
    opacity: 0.9,
  });
  const shadow = new THREE.Sprite(material);
  const center = box.getCenter(new THREE.Vector3());
  shadow.position.set(
    center.x,
    box.min.y + size.y * 0.004,
    center.z + Math.max(size.z, 0.1) * 0.06,
  );
  shadow.scale.set(size.x * 1.05, size.x * 0.3, 1);
  shadow.renderOrder = 1;
  scene.add(shadow);
  return {
    shadow,
    dispose() {
      scene.remove(shadow);
      texture.dispose();
      material.dispose();
    },
  };
}
