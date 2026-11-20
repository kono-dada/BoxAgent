import { useEffect, useRef, useState, type CSSProperties } from "react";
import { createRoot } from "react-dom/client";
import type { PetFrame } from "../types";
import "./pet.css";

function Caption({ frame }: { frame: PetFrame }) {
  const { caption, canvas, overlayAnchors, viewport, crop } = frame;
  if (!caption) return null;
  if (frame.gameOverlay && caption.source !== "微信") return null;
  const face = overlayAnchors?.face;
  const left = canvas.x + (face ? face.left * viewport.width : crop.x + crop.width * .25);
  const right = canvas.x + (face ? face.right * viewport.width : crop.x + crop.width * .75);
  const y = canvas.y + (face ? face.y * viewport.height : crop.y + crop.height * .22);
  const max = Math.min(frame.scene ? 260 : 232, innerWidth - 20);
  const gap = 9;
  const leftSpace = left - gap - 10;
  const rightSpace = innerWidth - right - gap - 10;
  const preferRight = rightSpace >= leftSpace || (frame.scene && rightSpace >= Math.min(max, 176));
  const side = frame.gameOverlay ? "game"
    : Math.max(leftSpace, rightSpace) < 126 ? "above"
    : preferRight ? "right" : "left";
  const top = Math.max(10, Math.min(innerHeight - 180, y - 17));
  let style: CSSProperties;
  switch (side) {
    case "game":
      style = { right: 48, top: "calc(27% + 54px)", maxWidth: Math.min(260, innerWidth - 60) };
      break;
    case "right":
      style = { left: right + gap, top, maxWidth: Math.min(max, rightSpace) };
      break;
    case "left":
      style = { right: innerWidth - left + gap, top, maxWidth: Math.min(max, leftSpace) };
      break;
    default: {
      const center = Math.max(max / 2 + 10, Math.min(innerWidth - max / 2 - 10, (left + right) / 2));
      style = { left: center, bottom: innerHeight - Math.max(52, canvas.y + crop.y - gap), maxWidth: max };
    }
  }
  return (
    <div className={`pet-caption pet-caption-${side}`} role="status" style={style}>
      {caption.source && <strong>{[caption.source, caption.sender].filter(Boolean).join(" · ")}</strong>}
      <span>{caption.text}</span>
    </div>
  );
}

function FootStatus({ frame }: { frame: PetFrame }) {
  if (!frame.footStatus) return null;
  const { feet } = frame.overlayAnchors || {};
  const x = frame.canvas.x + (feet ? feet.x * frame.viewport.width : frame.crop.x + frame.crop.width / 2);
  const y = frame.canvas.y + (feet ? feet.y * frame.viewport.height : frame.crop.y + frame.crop.height * .94);
  return (
    <div className="pet-foot-status" role="status" data-tone={frame.footStatus.tone} style={{
      left: Math.max(92, Math.min(innerWidth - 92, x)),
      top: Math.max(8, Math.min(innerHeight - 31, y + 6)),
    }}>
      <i aria-hidden="true" />
      <span>{frame.footStatus.text}</span>
    </div>
  );
}

// 整屏渲染层：窗口覆盖人物所在屏幕的工作区，角色按真实屏幕坐标绘制，
// 因此动作超出首帧边界时也不会被小窗口裁掉。
function Stage() {
  const [frame, setFrame] = useState<PetFrame | null>(null);
  const [overlayAnchors, setOverlayAnchors] = useState<PetFrame["overlayAnchors"]>();
  const iframe = useRef<HTMLIFrameElement>(null);
  function command(method: string, value?: unknown) {
    if (frame)
      iframe.current?.contentWindow?.postMessage(
        { type: "boxagent-pet-command", method, value },
        new URL(frame.url).origin,
      );
  }
  function present(current: PetFrame) {
    command("present", {
      state: current.state,
      pointer: current.pointer,
      action: current.action,
      actionId: current.actionId,
      music: current.music,
      walkDirection: current.walkDirection,
      hiddenForGame: current.gameOverlay,
      overlaysVisible: Boolean(current.caption || current.footStatus),
    });
  }
  useEffect(() => {
    const stop = window.boxagent.onPet(setFrame);
    window.boxagent.desktopAction("pet-ready");
    return stop;
  }, []);
  useEffect(
    () => window.boxagent.onInteract((value) => command("interact", value)),
    [frame?.url],
  );
  useEffect(
    () => window.boxagent.onPerform((value) => command("perform", value)),
    [frame?.url],
  );
  useEffect(() => {
    const listener = (event: MessageEvent) => {
      if (!frame || event.source !== iframe.current?.contentWindow ||
        event.origin !== new URL(frame.url).origin || event.data?.type !== "boxagent-pet-report") return;
      const report = event.data.value;
      if (report?.type === "overlay-anchors") setOverlayAnchors(report.anchors);
      else window.boxagent.desktopAction("pet-report", report);
    };
    window.addEventListener("message", listener);
    return () => window.removeEventListener("message", listener);
  }, [frame?.url]);
  useEffect(() => {
    if (frame) present(frame);
  }, [frame]);
  const overlayFrame = frame && { ...frame, overlayAnchors: overlayAnchors || frame.overlayAnchors };
  return (
    <div className="pet-stage">
      {frame?.scene && (
        <div className="pet-stage-bg" aria-hidden="true">
          {frame.scene === "pink" && <img src="backgrounds/bg-c.png" alt="" />}
        </div>
      )}
      {frame && (
        <iframe
          ref={iframe}
          title="伙伴"
          src={frame.url}
          sandbox="allow-scripts allow-same-origin"
          onLoad={() => present(frame)}
          style={{
            visibility: frame.gameOverlay ? "hidden" : undefined,
            width: frame.viewport.width,
            height: frame.viewport.height,
            left: frame.canvas.x,
            top: frame.canvas.y,
          }}
        />
      )}
      {overlayFrame && <><Caption frame={overlayFrame} /><FootStatus frame={overlayFrame} /></>}
    </div>
  );
}
createRoot(document.getElementById("app")!).render(<Stage />);
