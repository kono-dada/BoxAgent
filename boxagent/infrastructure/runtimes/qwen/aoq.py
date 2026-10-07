"""AOQ transport for Qwen Realtime on macOS.

The model protocol remains the same JSON event stream used by the WebSocket
adapter. AOQ carries those events on its data track and owns microphone,
speaker, acoustic echo cancellation, and noise suppression on the audio track.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from boxagent.infrastructure.runtimes.qwen.realtime import MODELS, QwenRealtimeSession

_AOQ_DELEGATE_CLASS = None


class AoqUnavailable(RuntimeError):
    """AOQ cannot be selected or initialized on this installation."""


@dataclass(frozen=True)
class AoqRelay:
    endpoint: str
    port: int
    route_index: int
    tcp_port: int = 0


@dataclass(frozen=True)
class AoqToken:
    token: str
    sid: str
    cert_fingerprint: str
    relay_endpoints: tuple[AoqRelay, ...]
    workspace_id_hash: str
    expires_in_seconds: int = 0

    @classmethod
    def from_payload(cls, payload):
        relays = tuple(
            AoqRelay(
                endpoint=str(item.get("endpoint") or ""),
                port=int(item.get("port") or 0),
                route_index=int(item.get("route_index", index)),
                tcp_port=int(item.get("tcp_port") or 0),
            )
            for index, item in enumerate(payload.get("clientRelayEndpoints") or ())
        )
        token = cls(
            token=str(payload.get("aoqTokenForClient") or ""),
            sid=str(payload.get("sid") or ""),
            cert_fingerprint=str(payload.get("clientRelayCertFingerprint") or ""),
            relay_endpoints=relays,
            workspace_id_hash=str(
                (payload.get("extraInfo") or {}).get("workspaceIdHash") or ""),
            expires_in_seconds=int(payload.get("sidExpiresInSecs") or 0),
        )
        missing = []
        for name, value in (
                ("aoqTokenForClient", token.token),
                ("sid", token.sid),
                ("clientRelayCertFingerprint", token.cert_fingerprint),
                ("clientRelayEndpoints", token.relay_endpoints),
                ("extraInfo.workspaceIdHash", token.workspace_id_hash)):
            if not value:
                missing.append(name)
        if any(not item.endpoint or item.port <= 0 for item in token.relay_endpoints):
            missing.append("clientRelayEndpoints.endpoint/port")
        if missing:
            raise AoqUnavailable("AOQ Token 响应缺少字段：" + ", ".join(missing))
        return token


class AoqTokenClient:
    """Exchange a DashScope API key for a short-lived AOQ connection token."""

    def __init__(self, *, api_key, workspace_id, region="cn-beijing",
                 timeout=15, opener=None):
        self.api_key = str(api_key or "")
        self.workspace_id = str(workspace_id or "")
        self.region = str(region or "cn-beijing")
        self.timeout = timeout
        self.opener = opener or urllib.request.urlopen

    @property
    def endpoint(self):
        return (f"https://{self.workspace_id}.{self.region}.maas.aliyuncs.com"
                "/api/v1/webrtc/realtime")

    def allocate(self, model):
        if not self.api_key:
            raise AoqUnavailable("未配置 DASHSCOPE_API_KEY")
        if not self.workspace_id:
            raise AoqUnavailable("未配置 BOXAGENT_DASHSCOPE_WORKSPACE_ID")
        request = urllib.request.Request(
            self.endpoint + "?" + urllib.parse.urlencode({"model": model}),
            data=b"{}",
            method="POST",
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
                "x-dashscope-rtc-transport": "moq",
            },
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise AoqUnavailable(
                f"AOQ Token 请求失败（HTTP {exc.code}）：{detail}") from exc
        except (OSError, ValueError) as exc:
            raise AoqUnavailable("AOQ Token 请求失败：" + str(exc)) from exc
        return AoqToken.from_payload(payload)


def aoq_preflight(*, sdk_dir, workspace_id, platform=None):
    """Return ``(available, reason)`` without loading native code."""
    if (platform or sys.platform) != "darwin":
        return False, "AOQ 当前只在 macOS 桌面端启用"
    if not str(workspace_id or "").strip():
        return False, "未配置 BOXAGENT_DASHSCOPE_WORKSPACE_ID"
    root = Path(sdk_dir)
    missing = [name for name in ("AoqClientSdk.framework", "PluginOpus.framework")
               if not (root / name).is_dir()]
    if missing:
        return False, "AOQ SDK 缺少 " + ", ".join(missing)
    return True, ""


class AoqFramework:
    """Thin PyObjC bridge around the vendor framework."""

    _active_lock = threading.Lock()
    _active = False

    def __init__(self, *, sdk_dir, work_dir, event_callback,
                 playback_callback=None):
        self.sdk_dir = Path(sdk_dir)
        self.work_dir = Path(work_dir)
        self.event_callback = event_callback
        self.playback_callback = playback_callback or (lambda _active: None)
        self.engine = self.delegate = None
        self._classes = {}
        self._closed = False
        self._playback_observed = False
        self._media_enabled = False
        self._owns_singleton = False

    def _load(self):
        try:
            import objc
            from Foundation import NSData, NSObject
        except ImportError as exc:
            raise AoqUnavailable("AOQ 需要 PyObjC/Cocoa 运行时") from exc
        for name in ("PluginOpus", "AoqClientSdk"):
            path = self.sdk_dir / f"{name}.framework"
            if not path.is_dir():
                raise AoqUnavailable(f"AOQ Framework 不存在：{path}")
            try:
                objc.loadBundle(name, globals(), bundle_path=str(path))
            except Exception as exc:
                raise AoqUnavailable(f"AOQ Framework 加载失败（{name}）：{exc}") from exc
        names = (
            "AoqClientEngine", "AoqCreateConfig", "AoqConnectConfig",
            "AoqTrackParam", "AoqRelayEndpoint", "AoqDataMsg",
            "AoqAudioCaptureConfig", "AoqAudioPlaybackConfig",
            "AoqAudioCodecConfig", "AoqAudioObserverConfig",
        )
        try:
            self._classes = {name: objc.lookUpClass(name) for name in names}
        except Exception as exc:
            raise AoqUnavailable("AOQ Objective-C 接口不完整：" + str(exc)) from exc

        global _AOQ_DELEGATE_CLASS
        if _AOQ_DELEGATE_CLASS is None:
            class AoqPythonDelegate(NSObject):
                @objc.typedSelector(b"v@:q@")
                def onError_message_(self, code, message):
                    self.owner.event_callback({
                        "type": "_aoq.error", "code": int(code),
                        "message": str(message),
                    })

                @objc.typedSelector(b"v@:q@")
                def onWarning_message_(self, code, message):
                    self.owner.event_callback({
                        "type": "_aoq.warning", "code": int(code),
                        "message": str(message),
                    })

                @objc.typedSelector(b"v@:q")
                def onConnectionStatusChange_(self, status):
                    self.owner.event_callback({
                        "type": "_aoq.connection", "status": int(status),
                    })

                def onStats_(self, _stats):
                    return None

                def onAudioDeviceStateChanged_(self, state):
                    self.owner.event_callback({
                        "type": "_aoq.audio_device", "state": int(state.state()),
                        "reason": int(state.reason()),
                    })

                @objc.typedSelector(b"v@:q")
                def onAudioDeviceRouteChanged_(self, route_type):
                    self.owner.event_callback({
                        "type": "_aoq.audio_route", "route": int(route_type),
                    })

                def onAudioFileState_(self, _state):
                    return None

                def onLocalAudioVolumeIndication_(self, _volume):
                    return None

                def onVideoDeviceStateChanged_(self, _state):
                    return None

                def onDataMsg_(self, message):
                    try:
                        payload = bytes(message.data()).decode("utf-8")
                        self.owner.event_callback(json.loads(payload))
                    except Exception as exc:
                        self.owner.event_callback({
                            "type": "_aoq.error", "code": -1,
                            "message": "AOQ Data Track 解析失败：" + str(exc),
                        })

                def onPlaybackAudioFrame_(self, _frame):
                    self.owner.observe_playback_frame(_frame)

            _AOQ_DELEGATE_CLASS = AoqPythonDelegate

        self._classes["NSData"] = NSData
        self.delegate = _AOQ_DELEGATE_CLASS.alloc().init()
        self.delegate.owner = self

    def observe_playback_frame(self, frame):
        """Observe native renderer activity without replacing SDK playback."""
        try:
            if bool(frame.autoGenMute()):
                return
        except Exception:
            # Older SDK builds may not expose autoGenMute. In that case the
            # callback still represents the best available playback receipt.
            pass
        if not self._playback_observed:
            self._playback_observed = True
            self.playback_callback(True)

    def start(self, token, *, microphone=True, playback=True):
        if not self._active_lock.acquire(blocking=False):
            raise AoqUnavailable("AOQ 引擎正在被另一个会话使用")
        try:
            if type(self)._active:
                raise AoqUnavailable("AOQ 引擎正在被另一个会话使用")
            type(self)._active = True
            self._owns_singleton = True
        finally:
            self._active_lock.release()
        try:
            self._load()
            self.work_dir.mkdir(parents=True, exist_ok=True)
            create = self._classes["AoqCreateConfig"].alloc().init()
            create.setWorkDir_(str(self.work_dir))
            create.setEnableDumpAudio_(False)
            create.setExtras_("{}")
            engine_class = self._classes["AoqClientEngine"]
            self.engine = engine_class.createEngine_delegate_(create, self.delegate)
            if self.engine is None:
                raise AoqUnavailable("AOQ 引擎创建失败")
            self._start_audio_devices(
                microphone=microphone, playback=playback)
            self._configure_audio_codecs()
            # The qwen3.8 AOQ handshake requires media to remain closed until
            # the service acknowledges session.update with session.updated.
            self.enable_audio_send(False)
            result = int(self.engine.connect_(self._connect_config(token)))
            if result != 0:
                raise AoqUnavailable(f"AOQ connect 返回错误码 {result}")
        except BaseException:
            self.close()
            raise

    @property
    def version(self):
        engine_class = self._classes.get("AoqClientEngine")
        return str(engine_class.getVersion()) if engine_class is not None else ""

    def _configure_audio_codecs(self):
        for method in (self.engine.setAudioEncoderConfig_,
                       self.engine.setAudioDecoderConfig_):
            config = self._classes["AoqAudioCodecConfig"].alloc().init()
            config.setTrackType_(0)  # audio
            config.setCodecType_(2)  # Opus
            config.setSampleRate_(16000)
            config.setChannel_(1)
            config.setBitrate_(32000)
            result = int(method(config))
            if result != 0:
                raise AoqUnavailable(f"AOQ 音频编解码配置失败：{result}")

    def _connect_config(self, token):
        config = self._classes["AoqConnectConfig"].alloc().init()
        config.setToken_(token.token)
        config.setSid_(token.sid)
        config.setCertFingerprint_(token.cert_fingerprint)
        config.setWorkspaceIdHash_(token.workspace_id_hash)
        endpoints = []
        for item in token.relay_endpoints:
            endpoint = self._classes["AoqRelayEndpoint"].alloc().init()
            endpoint.setRouteIndex_(item.route_index)
            endpoint.setEndpoint_(item.endpoint)
            endpoint.setPort_(item.port)
            endpoint.setTcpPort_(item.tcp_port)
            endpoints.append(endpoint)
        config.setRelayEndpoints_(endpoints)
        audio = self._classes["AoqTrackParam"].alloc().init()
        audio.setTrackType_(0)
        data = self._classes["AoqTrackParam"].alloc().init()
        data.setTrackType_(2)
        config.setPublishTracks_([audio, data])
        config.setSubscribeTracks_([audio, data])
        return config

    def _start_audio_devices(self, *, microphone=True, playback=True):
        if playback:
            config = self._classes["AoqAudioPlaybackConfig"].alloc().init()
            config.setIsExternal_(False)
            config.setChannel_(1)
            result = int(self.engine.startAudioPlayer_(config))
            if result != 0:
                raise AoqUnavailable(f"AOQ 扬声器启动失败：{result}")
        if microphone:
            config = self._classes["AoqAudioCaptureConfig"].alloc().init()
            config.setIsExternal_(False)
            config.setChannel_(1)
            result = int(self.engine.startAudioCapture_(config))
            if result != 0:
                raise AoqUnavailable(f"AOQ 麦克风启动失败：{result}")

        if playback:
            self.engine.setAudioFrameObserver_(self.delegate)
            observer = self._classes["AoqAudioObserverConfig"].alloc().init()
            observer.setSampleRate_(16000)
            observer.setChannels_(1)
            observer.setMode_(0)  # read-only
            result = int(self.engine.enableAudioFrameObserver_audioSource_config_(
                True, 3, observer))  # playback
            if result != 0:
                raise AoqUnavailable(f"AOQ 播放观察器启动失败：{result}")

    def enable_audio_send(self, enabled):
        if self.engine is None or self._closed:
            return
        result = int(self.engine.enableSendMediaStream_enable_(0, bool(enabled)))
        if result != 0:
            raise AoqUnavailable(f"AOQ 音频发送切换失败：{result}")
        self._media_enabled = bool(enabled)

    def send(self, message):
        if self.engine is None or self._closed:
            raise ConnectionError("AOQ 会话已关闭")
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        data = self._classes["NSData"].dataWithBytes_length_(payload, len(payload))
        packet = self._classes["AoqDataMsg"].alloc().init()
        packet.setData_(data)
        result = int(self.engine.sendDataMsg_(packet))
        if result != 0:
            raise ConnectionError(f"AOQ Data Track 发送失败：{result}")

    def append_audio(self, encoded):
        # AOQ downlink audio is delivered and rendered by the subscribed
        # native audio track. Data-track audio deltas must not be played twice.
        return None

    def interrupt(self):
        self.mark_playback_idle()
        if self.engine is not None and not self._closed:
            self.engine.interruptAudioPlayer_fadeMs_(0, 20)

    def begin_playback(self):
        return None

    def mark_playback_idle(self):
        if self._playback_observed:
            self._playback_observed = False
            self.playback_callback(False)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.mark_playback_idle()
        engine, self.engine = self.engine, None
        if engine is not None:
            for method in (engine.stopAudioCapture, engine.stopAudioPlayer,
                           engine.disconnect):
                try:
                    method()
                except Exception:
                    pass
        if self._owns_singleton and "AoqClientEngine" in self._classes:
            try:
                self._classes["AoqClientEngine"].destroy()
            except Exception:
                pass
        self.delegate = None
        if self._owns_singleton:
            with self._active_lock:
                type(self)._active = False
            self._owns_singleton = False


class _AoqAudioControl:
    def __init__(self, transport):
        self.transport = transport

    def clear(self):
        self.transport.interrupt()

    def append(self, data):
        self.transport.append_audio(data)

    def close(self):
        self.transport.close()


class AoqRealtimeSession(QwenRealtimeSession):
    """Qwen Realtime session using AOQ media and data tracks."""

    def __init__(self, *args, workspace_id, region="cn-beijing", sdk_dir,
                 work_dir, token_client_factory=AoqTokenClient,
                 native_factory=AoqFramework, **kwargs):
        super().__init__(*args, **kwargs)
        self.workspace_id = workspace_id
        self.region = region
        self.sdk_dir = Path(sdk_dir)
        self.work_dir = Path(work_dir)
        self.token_client_factory = token_client_factory
        self.native_factory = native_factory
        self.transport = None
        self.events = asyncio.Queue()
        self.playback_finish_task = None

    def _enqueue_native(self, event):
        if not self.closed:
            self.loop.call_soon_threadsafe(self.events.put_nowait, event)

    def _native_playback_changed(self, active):
        if not self.closed:
            self.loop.call_soon_threadsafe(self.playback_changed, active)

    async def send(self, message):
        if self.closed or self.transport is None:
            raise ConnectionError("AOQ 会话已关闭")
        if message.get("type") == "input_audio_buffer.append":
            return
        self.trace("client.request", request=message)
        self.transport.send(message)

    async def run(self):
        config = MODELS.get(self.model)
        if not config:
            raise ValueError("不支持的语音模型配置")
        self.loop = asyncio.get_running_loop()
        workers = []
        buffered = []
        try:
            client = self.token_client_factory(
                api_key=self.key, workspace_id=self.workspace_id,
                region=self.region)
            token = await asyncio.to_thread(client.allocate, self.model)
            self.transport = self.native_factory(
                sdk_dir=self.sdk_dir, work_dir=self.work_dir,
                event_callback=self._enqueue_native,
                playback_callback=self._native_playback_changed)
            self.audio = _AoqAudioControl(self.transport)
            self.ws = self  # active-transport sentinel used by shared session logic
            self.transport.start(
                token, microphone=self.microphone, playback=self.playback)
            while True:
                event = await asyncio.wait_for(self.events.get(), timeout=20)
                kind = event.get("type")
                if kind == "_aoq.connection" and event.get("status") == 2:
                    break
                if kind == "_aoq.connection" and event.get("status") == 3:
                    raise AoqUnavailable("AOQ 连接失败")
                if kind == "_aoq.error":
                    raise AoqUnavailable(event.get("message") or "AOQ 连接失败")
                buffered.append(event)
            self.trace(
                "transport.connected", transport="aoq",
                sdk_version=getattr(self.transport, "version", ""),
                token_expires_in_seconds=token.expires_in_seconds)
            await self.send_session_update(config)
            workers.append(asyncio.create_task(self.guarded_delivery()))
            workers.append(asyncio.create_task(self.guarded_notifications()))
            for event in buffered:
                await self.receive(event)
            while not self.closed:
                event = await self.events.get()
                if event.get("type") == "_aoq.closed":
                    break
                await self.receive(event)
        finally:
            self.closed = True
            for notification_id in tuple(self.pending_notifications):
                self._dispatch_notification_event(
                    "delivery_interrupted", notification_id=notification_id,
                    error="realtime_connection_closed")
            self.pending_notifications.clear()
            await self.close_resources(workers)

    async def receive(self, event):
        kind = event.get("type")
        if kind == "session.updated" and self.transport:
            self.transport.enable_audio_send(self.microphone)
            await self.inject_history()
        if kind == "response.audio.delta":
            self.trace(
                "transport.audio_delta", transport="aoq",
                encoded_bytes=len(str(event.get("delta") or "")))
        if kind == "_aoq.warning":
            self.trace("transport.warning", transport="aoq",
                       code=event.get("code"), message=event.get("message"))
            return
        if kind == "_aoq.audio_device":
            self.trace("transport.audio_device", transport="aoq", **{
                key: event.get(key) for key in ("state", "reason")})
            return
        if kind == "_aoq.audio_route":
            self.trace("transport.audio_route", transport="aoq",
                       route=event.get("route"))
            return
        if kind == "_aoq.connection":
            status = event.get("status")
            self.trace("transport.connection", transport="aoq", status=status)
            if status in {0, 3} and not self.closed:
                # Let the lifecycle owner retry transient native transport
                # failures before exposing an error to the user.
                raise AoqUnavailable("AOQ 语音连接已断开")
            return
        if kind == "_aoq.error":
            self.trace("transport.error", transport="aoq",
                       code=event.get("code"), message=event.get("message"))
            if not self.closed:
                self.emit("voice.error", error=event.get("message") or "AOQ 语音服务错误")
            return
        if kind == "response.created" and self.transport:
            self.transport.begin_playback()
        if kind in {"response.audio.done", "response.done"} and self.transport:
            self._schedule_playback_idle(event.get("response_id") or
                                         (event.get("response") or {}).get("id"))
        await super().receive(event)
        # Media and data tracks are independent. If the first native playback
        # frame wins the race against response.created, acknowledge a queued
        # notification as soon as its response identity arrives.
        if kind == "response.created" and self.playing:
            response_id = event.get("response", {}).get("id")
            notification_id = self.notification_responses.get(response_id)
            if notification_id:
                self.pending_notifications.discard(notification_id)
                self._dispatch_notification_event(
                    "playback_started", notification_id=notification_id)

    async def feed_audio(self, _chunk):
        # Native capture/AEC is owned by AOQ.
        return None

    def _schedule_playback_idle(self, response_id, delay=1.0):
        """Allow AOQ's media track to deliver frames lagging data-track done."""
        if self.playback_finish_task:
            self.playback_finish_task.cancel()

        async def finish():
            try:
                await asyncio.sleep(delay)
                if not response_id or self.response_id == response_id:
                    self.transport.mark_playback_idle()
            except asyncio.CancelledError:
                raise

        self.playback_finish_task = asyncio.create_task(finish())

    async def close_resources(self, workers):
        if self.playback_finish_task:
            self.playback_finish_task.cancel()
            workers = [*workers, self.playback_finish_task]
            self.playback_finish_task = None
        children = [*workers, *self.deliveries]
        for task in children:
            task.cancel()
        await asyncio.gather(*children, return_exceptions=True)
        if self.transport:
            self.transport.close()
        self.ws = None

    async def stop(self):
        if self.closed:
            return
        self.closed = True
        if self.transport:
            self.transport.interrupt()
            self.transport.close()
        self.events.put_nowait({"type": "_aoq.closed"})
