export const API_ERROR_EVENT = 'imageai-api-error';

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function reportError(error: unknown): void {
  window.dispatchEvent(new CustomEvent(API_ERROR_EVENT, { detail: errorMessage(error) }));
}

// The shared error banner keeps errors visible even after a dialog closes.
export function runAction(action: () => Promise<unknown>): void {
  void action().catch(reportError);
}
