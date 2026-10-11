const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('boxagent', {
  request: (method, params) => ipcRenderer.invoke('engine:request', method, params),
  errors: () => ipcRenderer.invoke('engine:errors'),
  onState: callback => ipcRenderer.on('engine:state', (_event, state) => callback(state)),
  onDiagnostic: callback => ipcRenderer.on('engine:diagnostic', (_event, message) => callback(message))
});
