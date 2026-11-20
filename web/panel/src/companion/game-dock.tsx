import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { CircleStop, Mic, MicOff, Volume2, VolumeX } from "lucide-react";
import type { AvatarSnapshot, RuntimeState } from "../types";
import { t } from "../i18n";
import "./game-dock.css";

const bridge = window.boxagent;

function GameDock() {
  const [desktop, setDesktop] = useState<AvatarSnapshot & { name?: string }>({});
  const [state, setState] = useState<RuntimeState>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const stopDesktop = bridge.onDesktop(setDesktop);
    const stopState = bridge.onState(setState);
    bridge.request<AvatarSnapshot>("desktop_snapshot").then(setDesktop).catch(() => {});
    bridge.request<RuntimeState>("state").then(setState).catch(() => {});
    return () => { stopDesktop(); stopState(); };
  }, []);

  const micOn = state.voice === "ready" || state.voice === "connecting";
  const speechOn = !state.companion_speech_muted;
  const micLabel = micOn ? t("关闭麦克风") : t("开启麦克风");
  const speechLabel = speechOn ? t("关闭陪玩声音") : t("开启陪玩声音");
  const open = desktop.game_dock_open === true;
  const name = desktop.name || t("伙伴");

  async function request(method: string, params: Record<string, unknown>) {
    setBusy(true);
    setError("");
    try {
      await bridge.request(method, params);
    } catch (reason) {
      setError(String(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className={`game-dock ${open ? "game-dock-open" : ""}`}>
      {open && (
        <div className="game-dock-controls" role="group" aria-label={t("陪玩模式")} title={error || undefined}>
          <button type="button" className={micOn ? "active" : undefined} aria-label={micLabel}
            title={micLabel} aria-pressed={micOn} disabled={busy}
            onClick={() => void request("set_companion_audio", { control: "microphone", enabled: !micOn })}>
            {micOn ? <Mic /> : <MicOff />}
          </button>
          <button type="button" className={speechOn ? "active" : undefined} aria-label={speechLabel}
            title={speechOn ? t("关闭游戏短评，保留对话和微信播报") : speechLabel} aria-pressed={speechOn} disabled={busy}
            onClick={() => void request("set_companion_audio", { control: "speech", enabled: !speechOn })}>
            {speechOn ? <Volume2 /> : <VolumeX />}
          </button>
          <button type="button" className="game-dock-stop" aria-label={t("结束陪玩")}
            title={t("结束陪玩")} disabled={busy || !desktop.game_dock_game}
            onClick={() => void request("set_companion_session", { game: desktop.game_dock_game, enabled: false })}>
            <CircleStop />
          </button>
        </div>
      )}
      <button type="button" className="game-dock-avatar" aria-label={open ? t("收起陪玩控制") : t("展开陪玩控制")}
        aria-expanded={open} onClick={() => bridge.desktopAction("game-dock-toggle")}>
        {desktop.avatar_portrait ? <img src={desktop.avatar_portrait} alt="" draggable={false} /> : name.slice(0, 1)}
      </button>
    </main>
  );
}

createRoot(document.getElementById("app")!).render(<GameDock />);
