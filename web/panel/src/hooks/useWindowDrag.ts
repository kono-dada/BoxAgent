import { useEffect, useRef, type PointerEvent } from "react";
import type { PanelBridge } from "../types";

export function useWindowDrag(bridge: PanelBridge) {
  const pointer = useRef<number | null>(null);
  function cancel() {
    if (pointer.current === null) return;
    pointer.current = null;
    bridge.desktopAction("drag", { phase: "cancel", buttons: 0 });
  }
  useEffect(() => {
    window.addEventListener("blur", cancel);
    return () => { window.removeEventListener("blur", cancel); cancel(); };
  }, [bridge]);
  function send(event: PointerEvent<HTMLElement>, phase: "start" | "move" | "end") {
    bridge.desktopAction("drag", { phase, buttons: event.buttons,
      point: { x: event.screenX, y: event.screenY } });
  }
  return {
    onPointerDown(event: PointerEvent<HTMLElement>) {
      if (event.button !== 0 || !(event.buttons & 1) ||
        (event.target instanceof Element && event.target.closest("button,input,textarea,select,a,details"))) return;
      event.preventDefault();
      pointer.current = event.pointerId;
      event.currentTarget.setPointerCapture(event.pointerId);
      send(event, "start");
    },
    onPointerMove(event: PointerEvent<HTMLElement>) {
      if (pointer.current !== event.pointerId) return;
      if (!(event.buttons & 1)) { cancel(); return; }
      send(event, "move");
    },
    onPointerUp(event: PointerEvent<HTMLElement>) {
      if (pointer.current !== event.pointerId) return;
      pointer.current = null;
      send(event, "end");
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    },
    onPointerCancel: cancel,
    onLostPointerCapture: cancel,
  };
}
