---
name: macos-wechat
description: Operate WeChat on macOS to open the app, find a contact or chat, read the visible conversation, and send an explicitly requested message. 适用于打开微信、查找联系人、进入会话和发送明确消息；使用有界验证，不要在无障碍树稀疏时重复盲点。
---

# WeChat for macOS

Keep every action inside WeChat. Never inspect or operate another application
while completing a WeChat request.

## Open and focus

1. Read WeChat once with `desktop_get_app_state`.
2. If the window exists but only its window chrome or menu bar is exposed, use
   the window's `Raise` secondary action once. Do not repeatedly click guessed
   title-bar coordinates: `noWindowsAvailable` means the coordinate route is
   not usable for the current window state.
3. After raising, prefer keyboard navigation or a fresh screenshot. Never
   reuse coordinates from an older observation.

## Find a contact or chat

1. Use `super+f` once to focus WeChat search, then type the exact contact name.
2. Read the refreshed state once. If search results are not present in the
   accessibility tree, inspect the current screenshot and click the exact
   visible result from that screenshot.
3. Verify the opened chat title matches the requested contact before entering
   message text. Similar names are not interchangeable. If the exact contact
   cannot be distinguished, stop as blocked and ask the user to clarify.

## Send a message

1. The user must have explicitly provided both the intended recipient and the
   message content. Do not infer either from another turn or another chat.
2. Enter the exact requested content into the current chat input. Before the
   irreversible send action, verify the chat title and input text from the
   newest available state.
3. Send once, then perform one final read. Treat a visible outgoing bubble with
   the exact text in the intended chat as sufficient evidence.

## Bounded recovery

- Retry one transient read or focus error once.
- After one failed coordinate click, switch to Raise plus keyboard/screenshot;
  do not repeat the same click.
- Do not open Music or any unrelated app as a diagnostic step.
- For a simple contact-message request, stop after eight tool actions. If the
  exact chat and outgoing message cannot be verified by then, report `blocked`
  with the observed obstacle instead of continuing an open-ended loop.
