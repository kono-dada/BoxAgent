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
  return state.voice === "ready" ? "listening" : "idle";
}
module.exports = {
  clampFrame,
  petSize,
  validBounds,
  bubbleFrame,
  presentation,
};
