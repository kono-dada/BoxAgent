const PANES = { microphone: 'Privacy_Microphone', screen: 'Privacy_ScreenCapture', accessibility: 'Privacy_Accessibility' };
class Permissions {
  constructor(system, shell, { requestScreen } = {}) {
    this.system = system; this.shell = shell; this.requestScreen = requestScreen; this.screenRequest = null;
  }
  snapshot() {
    return { microphone: this.system.getMediaAccessStatus('microphone'),
      screen: this.system.getMediaAccessStatus('screen'),
      accessibility: this.system.isTrustedAccessibilityClient(false) };
  }
  async request(key) {
    if (!Object.hasOwn(PANES, key)) throw Error('invalid_permission');
    if (key === 'microphone' && this.system.getMediaAccessStatus(key) === 'not-determined')
      await this.system.askForMediaAccess(key);
    else if (key === 'screen') {
      if (this.system.getMediaAccessStatus('screen') === 'granted') return this.snapshot();
      if (!this.requestScreen) throw Error('screen_permission_request_unavailable');
      // macOS must see an actual request from this Electron app before it can
      // show BoxAgent in the recording list. No thumbnails or frames are kept.
      if (!this.screenRequest) this.screenRequest = Promise.resolve().then(this.requestScreen).catch(() => {})
        .finally(() => { this.screenRequest = null; });
      let timer;
      try {
        await Promise.race([this.screenRequest, new Promise(resolve => { timer = setTimeout(resolve, 2500); })]);
      } finally { clearTimeout(timer); }
      if (this.system.getMediaAccessStatus('screen') !== 'granted')
        await this.shell.openExternal(`x-apple.systempreferences:com.apple.preference.security?${PANES.screen}`);
    } else {
      if (key === 'accessibility' && !this.system.isTrustedAccessibilityClient(false))
        this.system.isTrustedAccessibilityClient(true);
      await this.shell.openExternal(`x-apple.systempreferences:com.apple.preference.security?${PANES[key]}`);
    }
    return this.snapshot();
  }
}
module.exports = { Permissions };
