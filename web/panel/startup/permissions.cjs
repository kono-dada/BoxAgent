const PANES = { microphone: 'Privacy_Microphone', speech: 'Privacy_SpeechRecognition', screen: 'Privacy_ScreenCapture', accessibility: 'Privacy_Accessibility' };
class Permissions {
  constructor(system, shell, { requestScreen, speech, onVoiceGranted = () => {} } = {}) {
    this.system = system; this.shell = shell; this.requestScreen = requestScreen; this.screenRequest = null;
    this.speech = speech; this.onVoiceGranted = onVoiceGranted;
  }
  snapshot() {
    return { microphone: this.system.getMediaAccessStatus('microphone'),
      speech: this.speech?.status || 'unavailable',
      screen: this.system.getMediaAccessStatus('screen'),
      accessibility: this.system.isTrustedAccessibilityClient(false) };
  }
  async refresh() {
    await this.speech?.refresh();
    if (this.snapshot().speech === 'granted' && this.snapshot().microphone === 'granted') this.onVoiceGranted();
    return this.snapshot();
  }
  close() { this.speech?.close(); }
  async request(key) {
    if (!Object.hasOwn(PANES, key)) throw Error('invalid_permission');
    if (key === 'speech') {
      if (!this.speech) throw Error('speech_permission_unavailable');
      const status = await this.speech.request();
      if (status === 'granted') this.onVoiceGranted();
      else if (['denied', 'restricted'].includes(status) && !this.speech.lastPromptRequested)
        await this.shell.openExternal(`x-apple.systempreferences:com.apple.preference.security?${PANES.speech}`);
      else if (status === 'unavailable') throw Error('speech_permission_unavailable');
      return this.snapshot();
    }
    if (this.snapshot()[key] === 'granted' || this.snapshot()[key] === true) {
      if (key === 'microphone') this.onVoiceGranted();
      return this.snapshot();
    }
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
    if (key === 'microphone' && this.snapshot().microphone === 'granted') this.onVoiceGranted();
    return this.snapshot();
  }
}
module.exports = { Permissions };
