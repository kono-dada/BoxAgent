const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('boxagentSetup', {
  request: async (method, params) => {
    try { return await ipcRenderer.invoke('setup:request', method, params); }
    catch { throw Error('setup_request_failed'); }
  },
  subscribe: callback => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('setup:state', listener);
    return () => ipcRenderer.removeListener('setup:state', listener);
  },
});
