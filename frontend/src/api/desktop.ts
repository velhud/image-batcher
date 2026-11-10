export interface DesktopBridge {
  backendOrigin(): Promise<string>;
  reconnect(): Promise<string>;
  openSession(): Promise<string | null>;
  saveSession(): Promise<string | null>;
  selectFolder(defaultPath?: string | null): Promise<string | null>;
  selectReferences(): Promise<string[]>;
  copyImage(png: Uint8Array): Promise<void>;
  copyText(text: string): Promise<void>;
  revealImage(path: string): Promise<void>;
  openFolder(path: string): Promise<void>;
}

declare global {
  interface Window {
    imageaiDesktop?: DesktopBridge;
  }
}

export function desktopBridge(): DesktopBridge {
  if (!window.imageaiDesktop) throw new Error('Open ImageAI Desktop to use native file and clipboard actions.');
  return window.imageaiDesktop;
}

export async function copyText(text: string): Promise<void> {
  await desktopBridge().copyText(text);
}

export async function copyImage(url: string): Promise<void> {
  const desktop = desktopBridge();
  const response = await fetch(url);
  if (!response.ok) throw new Error('The saved image could not be read.');
  const bitmap = await createImageBitmap(await response.blob());
  try {
    const canvas = document.createElement('canvas');
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    canvas.getContext('2d')!.drawImage(bitmap, 0, 0);
    const png = await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('The saved image could not be copied.')), 'image/png');
    });
    await desktop.copyImage(new Uint8Array(await png.arrayBuffer()));
  } finally {
    bitmap.close();
  }
}
