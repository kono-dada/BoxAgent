const { contextBridge, ipcRenderer } = require("electron");
function subscribe(channel, callback) {
  const listener = (_event, value) => callback(value);
  ipcRenderer.on(channel, listener);
  return () => {
    ipcRenderer.removeListener(channel, listener);
  };
}
contextBridge.exposeInMainWorld("boxagent", {
  request: async (method, params) => {
    try {
      return await ipcRenderer.invoke("engine:request", method, params);
    } catch (error) {
      // Electron's transport prefix is not a useful product error message.
      throw new Error(String(error.message || error).replace(/^Error invoking remote method '[^']+': (?:Error: )?/, ""));
    }
  },
  errors: () => ipcRenderer.invoke("engine:errors"),
  onState: (callback) => subscribe("engine:state", callback),
  onDiagnostic: (callback) => subscribe("engine:diagnostic", callback),
  onEvent: (callback) => subscribe("engine:event", callback),
  onDesktop: (callback) => subscribe("desktop:state", callback),
  onPet: (callback) => subscribe("desktop:pet", callback),
  onInteract: (callback) => subscribe("desktop:interact", callback),
  onNewSession: (callback) => subscribe("desktop:new-session", callback),
  desktopAction: (action, params) =>
    ipcRenderer.send("desktop:action", action, params),
});
