function clampFrame(frame, area) {
  return {
    ...frame,
    x: Math.round(
      Math.max(area.x, Math.min(frame.x, area.x + area.width - frame.width)),
    ),
    y: Math.round(
      Math.max(area.y, Math.min(frame.y, area.y + area.height - frame.height)),
    ),
  };
}
function petSize(scale, area) {
  const fit = Math.min(scale, area.width / 252, area.height / 182);
  return { width: Math.round(252 * fit), height: Math.round(182 * fit) };
}
function validBounds(report, viewport) {
  const b = report?.bounds;
  return report?.viewport?.[0] === viewport.width &&
    report?.viewport?.[1] === viewport.height &&
    b &&
    ["x", "y", "width", "height"].every((key) => Number.isFinite(b[key])) &&
    b.x >= 0 &&
    b.y >= 0 &&
    b.width > 0 &&
    b.height > 0 &&
    b.x + b.width <= viewport.width &&
    b.y + b.height <= viewport.height
    ? b
    : null;
}
function scalePetCrop(initial, viewport) {
  // 始终从首帧边界缩放，向外取整，避免反复缩放积累误差或裁掉边缘。
  const scaleX = viewport.width / initial.viewport.width,
    scaleY = viewport.height / initial.viewport.height,
    x = Math.floor(initial.crop.x * scaleX),
    y = Math.floor(initial.crop.y * scaleY);
  return {
    x,
    y,
    width: Math.ceil((initial.crop.x + initial.crop.width) * scaleX) - x,
    height: Math.ceil((initial.crop.y + initial.crop.height) * scaleY) - y,
  };
}
function bubbleFrame(anchor, area, offset, requestedHeight = 270) {
  const width = Math.min(440, area.width - 16),
    height = Math.min(
      Math.max(228, Math.min(600, requestedHeight)),
      area.height - 32,
    );
  return clampFrame(
    {
      x: anchor.x + anchor.width - width + (offset?.x || 0),
      y: anchor.y - height - 12 + (offset?.y || 0),
      width,
      height,
    },
    {
      x: area.x + 8,
      y: area.y + 8,
      width: area.width - 16,
      height: area.height - 16,
    },
  );
}
function presentation(state) {
  if (state.approval) return "approval";
  if (state.user_speaking) return "listening";
  if (state.speaking) return "speaking";
  if (["accepted", "running", "cancelling"].includes(state.task))
    return "working";
  if (state.voice === "connecting") return "connecting";
  if (state.error || ["failed", "blocked"].includes(state.task)) return "error";
  if (state.task === "succeeded") return "success";
  return "idle";
}
function taskFrame(anchor, area, expanded) {
  const width = Math.min(expanded ? 320 : 248, area.width - 16);
  const height = Math.min(expanded ? 280 : 54, area.height - 16);
  const left = anchor.x - width - 12;
  return clampFrame({
    x: left >= area.x + 8 ? left : anchor.x + anchor.width + 12,
    y: anchor.y,
    width, height,
  }, { x: area.x + 8, y: area.y + 8, width: area.width - 16, height: area.height - 16 });
}
function linkedFrames(bubble, tasks, area) {
  const [chat, panel] = clampFrames([bubble, tasks], area);
  return { bubble: chat, tasks: panel };
}
function clampFrames(frames, area) {
  const x = Math.min(...frames.map(frame => frame.x)), y = Math.min(...frames.map(frame => frame.y));
  const group = clampFrame({ x, y,
    width: Math.max(...frames.map(frame => frame.x + frame.width)) - x,
    height: Math.max(...frames.map(frame => frame.y + frame.height)) - y,
  }, area);
  return frames.map(frame => ({ ...frame, x: frame.x + group.x - x, y: frame.y + group.y - y }));
}
module.exports = {
  clampFrame,
  petSize,
  validBounds,
  scalePetCrop,
  bubbleFrame,
  taskFrame,
  linkedFrames,
  clampFrames,
  presentation,
};
