// 从缩小的透明画面求实际角色边界，再转换到原生窗口的左下角坐标。
export function visibleBounds(data, width, height, viewportWidth, viewportHeight) {
  let left = width, right = -1, top = height, bottom = -1;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      if (data[(y * width + x) * 4 + 3] === 0) continue;
      left = Math.min(left, x); right = Math.max(right, x);
      top = Math.min(top, y); bottom = Math.max(bottom, y);
    }
  }
  if (right < left) return null;
  // 保留一个采样格和少量动画余量，避免细发丝与下一帧的摆动被裁掉。
  const margin = Math.max(6, viewportHeight * .02);
  const padX = viewportWidth / width + margin, padY = viewportHeight / height + margin;
  const x = Math.max(0, Math.floor(left * viewportWidth / width - padX));
  const y = Math.max(0, Math.floor((height - bottom - 1) * viewportHeight / height - padY));
  const maxX = Math.min(viewportWidth, Math.ceil((right + 1) * viewportWidth / width + padX));
  const maxY = Math.min(viewportHeight, Math.ceil((height - top) * viewportHeight / height + padY));
  return {x, y, width:maxX - x, height:maxY - y};
}
