---
name: macos-music
description: Operate Apple Music on macOS for playback, track navigation, search, queue, shuffle, repeat, seeking, volume, lyrics, and queue inspection. 适用于音乐播放、暂停、切歌、搜索歌曲和播放队列等请求；verify each requested state without open-ended observation loops.
---

# Apple Music

Use the `desktop_*` tools and keep all actions inside Music. Preserve the user's current queue and playback state unless the request requires changing them.

## Observe before acting

Read Music once and identify the latest elements relevant to the request. Prefer these stable mini-player identifiers when present:

- `Music.miniPlayer.playbackTransportControl`: play or pause;
- `Music.miniPlayer.leadingTransportControl`: previous or restart current track;
- `Music.miniPlayer.trailingTransportControl`: next track;
- `Music.miniPlayer.title`: current track;
- `Music.miniPlayer.playbackSlider`: progress and seeking;
- `Music.miniPlayer.shuffleButton` and `Music.miniPlayer.repeatButton`;
- `Music.miniPlayer.contextMenu`: more actions;
- `Music.miniPlayer.volumeButton`, `lyricsButton`, `queueButton`, and `airplayButton`.

Element indexes can change after any navigation or menu action. Use identifiers when possible and refresh state before using an index from an older observation.

Interpret the playback button as the action it would perform:

- `播放` / `Play` means playback is currently paused;
- `暂停` / `Pause` means playback is currently active.

The container field `Music.miniPlayer.contentView[...isPlaying=false]` and the progress slider can be stale. They do not override the transport button, visible icon, a changed title, or updated human-readable time.

## Playback and track navigation

- **Play or resume:** if the control already says `暂停` / `Pause`, do nothing. Otherwise click it once and verify that it says `暂停` / `Pause`.
- **Pause:** if the control already says `播放` / `Play`, do nothing. Otherwise click it once and verify that it says `播放` / `Play`.
- **Next:** click `trailingTransportControl` once. Verify that the title changed, or that the interface otherwise identifies the requested next item.
- **Previous:** click `leadingTransportControl` once and observe. Music may restart the current track when it has already played for a while. If the title is unchanged but the readable position reset near the beginning, one additional click is allowed to reach the previous track. Stop after the title changes or the requested restart is established.

Never toggle play/pause merely to test whether a stale field is live.

## Find and play music

For a track, artist, album, or playlist request:

1. Open the Music Search sidebar destination. Use the actual search field with `desktop_set_value`; `filterField` only filters the current view and is not global search. Search results may submit automatically.
2. If the user asked for library content, choose the library results scope when offered. A playlist may require selecting the library scope or scrolling the sidebar.
3. Resolve ambiguous results using the visible title together with artist, album, or playlist name. Do not choose solely from a partial title when multiple candidates exist.
4. To play a track result, double-click that track. For an album, artist, or playlist, open the matching result and use its visible play control.
5. Verify the mini-player title and playback control. If the exact requested result cannot be distinguished, ask for clarification or report the ambiguity instead of guessing.

If navigation unexpectedly enters the wrong subview, use the visible `backBtn` and observe again rather than continuing from stale indexes.

## Queue and collection actions

- To enqueue an item, open its More menu or use `desktop_perform_secondary_action`, then select `Play Next` / `接着播放` or `Play Last` / `最后播放` as requested.
- Do not replace the current playback merely to verify that an item was queued. Inspect the queue panel when confirmation is required.
- Only add, remove, download, favorite, or modify library/playlist content when the user explicitly asked for that mutation. Verify the resulting menu state, list membership, or visible confirmation.

## Shuffle, repeat, seeking, and volume

- **Shuffle or repeat:** read the button's current selected/value/description state first. Click only when it differs from the requested state, then verify the new state. Repeat may cycle through off, all, and one; stop only at the requested mode.
- **Seek:** use `playbackSlider` only when it exposes a settable value. Convert the user's target into the slider's represented range, set it once, and verify using the readable elapsed time. If the accessible slider is stale or not settable, do not simulate repeated clicks across it.
- **Volume:** open `volumeButton`, identify the volume control that appears, change only that control, and verify its value. Do not change macOS system volume unless explicitly requested.
- **Lyrics or queue:** toggle the corresponding mini-player button and verify that the requested panel appeared or disappeared.
- **Output device:** opening AirPlay is read-only; selecting a different output changes routing and must match an explicit user request.

## Bounded verification

Normally use one action followed by one verification. For a simple operation:

- use at most two post-action state reads;
- retry one transient observation or screenshot error once;
- stop immediately when the requested state has sufficient evidence;
- do not perform an extra state-changing action for confirmation;
- if a stale accessibility field prevents stronger proof, report exactly what was verified and what remained unobservable.

For explicit playback-progress verification, accept either an advanced readable time or a track transition while the control remains `暂停` / `Pause`. A track transition already proves continued playback; do not wait for the new track's slider to refresh.

In the final result, cite only the observations and actions that establish the requested outcome.
