const { clampFrame, petSize, scalePetCrop } = require('./layout.cjs');

function targetDisplay(displays, primary, target, label = '') {
  if (target === 'primary') return primary;
  const exact = displays.find(display => String(display.id) === target);
  if (!label) return exact || primary;
  if (exact?.label === label) return exact;
  // Display IDs can change after reconnecting or rebooting; avoid guessing between identical names.
  const matches = displays.filter(display => display.label === label);
  return matches.length === 1 ? matches[0] : primary;
}

function petAnchor(pet) {
  return { x: pet.origin.x + pet.crop.x + pet.crop.width / 2,
    y: pet.origin.y + pet.crop.y + pet.crop.height };
}

function constrainPetOrigin(origin, crop, area) {
  const frame = clampFrame({ x: origin.x + crop.x, y: origin.y + crop.y,
    width: crop.width, height: crop.height }, {
    x: area.x + 8, y: area.y + 8, width: Math.max(1, area.width - 16), height: Math.max(1, area.height - 16),
  });
  return { x: frame.x - crop.x, y: frame.y - crop.y };
}

function petPlacement(pet, scale, area, center = false, fitVisible = true) {
  const measured = fitVisible && pet.hasBounds;
  let initial = pet.initialCrop || { crop: pet.crop, viewport: pet.viewport };
  if (measured) {
    // Reserve fixed motion room on independent displays without per-frame resizing.
    const { crop, viewport } = initial;
    const x = Math.max(0, Math.floor(crop.x - crop.width * .12));
    const y = Math.max(0, Math.floor(crop.y - crop.height * .06));
    initial = { viewport, crop: { x, y,
      width: Math.min(viewport.width, Math.ceil(crop.x + crop.width * 1.12)) - x,
      height: Math.min(viewport.height, Math.ceil(crop.y + crop.height * 1.06)) - y,
    } };
  }
  const padding = measured ? 18 : 16;
  // The renderer's transparent margins may extend offscreen; only its cropped window must fit.
  const viewport = petSize(scale, {
    width: Math.max(1, area.width - padding) / (measured ? initial.crop.width / initial.viewport.width : 1),
    height: Math.max(1, area.height - padding) / (measured ? initial.crop.height / initial.viewport.height : 1),
  });
  const crop = pet.hasBounds ? scalePetCrop(initial, viewport) : { x: 0, y: 0, ...viewport };
  const limit = fitVisible ? crop : { x: 0, y: 0, ...viewport };
  const anchor = measured ? petAnchor(pet) : null;
  const origin = center ? {
    x: area.x + (area.width - limit.width) / 2 - limit.x,
    y: area.y + area.height - limit.height - limit.y - 30,
  } : measured ? {
    x: anchor.x - crop.x - crop.width / 2,
    y: anchor.y - crop.y - crop.height,
  } : pet.origin;
  return { viewport, crop, origin: constrainPetOrigin(origin, limit, area) };
}

function primaryBubbleFrame(previous, area, requestedHeight) {
  const width = Math.min(440, area.width - 16);
  const height = Math.min(Math.max(228, Math.min(600, requestedHeight)), area.height - 32);
  const onScreen = previous.x >= area.x && previous.y >= area.y &&
    previous.x < area.x + area.width && previous.y < area.y + area.height;
  return clampFrame({
    x: onScreen ? previous.x : area.x + area.width - width - 26,
    y: onScreen ? previous.y : area.y + area.height - height - 30,
    width, height,
  }, { x: area.x + 8, y: area.y + 8, width: area.width - 16, height: area.height - 16 });
}

module.exports = { targetDisplay, petAnchor, petPlacement, constrainPetOrigin, primaryBubbleFrame };
