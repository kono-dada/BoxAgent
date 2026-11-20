// Source launches use the same native permission checks as the installed app,
// without running onboarding or opening its encrypted credential store.
const LABELS = { microphone: '麦克风', speech: '语音识别', screen: '屏幕录制', accessibility: '辅助功能' };
const STATUS = { granted: '已授权', denied: '未授权', restricted: '系统限制', 'not-determined': '尚未申请', unavailable: '不可用' };
class LocalPermissions {
  constructor({ permissions, dialog, record = () => {} }) {
    Object.assign(this, { permissions, dialog, record });
    this.showing = null;
  }
  snapshot() { return this.permissions.snapshot(); }
  async refresh() {
    const snapshot = await this.permissions.refresh();
    this.record(snapshot);
    return snapshot;
  }
  missing() {
    const snapshot = this.snapshot();
    return Object.keys(LABELS).some(key => snapshot[key] !== 'granted' && snapshot[key] !== true);
  }
  show() {
    if (!this.showing) this.showing = this.review().finally(() => { this.showing = null; });
    return this.showing;
  }
  async review() {
    for (;;) {
      const snapshot = await this.refresh();
      const keys = Object.keys(LABELS);
      const detail = keys.map(key => `${LABELS[key]}：${typeof snapshot[key] === 'boolean' ? (snapshot[key] ? '已授权' : '未授权') : (STATUS[snapshot[key]] || '未知')}`).join('\n');
      const { response } = await this.dialog.showMessageBox({
        type: 'info', title: 'BoxAgent · 本地脚本权限', message: '本地运行的系统权限',
        detail: `${detail}\n\n点击对应按钮申请或打开系统设置。此处不修改 Key、模型或微信登录。录屏和辅助功能变更后，可能需要退出并重新执行启动脚本。`,
        buttons: [...keys.map(key => LABELS[key]), '刷新状态', '关闭'],
        defaultId: 4, cancelId: 5, noLink: true,
      });
      if (response === 5) return this.snapshot();
      if (response >= 0 && response < keys.length) {
        try { await this.permissions.request(keys[response]); }
        catch (error) {
          await this.dialog.showMessageBox({ type: 'warning', message: '权限申请未完成', detail: error.message });
        }
        // Let the user finish the system dialog before reopening this panel.
        return this.refresh();
      }
    }
  }
  close() { this.permissions.close(); }
}
module.exports = { LocalPermissions };
