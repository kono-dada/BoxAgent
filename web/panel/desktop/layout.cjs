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
const DESKTOP_CHAT_SCALE = 1.15;
const DESKTOP_CHAT_SIZE = {
  width: Math.round(360 * DESKTOP_CHAT_SCALE),
  height: Math.round(300 * DESKTOP_CHAT_SCALE),
};

function bubbleFrame(anchor, area, offset, requestedHeight = 300, shoulder) {
  const leftEdge = (shoulder?.left ?? anchor.x) - 4;
  const rightEdge = (shoulder?.right ?? (anchor.x + anchor.width)) + 4;
  const leftRoom = leftEdge - area.x - 8;
  const rightRoom = area.x + area.width - 8 - rightEdge;
  const sideRoom = Math.max(leftRoom, rightRoom);
  const width = Math.min(DESKTOP_CHAT_SIZE.width, area.width - 16,
    sideRoom >= 320 ? Math.floor(sideRoom) : DESKTOP_CHAT_SIZE.width);
  const requested = Math.min(
      Math.round(Math.max(228, Math.min(300, requestedHeight)) * DESKTOP_CHAT_SCALE),
      area.height - 32,
    );
  const beside = leftRoom >= width ? leftEdge - width : rightRoom >= width ? rightEdge : null;
  const below = anchor.y + anchor.height + 12;
  const aboveRoom = anchor.y - 12 - (area.y + 8);
  const belowRoom = area.y + area.height - 8 - below;
  const stackAbove = aboveRoom >= requested || (belowRoom < requested && aboveRoom > belowRoom);
  const stackRoom = stackAbove ? aboveRoom : belowRoom;
  const height = beside === null && stackRoom >= 228 ? Math.min(requested, stackRoom) : requested;
  const centered = (shoulder ? (shoulder.left + shoulder.right) / 2 : anchor.x + anchor.width / 2) - width / 2;
  const y = beside !== null ? (shoulder?.y ?? anchor.y + anchor.height * .26) - 28
    : stackAbove ? anchor.y - height - 12 : below;
  return clampFrame(
    {
      x: (beside ?? centered) + (offset?.x || 0),
      y: y + (offset?.y || 0),
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
  if (bubble.width + tasks.width + 12 > area.width) {
    // Narrow displays use a vertical task popover instead of spilling onto another screen.
    const chat = clampFrame(bubble, area);
    const panel = { ...tasks, x: chat.x + chat.width - tasks.width,
      y: chat.y - tasks.height - 12 };
    if (chat.height + panel.height + 12 <= area.height) {
      const [placedChat, placedPanel] = clampFrames([chat, panel], area);
      return { bubble: placedChat, tasks: placedPanel };
    }
    return { bubble: chat, tasks: clampFrame(panel, area) };
  }
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
  DESKTOP_CHAT_SIZE,
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
