import { useEffect } from 'react';

export function useHotkeys(keyCombo: string, callback: (e: KeyboardEvent) => void) {
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (document.querySelector('dialog[open]')) return;
      // Parse key combo (very basic parser for now)
      const keys = keyCombo.split('+').map(k => k.trim().toLowerCase());
      const mainKey = keys[keys.length - 1];
      
      const meta = keys.includes('cmd') || keys.includes('ctrl') || keys.includes('meta');
      const shift = keys.includes('shift');
      
      const isMetaPressed = event.metaKey || event.ctrlKey;
      const isShiftPressed = event.shiftKey;
      
      if (
        event.key.toLowerCase() === mainKey && 
        isMetaPressed === meta && 
        isShiftPressed === shift
      ) {
        event.preventDefault();
        callback(event);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [keyCombo, callback]);
}