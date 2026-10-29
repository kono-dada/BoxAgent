# AOQ independent speaker output

`BOXAGENT_AOQ_OUTPUT_DEVICE` selects an output device for BoxAgent's AOQ voice
only. It does not change macOS default output, system alert output, camera
settings, or the SDK's native microphone capture.

## Configuration

Set the exact CoreAudio/PortAudio device name in your own `.env.local` or the
desktop launch environment. `12.3FHD` below is an example, not a project default:

```sh
export BOXAGENT_QWEN_TRANSPORT=aoq
export BOXAGENT_AOQ_OUTPUT_DEVICE=12.3FHD
./scripts/run-pet.sh
```

Restart BoxAgent after changing the setting. An explicitly empty value disables
the override and uses the original SDK playback path. Device names are resolved
on each voice connection; numeric device indexes are not persisted. Missing or
duplicate names cause an error, not fallback to another speaker. A configured
override also rejects a WebSocket fallback.

Personal launchers under `.runtime/` are ignored by Git and are not required.
Other group members retain native playback unless they explicitly configure an
output device. To use the original path, clear the setting in `.env.local` or
override it for this launch:

```sh
BOXAGENT_AOQ_OUTPUT_DEVICE='' ./scripts/run-pet.sh
```

## Playback path

AOQ still transports and decodes the remote audio. With an override, its player
uses `isExternal=true`; the read-only playback observer delivers 48 kHz mono
PCM16. BoxAgent validates each frame and copies the SDK-owned pointer before the
callback returns, then sends the copy to a bounded, output-only PyAudio stream.
No microphone is opened by that stream. Data-track audio deltas are not played
a second time. Interrupt clears the pending software buffer; closing the voice
session releases the stream. Hardware-buffered audio may have a short tail.

This follows the [AOQ custom playback API](https://help.aliyun.com/zh/model-studio/aoq-custom-audio-playback).
Changing the physical speaker and playback path can affect echo and interruption
behavior. Native microphone capture is retained, but equivalent echo cancellation
has not been established. Test a real two-way call at normal speaker volume;
reduce volume or disable the override if echo causes false interruptions.

## Verification

- Unit tests cover exact-device selection, no microphone, no silent fallback,
  PCM ownership, bounded buffering, disconnect/error handling, interrupt and
  close, and forwarding the option into AOQ.
- On 2026-10-28, a local tone and an output-only Qwen AOQ response successfully
  played through `12.3FHD`. The user confirmed hearing the display's speaker.
- The cloud test delivered 474240 PCM bytes to that device. Default output was
  `MacBook Air扬声器` before and after. Neither camera nor microphone was enabled
  for these output tests. These tests do not validate two-way echo cancellation
  or fix the separately observed conversation-preparation timeout.
