import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { AlertCircle, Camera, CameraOff, Check, ChevronDown, FlipVertical2, LoaderCircle, PhoneOff, Pin, Video } from 'lucide-react';
import './camera.css';

interface CameraEvent {
  type: string;
  state: { revision: number; camera_error?: string; error?: string;
    user_speaking?: boolean; speaking?: boolean };
}
declare global {
  interface Window {
    camera: {
      request(method: 'ready' | 'authorize' | 'start' | 'stop' | 'frame' | 'pin', payload?: object): Promise<unknown>;
      onEvent(callback: (event: CameraEvent) => void): () => void;
      onDisconnected(callback: () => void): () => void;
      onStart(callback: () => void): () => void;
    };
  }
}

function CameraPage() {
  const video = useRef<HTMLVideoElement>(null);
  const active = useRef(false);
  const starting = useRef(false);
  const startCamera = useRef<() => Promise<void>>();
  startCamera.current = start;
  const epoch = useRef(0);
  const startedRevision = useRef(0);
  const stream = useRef<MediaStream>();
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const deadline = useRef<ReturnType<typeof setTimeout>>();
  const [status, setStatus] = useState<'off' | 'starting' | 'waiting' | 'active'>('off');
  const [error, setError] = useState('');
  const [activity, setActivity] = useState('');
  const [connectedAt, setConnectedAt] = useState<number>();
  const [elapsed, setElapsed] = useState(0);
  const [mirrored, setMirrored] = useState(true);
  const [pinned, setPinned] = useState(false);
  const [pinPending, setPinPending] = useState(false);
  const [deviceMenu, setDeviceMenu] = useState(false);
  const chooser = useRef<HTMLDivElement>(null);
  const deviceButton = useRef<HTMLButtonElement>(null);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [device, setDevice] = useState('');

  useEffect(() => {
    if (!connectedAt) { setElapsed(0); return; }
    const tick = () => setElapsed(Math.floor((Date.now() - connectedAt) / 1000));
    tick();
    const interval = setInterval(tick, 1000);
    return () => clearInterval(interval);
  }, [connectedAt]);

  useEffect(() => {
    if (!deviceMenu) return;
    let mounted = true;
    void navigator.mediaDevices.enumerateDevices().then((items) => {
      if (mounted) setDevices(items.filter((item) => item.kind === 'videoinput'));
    }).catch(() => {});
    const outside = (event: PointerEvent) => {
      if (!chooser.current?.contains(event.target as Node)) setDeviceMenu(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setDeviceMenu(false); deviceButton.current?.focus(); }
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      mounted = false;
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [deviceMenu]);

  async function togglePin() {
    if (pinPending) return;
    setPinPending(true);
    try {
      const result = await window.camera.request('pin', { enabled: !pinned }) as { pinned: boolean };
      setPinned(result.pinned);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setPinPending(false); }
  }

  function release() {
    active.current = false;
    starting.current = false;
    ++epoch.current;
    clearTimeout(timer.current);
    clearTimeout(deadline.current);
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = undefined;
    if (video.current) video.current.srcObject = null;
    setStatus('off');
    setActivity('');
    setConnectedAt(undefined);
  }
  async function stop(message = '') {
    release();
    setError(message);
    await window.camera.request('stop', { error: message }).catch(() => {});
  }
  useEffect(() => {
    let mounted = true;
    const requested = window.camera.onStart(() => { void startCamera.current?.(); });
    void window.camera.request('ready').then((result) => {
      if (!mounted) return;
      const ready = result as { start: boolean; pinned: boolean };
      setPinned(ready.pinned);
      if (ready.start) void startCamera.current?.();
    }).catch((cause) => { if (mounted) setError(String(cause)); });
    const unsubscribe = window.camera.onEvent((event) => {
      if (!active.current) return;
      const state = event.state;
      // Engine events and IPC responses can arrive on different renderer queues.
      if (state.revision <= startedRevision.current) return;
      if (event.type === 'camera.stopped') {
        void stop(state.camera_error || ''); return;
      }
      if (event.type === 'voice.error') {
        void stop(state.error || '语音连接出现错误'); return;
      }
      if (event.type === 'camera.frame_sent') {
        setStatus('active');
      }
      if (event.type === 'camera.waiting') setStatus('waiting');
      setActivity(state.speaking ? '正在回应' : state.user_speaking ? '正在聆听' : '通话中');
    });
    const disconnected = window.camera.onDisconnected(() => void stop('连接已断开，请重新开启视频'));
    // The main process revokes the token on close/navigation, even after this renderer is gone.
    const unload = () => release();
    window.addEventListener('beforeunload', unload);
    return () => {
      mounted = false;
      requested(); unsubscribe(); disconnected(); release();
      window.removeEventListener('beforeunload', unload);
    };
  }, []);

  async function start() {
    if (active.current || starting.current) return;
    starting.current = true;
    setError(''); setDeviceMenu(false); setStatus('starting');
    const generation = ++epoch.current;
    const current = () => generation === epoch.current;
    let lastVideoTime = -1;
    let sequence = 0;
    try {
      await window.camera.request('authorize');
      if (!current()) return;
      const media = await navigator.mediaDevices.getUserMedia({ audio: false, video: {
        width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 15, max: 15 },
        ...(device ? { deviceId: { exact: device } } : {}),
      } });
      if (!current()) { media.getTracks().forEach((track) => track.stop()); return; }
      stream.current = media;
      media.getVideoTracks()[0].onended = () => { if (current()) void stop('摄像头连接已结束'); };
      const element = video.current!;
      element.srcObject = media;
      await element.play();
      if (!current()) return;
      setDevices((await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'videoinput'));
      if (!current()) return;
      const result = await window.camera.request('start') as { revision: number };
      if (!current()) return;
      startedRevision.current = result.revision;
      active.current = true;
      starting.current = false;
      setStatus('waiting');
      setActivity('通话中');
      setConnectedAt(Date.now());
      const canvas = document.createElement('canvas');
      const context = canvas.getContext('2d', { alpha: false })!;
      const sendFrame = async () => {
        if (!current() || !active.current) return;
        if (element.readyState < 2 || element.currentTime === lastVideoTime) {
          timer.current = setTimeout(sendFrame, 200); return;
        }
        lastVideoTime = element.currentTime;
        const scale = Math.min(640 / element.videoWidth, 480 / element.videoHeight, 1);
        canvas.width = Math.round(element.videoWidth * scale);
        canvas.height = Math.round(element.videoHeight * scale);
        // Mirroring affects only the preview, never the uploaded camera frame.
        context.drawImage(element, 0, 0, canvas.width, canvas.height);
        const image = canvas.toDataURL('image/jpeg', 0.7).split(',')[1];
        try {
          deadline.current = setTimeout(() => { if (current()) void stop('画面上传超时，摄像头已关闭'); }, 7000);
          await window.camera.request('frame', { sequence: sequence++, captured_at: Date.now() / 1000, image });
          clearTimeout(deadline.current);
          if (current()) timer.current = setTimeout(sendFrame, 1010);
        } catch (cause) { if (current()) void stop(cause instanceof Error ? cause.message : String(cause)); }
      };
      void sendFrame();
    } catch (cause) {
      if (current()) await stop(cause instanceof Error ? cause.message : String(cause));
    }
  }

  const connected = status === 'waiting' || status === 'active';
  const duration = `${Math.floor(elapsed / 60).toString().padStart(2, '0')}:${(elapsed % 60).toString().padStart(2, '0')}`;
  const action = status === 'off' ? '开启视频' : status === 'starting' ? '取消连接' : '关闭视频';
  return <main className="call-window" data-status={status}>
    <header className="call-header">
      <span className="window-button-space" aria-hidden="true"/>
      <time className="call-time" aria-label={`通话时长 ${duration}`}>{duration}</time>
      <div className="window-tools">
        <button className="pin-button" aria-label="置顶窗口" title={pinned ? '取消置顶' : '置顶窗口'} aria-pressed={pinned} disabled={pinPending} onClick={() => void togglePin()}><Pin size={15}/></button>
      </div>
    </header>
    <section className={`preview ${mirrored ? 'mirrored' : ''}`} aria-label="摄像头预览">
      <video ref={video} autoPlay muted playsInline aria-label="实时画面" />
      <div className={`call-status ${connected ? 'connected' : ''}`}>
        {status === 'starting' ? <LoaderCircle size={13} className="spin" aria-hidden="true"/> : <span className="status-dot"/>}
        <span className="status" role="status">{status === 'off' ? '视频未开启' : status === 'starting' ? '正在连接' : activity || '通话中'}</span>
      </div>
      {status === 'off' && !error && <div className="placeholder"><CameraOff size={32} strokeWidth={1.4}/><span>准备好见面了吗</span></div>}
      {connected && <span className="self-label">我</span>}
      {error && <div className="error" role="alert"><AlertCircle size={16}/><span>{error}</span></div>}
    </section>
    <footer className="call-controls">
      <div className="control-item device-chooser" ref={chooser}>
        <button ref={deviceButton} className="control-button device-button" aria-label="选择摄像头" title="选择摄像头" aria-expanded={deviceMenu} aria-controls="camera-devices" onClick={() => setDeviceMenu(!deviceMenu)}><Camera size={21}/><ChevronDown size={10} className="chevron"/></button>
        <span>摄像头</span>
        {deviceMenu && <div className="device-menu" id="camera-devices">
          <label htmlFor="camera-device">摄像头</label>
          <select id="camera-device" aria-label="摄像头设备" title={status === 'off' ? '选择摄像头' : '结束视频后可切换摄像头'} value={device} disabled={status !== 'off'} onChange={(event) => { setDevice(event.target.value); setDeviceMenu(false); }}><option value="">默认摄像头</option>{devices.map((d) => <option key={d.deviceId} value={d.deviceId}>{d.label || '摄像头'}</option>)}</select>
        </div>}
      </div>
      <div className="control-item primary-control">
        <button className={`control-button ${status === 'off' ? 'start' : 'stop'}`} aria-label={action} title={action} onClick={() => status === 'off' ? void start() : void stop()}>{status === 'off' ? <Video size={25}/> : <PhoneOff size={25}/>}</button>
        <span>{action}</span>
      </div>
      <div className="control-item">
        <button className="control-button mirror-button" aria-label="镜像预览" title={mirrored ? '关闭镜像预览' : '开启镜像预览'} aria-pressed={mirrored} onClick={() => setMirrored(!mirrored)}><FlipVertical2 size={21}/>{mirrored && <Check size={9} className="check"/>}</button>
        <span>镜像</span>
      </div>
    </footer>
  </main>;
}
createRoot(document.getElementById('app')!).render(<CameraPage/>);
