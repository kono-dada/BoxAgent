// Real VRM + production host; the surrounding fixture has no model or microphone.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { screen } = require('electron');

exports.run = async ({ pet, stage, bubble, js, wait }) => {
  const primary = screen.getPrimaryDisplay();
  const secondary = screen.getAllDisplays().find(display => display.id !== primary.id);
  assert(secondary, 'Connect an extended display to test native hardware-mode walking');
  const output = process.env.BOXAGENT_WALK_ARTIFACTS;
  if (output) fs.mkdirSync(output, { recursive: true });
  await js(bubble, "boxagent.request('desktop_set_display_profile', {profile:'hardware'})");
  await js(bubble, `boxagent.request('desktop_set_pet_display', {id:${JSON.stringify(String(secondary.id))}})`);
  if (bubble.isVisible()) await js(bubble, "boxagent.request('desktop_toggle_bubble')");
  const animation = stage.webContents.mainFrame.frames[0];
  await wait(() => animation.executeJavaScript('Boolean(pet.debug().ready)'), 'VRM loaded');
  const before = pet.getBounds();
  if (output) fs.writeFileSync(path.join(output, 'before.png'), (await stage.webContents.capturePage()).toPNG());
  let motion, moved;
  // Allow the full introduction plus the production 15–30 second idle timer.
  for (let index = 0; index < 1100; index++) {
    motion = await animation.executeJavaScript('pet.debug().behavior');
    moved = pet.getBounds();
    if (motion.transitionKind === 'walk' && Math.abs(moved.x - before.x) > 8) break;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  assert.equal(motion.transitionKind, 'walk', 'real walking clip is playing');
  assert(Math.abs(moved.x - before.x) > 8, 'native hit window moves with the walking renderer');
  const display = screen.getDisplayMatching(moved);
  assert(moved.x >= display.bounds.x && moved.x + moved.width <= display.bounds.x + display.bounds.width);
  const frame = await js(stage, `new Promise(resolve => {
    const stop = boxagent.onPet(value => { stop(); resolve(value); });
  })`);
  const native = pet.getBounds(), scene = stage.getBounds();
  assert(Math.abs(scene.x + frame.canvas.x + frame.crop.x - native.x) <= 4, 'render and hit window stay aligned');
  if (output) fs.writeFileSync(path.join(output, 'walking.png'), (await stage.webContents.capturePage()).toPNG());
  await js(bubble, "boxagent.request('desktop_toggle_bubble')");
  await wait(() => bubble.isVisible(), 'chat opens');
  await wait(async () => (await animation.executeJavaScript('pet.debug().behavior')).transitionKind !== 'walk', 'chat pauses walking');
  const stopped = pet.getBounds();
  await new Promise(resolve => setTimeout(resolve, 800));
  assert.deepEqual(pet.getBounds(), stopped, 'character stays still during chat');
  const result = { before, walking: moved, action: motion.action, pausedWithChat: true };
  if (output) fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(result, null, 2));
  console.log('Real desktop walking passed:', JSON.stringify(result));
};
