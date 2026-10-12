import { useEffect, useRef, useState, type PointerEvent } from "react";
import { createRoot } from "react-dom/client";
import type { PetFrame } from "../types";
import "./pet.css";
function Pet() {
  const [frame, setFrame] = useState<PetFrame | null>(null);
  const iframe = useRef<HTMLIFrameElement>(null),
    dragging = useRef(false);
  function command(method: string, value?: unknown) {
    if (frame)
      iframe.current?.contentWindow?.postMessage(
        { type: "boxagent-pet-command", method, value },
        new URL(frame.url).origin,
      );
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
  useEffect(() => {
    if (frame)
      command("present", { state: frame.state, pointer: frame.pointer });
  }, [frame]);
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
  function drag(event: PointerEvent, phase: string) {
    if (phase === "start") {
      if (event.button !== 0) return;
      dragging.current = true;
      event.currentTarget.setPointerCapture(event.pointerId);
    } else if (!dragging.current) return;
    window.boxagent.desktopAction("drag", phase);
    if (phase === "end") dragging.current = false;
  }
  return (
    <div
      className="pet-hit"
      onPointerDown={(event) => drag(event, "start")}
      onPointerMove={(event) => drag(event, "move")}
      onPointerUp={(event) => drag(event, "end")}
      onPointerCancel={(event) => drag(event, "end")}
      onContextMenu={(event) => {
        event.preventDefault();
        window.boxagent.desktopAction("context-menu");
      }}
    >
      {frame && (
        <iframe
          ref={iframe}
          title="伙伴"
          src={frame.url}
          sandbox="allow-scripts allow-same-origin"
          onLoad={() =>
            command("present", { state: frame.state, pointer: frame.pointer })
          }
          style={{
            width: frame.viewport.width,
            height: frame.viewport.height,
            left: -frame.crop.x,
            top: -frame.crop.y,
          }}
        />
      )}
    </div>
  );
}
createRoot(document.getElementById("app")!).render(<Pet />);
