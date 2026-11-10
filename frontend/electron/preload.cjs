const { contextBridge, ipcRenderer } = require('electron');

/** @type {import('../src/api/desktop').DesktopBridge} */
const desktop = {
  backendOrigin: () => ipcRenderer.invoke('desktop:backend-origin'),
  reconnect: () => ipcRenderer.invoke('desktop:reconnect'),
  openSession: () => ipcRenderer.invoke('desktop:open-session'),
  saveSession: () => ipcRenderer.invoke('desktop:save-session'),
  selectFolder: defaultPath => ipcRenderer.invoke('desktop:select-folder', defaultPath),
  selectReferences: () => ipcRenderer.invoke('desktop:select-references'),
  copyImage: png => ipcRenderer.invoke('desktop:copy-image', png),
  copyText: text => ipcRenderer.invoke('desktop:copy-text', text),
  revealImage: path => ipcRenderer.invoke('desktop:reveal-image', path),
  openFolder: path => ipcRenderer.invoke('desktop:open-folder', path),
};

contextBridge.exposeInMainWorld('imageaiDesktop', desktop);
