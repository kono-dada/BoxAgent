import { useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";
import { useWindowDrag } from "../hooks/useWindowDrag";
import "./pet.css";

// 命中层：窗口只覆盖人物本身，负责悬停与拖拽；画面由整屏渲染层负责，
// 因此这里不需要再承载 3D 画布。
function Pet() {
  const drag = useWindowDrag(window.boxagent);
  const cell = useRef("");
  useEffect(() => {
    window.boxagent.desktopAction("pet-hit-ready");
    return () => window.boxagent.desktopAction("pet-hit-ready");
  }, []);
  return (
    <div
      className="pet-hit"
      onMouseMove={(event) => {
        const next = `${Math.floor(event.screenX / 8)},${Math.floor(event.screenY / 8)}`;
        if (next === cell.current) return;
        cell.current = next;
        window.boxagent.desktopAction("pet-pointer");
      }}
      onMouseLeave={() => { cell.current = ""; }}
      {...drag}
      onContextMenu={(event) => {
        event.preventDefault();
        window.boxagent.desktopAction("context-menu");
      }}
    />
  );
}
createRoot(document.getElementById("app")!).render(<Pet />);
