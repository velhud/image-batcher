import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { createInterface } from 'node:readline';

export class DesktopBackend {
  private process?: ChildProcess;
  private ready?: Promise<string>;

  constructor(private readonly cwd: string, private readonly rendererOrigin: string) {}

  start(): Promise<string> {
    if (this.ready) return this.ready;
    const python = process.env.IMAGEAI_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
    const child = spawn(python, ['-m', 'app.backend_server', '--port', '0'], {
      cwd: this.cwd,
      env: { ...process.env, IMAGEAI_ALLOWED_ORIGINS: this.rendererOrigin },
      stdio: ['ignore', 'pipe', 'inherit'],
      windowsHide: true,
    });
    this.process = child;
    this.ready = new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        reject(new Error('The Image Batcher backend took too long to start.'));
        child.kill();
      }, 12000);
      createInterface({ input: child.stdout }).on('line', line => {
        const ready = /^IMAGEAI_READY (\d{1,5})$/.exec(line);
        if (ready && Number(ready[1]) > 0 && Number(ready[1]) <= 65535) {
          clearTimeout(timeout);
          resolve(`http://127.0.0.1:${ready[1]}`);
        }
      });
      child.once('error', error => {
        clearTimeout(timeout);
        console.error('Could not start the Image Batcher Python process.', error);
        reject(new Error('Could not start Python. Check the installed Image Batcher dependencies and interpreter path.'));
      });
      child.once('close', () => {
        clearTimeout(timeout);
        reject(new Error('The Image Batcher backend exited before it became ready. Check the Python setup and try again.'));
        if (this.process === child) {
          this.process = undefined;
          this.ready = undefined;
        }
      });
    });
    return this.ready;
  }

  origin(): Promise<string> {
    return this.ready ?? Promise.reject(new Error('The Image Batcher backend is stopped. Reconnect to reopen the workspace.'));
  }

  stop(): void {
    this.process?.kill();
  }
}
