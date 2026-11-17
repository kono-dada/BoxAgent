const PANES = { microphone: 'Privacy_Microphone', screen: 'Privacy_ScreenCapture', accessibility: 'Privacy_Accessibility' };
class Permissions {
  constructor(system, shell) { this.system = system; this.shell = shell; }
  snapshot() {
    return { microphone: this.system.getMediaAccessStatus('microphone'),
      screen: this.system.getMediaAccessStatus('screen'),
      accessibility: this.system.isTrustedAccessibilityClient(false) };
  }
  async request(key) {
    if (!Object.hasOwn(PANES, key)) throw Error('invalid_permission');
    if (key === 'microphone' && this.system.getMediaAccessStatus(key) === 'not-determined')
      await this.system.askForMediaAccess(key);
    else await this.shell.openExternal(`x-apple.systempreferences:com.apple.preference.security?${PANES[key]}`);
    return this.snapshot();
  }
}
module.exports = { Permissions };
