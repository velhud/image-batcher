import { app, BrowserWindow, clipboard, dialog, ipcMain, nativeImage, net, protocol, shell } from 'electron';
import type { IpcMainInvokeEvent } from 'electron';
import { stat } from 'node:fs/promises';
import { DesktopBackend } from './backend';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const moduleDirectory = path.dirname(fileURLToPath(import.meta.url));
const assetRoot = path.resolve(moduleDirectory, '../dist');
const desktopOrigin = 'imageai://desktop';
const isDev = !app.isPackaged;
const rendererUrl = process.env.VITE_DEV_SERVER_URL || desktopOrigin;
const sessionFilters = [{ name: 'Image Batcher session', extensions: ['json'] }];
const backend = new DesktopBackend(
  isDev ? path.resolve(moduleDirectory, '../..') : path.join(process.resourcesPath, 'backend'),
  isDev && process.env.VITE_DEV_SERVER_URL ? new URL(rendererUrl).origin : desktopOrigin,
);

protocol.registerSchemesAsPrivileged([{
  scheme: 'imageai',
  privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true },
}]);

function trustedWindow(event: IpcMainInvokeEvent): BrowserWindow {
  const window = BrowserWindow.fromWebContents(event.sender);
  const url = new URL(event.senderFrame?.url || 'about:blank');
  const trusted = new URL(rendererUrl);
  if (!window || event.senderFrame !== event.sender.mainFrame ||
      url.protocol !== trusted.protocol || url.host !== trusted.host) {
    throw new Error('Desktop action requires the Image Batcher window.');
  }
  return window;
}

function registerDesktopActions() {
  ipcMain.handle('desktop:backend-origin', event => {
    trustedWindow(event);
    return backend.origin();
  });
  ipcMain.handle('desktop:reconnect', event => {
    trustedWindow(event);
    return backend.start();
  });
  ipcMain.handle('desktop:open-session', async event => {
    const result = await dialog.showOpenDialog(trustedWindow(event), {
      title: 'Open Image Batcher session', properties: ['openFile'], filters: sessionFilters,
    });
    return result.canceled ? null : result.filePaths[0];
  });
  ipcMain.handle('desktop:save-session', async event => {
    const result = await dialog.showSaveDialog(trustedWindow(event), {
      title: 'Save Image Batcher session as', defaultPath: 'session.json', filters: sessionFilters,
    });
    return result.canceled ? null : result.filePath;
  });
  ipcMain.handle('desktop:select-folder', async (event, defaultPath?: string | null) => {
    const result = await dialog.showOpenDialog(trustedWindow(event), {
      title: 'Choose export folder', properties: ['openDirectory', 'createDirectory'],
      defaultPath: typeof defaultPath === 'string' ? defaultPath : undefined,
    });
    return result.canceled ? null : result.filePaths[0];
  });
  ipcMain.handle('desktop:select-references', async event => {
    const result = await dialog.showOpenDialog(trustedWindow(event), {
      title: 'Add reference images', properties: ['openFile', 'multiSelections'],
      filters: [{ name: 'Reference images', extensions: ['png', 'jpg', 'jpeg', 'webp'] }],
    });
    return result.canceled ? [] : result.filePaths;
  });
  ipcMain.handle('desktop:copy-image', (event, png: Uint8Array) => {
    trustedWindow(event);
    if (!(png instanceof Uint8Array) || png.byteLength > 128 * 1024 * 1024) {
      throw new Error('Invalid clipboard image.');
    }
    const image = nativeImage.createFromBuffer(Buffer.from(png));
    if (image.isEmpty()) throw new Error('Could not decode the clipboard image.');
    clipboard.writeImage(image);
  });
  ipcMain.handle('desktop:copy-text', (event, text: string) => {
    trustedWindow(event);
    if (typeof text !== 'string') throw new Error('Invalid clipboard text.');
    clipboard.writeText(text);
  });
  ipcMain.handle('desktop:reveal-image', async (event, file: string) => {
    trustedWindow(event);
    if (!path.isAbsolute(file) || !(await stat(file)).isFile()) throw new Error('The image file is unavailable.');
    shell.showItemInFolder(file);
  });
  ipcMain.handle('desktop:open-folder', async (event, folder: string) => {
    trustedWindow(event);
    if (!path.isAbsolute(folder) || !(await stat(folder)).isDirectory() || /\.app$/i.test(folder)) {
      throw new Error('The export folder is unavailable.');
    }
    const error = await shell.openPath(folder);
    if (error) throw new Error(error);
  });
}

function createWindow() {
  const window = new BrowserWindow({
    width: 1280,
    height: 900,
    backgroundColor: '#0f172a',
    title: 'Image Batcher',
    webPreferences: { preload: path.resolve(moduleDirectory, '../electron/preload.cjs') },
  });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  window.webContents.on('will-navigate', event => event.preventDefault());
  void window.loadURL(rendererUrl);
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    const window = BrowserWindow.getAllWindows()[0];
    if (window) {
      if (window.isMinimized()) window.restore();
      window.focus();
    }
  });
  app.whenReady().then(() => {
    protocol.handle('imageai', request => {
      const url = new URL(request.url);
      const file = path.resolve(assetRoot, '.' + decodeURIComponent(url.pathname === '/' ? '/index.html' : url.pathname));
      const relative = path.relative(assetRoot, file);
      if (url.host !== 'desktop' || relative.startsWith('..') || path.isAbsolute(relative)) {
        return new Response(null, { status: 404 });
      }
      return net.fetch(pathToFileURL(file).href);
    });
    registerDesktopActions();
    void backend.start().catch(error => console.error(error.message));
    createWindow();
  });
  app.on('activate', () => {
    if (!BrowserWindow.getAllWindows().length) createWindow();
  });
  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
  });
  app.on('will-quit', () => backend.stop());
}
