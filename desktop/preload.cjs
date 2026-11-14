"use strict";

const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("stockWindow", Object.freeze({
  minimize: () => ipcRenderer.invoke("stock:window-action", "minimize"),
  toggleMaximize: () => ipcRenderer.invoke("stock:window-action", "toggle-maximize"),
  close: () => ipcRenderer.invoke("stock:window-action", "close"),
  getState: () => ipcRenderer.invoke("stock:window-action", "state"),
  onStateChanged: callback => {
    if (typeof callback !== "function") throw new TypeError("Expected callback");
    const listener = (_event, value) => callback({ maximized: Boolean(value.maximized), focused: Boolean(value.focused) });
    ipcRenderer.on("stock:window-state", listener);
    return () => ipcRenderer.removeListener("stock:window-state", listener);
  },
}));
