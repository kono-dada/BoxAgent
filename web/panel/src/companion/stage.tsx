import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import type { PetFrame } from "../types";
import "./pet.css";

// 整屏渲染层：窗口覆盖人物所在屏幕的工作区，角色按真实屏幕坐标绘制，
// 因此动作超出首帧边界时也不会被小窗口裁掉。
function Stage() {
  const [frame, setFrame] = useState<PetFrame | null>(null);
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
      if (
        frame &&
        event.source === iframe.current?.contentWindow &&
        event.origin === new URL(frame.url).origin &&
        event.data?.type === "boxagent-pet-report"
      )
        window.boxagent.desktopAction("pet-report", event.data.value);
    };
    window.addEventListener("message", listener);
    return () => window.removeEventListener("message", listener);
  }, [frame?.url]);
  useEffect(() => {
    if (frame) present(frame);
  }, [frame]);
  return (
    <div className="pet-stage">
      <img
        className="pet-stage-bg"
        src="backgrounds/bg-c.png"
        alt=""
        aria-hidden="true"
      />
      {frame && (
        <iframe
          ref={iframe}
          title="伙伴"
          src={frame.url}
          sandbox="allow-scripts allow-same-origin"
          onLoad={() => present(frame)}
          style={{
            width: frame.viewport.width,
            height: frame.viewport.height,
            left: frame.canvas.x,
            top: frame.canvas.y,
          }}
        />
      )}
    </div>
  );
}
createRoot(document.getElementById("app")!).render(<Stage />);
