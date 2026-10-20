import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import type { PetFrame } from "../types";
import { useWindowDrag } from "../hooks/useWindowDrag";
import "./pet.css";
function Pet() {
  const [frame, setFrame] = useState<PetFrame | null>(null);
  const iframe = useRef<HTMLIFrameElement>(null);
  const drag = useWindowDrag(window.boxagent);
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
  return (
    <div
      className="pet-hit"
      {...drag}
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
