import { useEffect, useRef, useState } from 'react';
import { reportError } from '@/lib/feedback';

export const fieldClass = 'w-full bg-black/20 border border-white/10 rounded p-2 text-xs focus:border-primary outline-none disabled:opacity-40';

// Commit on blur, not each keystroke: polling cannot overwrite a half-typed value.
export function NumberField({ value, onCommit, min, max, placeholder, label, disabled = false }: {
  value: number | null | undefined;
  onCommit: (value: number | null) => Promise<unknown>;
  min?: number;
  max?: number;
  placeholder?: string;
  label: string;
  disabled?: boolean;
}) {
  const [draft, setDraft] = useState(value == null ? '' : String(value));
  const focused = useRef(false);
  useEffect(() => { if (!focused.current) setDraft(value == null ? '' : String(value)); }, [value]);
  const commit = () => {
    focused.current = false;
    const failed = (error: unknown) => { setDraft(value == null ? '' : String(value)); reportError(error); };
    if (draft === '' && placeholder) {
      if (value != null) void onCommit(null).catch(failed);
      return;
    }
    const next = Number(draft);
    if (!draft.trim() || !Number.isInteger(next) || (min != null && next < min) || (max != null && next > max)) {
      setDraft(value == null ? '' : String(value));
      reportError(new Error(`${label} must be a whole number${min != null ? ` from ${min}` : ''}${max != null ? ` to ${max}` : ''}.`));
      return;
    }
    if (next !== value) void onCommit(next).catch(failed);
  };
  return <input type="number" aria-label={label} className={fieldClass} min={min} max={max} value={draft}
    disabled={disabled} placeholder={placeholder} onFocus={() => { focused.current = true; }}
    onChange={event => setDraft(event.target.value)} onBlur={commit}
    onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); }} />;
}

export function TextSetting({ value, onCommit, label, rows = 3 }: {
  value: string;
  onCommit: (value: string) => Promise<unknown>;
  label: string;
  rows?: number;
}) {
  const [draft, setDraft] = useState(value);
  const focused = useRef(false);
  useEffect(() => { if (!focused.current) setDraft(value); }, [value]);
  return <textarea aria-label={label} rows={rows} className={`${fieldClass} resize-y`} value={draft}
    onFocus={() => { focused.current = true; }} onChange={event => setDraft(event.target.value)}
    onBlur={() => { focused.current = false; if (draft !== value) void onCommit(draft).catch(error => { setDraft(value); reportError(error); }); }} />;
}
