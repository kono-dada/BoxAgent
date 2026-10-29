const { contextBridge, ipcRenderer } = require('electron');
function subscribe(channel, callback) {
  const listener = (_event, value) => callback(value);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
}
contextBridge.exposeInMainWorld('camera', {
  request: async (method, payload) => {
    try { return await ipcRenderer.invoke('camera:request', method, payload); }
    catch (error) { throw Error(String(error.message).replace(/^Error invoking remote method '[^']+': (?:Error: )?/, '')); }
  },
  onEvent: (callback) => subscribe('engine:event', callback),
  onDisconnected: (callback) => subscribe('camera:disconnected', callback),
  onStart: (callback) => subscribe('camera:start', callback),
});
