import { useState } from 'react';
import { api } from '@/api/client';
import { Button } from '@/components/common/Button';
import { useQueryClient } from '@tanstack/react-query';
import { X } from 'lucide-react';
import { errorMessage } from '@/lib/feedback';

interface TemplatesDialogProps {
  isOpen: boolean;
  onClose: () => void;
}

export function TemplatesDialog({ isOpen, onClose }: TemplatesDialogProps) {
  const [name, setName] = useState("My Template");
  const [templateStr, setTemplateStr] = useState("A {style} portrait of {subject}");
  const [varsStr, setVarsStr] = useState("style: photorealistic, illustration\nsubject: cat, dog");
  const [preview, setPreview] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const queryClient = useQueryClient();

  if (!isOpen) return null;

  const parseVars = () => {
    const vars: Record<string, string[]> = {};
    varsStr.split('\n').forEach(line => {
      const separator = line.indexOf(':');
      if (separator < 0) return;
      const key = line.slice(0, separator).trim();
      if (key) vars[key] = line.slice(separator + 1).split(',').map(v => v.trim()).filter(Boolean);
    });
    return vars;
  };

  const run = async (action: () => Promise<void>) => {
    setError('');
    setBusy(true);
    try {
      await action();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  };
  const template = () => ({ name, template: templateStr, variables: parseVars() });
  const handlePreview = () => run(async () => setPreview((await api.expandTemplate(template())).prompts));
  const handleApply = () => run(async () => {
    await api.applyTemplate(template());
    await queryClient.invalidateQueries({ queryKey: ['state'] });
    setPreview([]);
    onClose();
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm animate-in fade-in">
      <div className="w-full max-w-3xl bg-surface border border-white/10 rounded-xl shadow-2xl flex flex-col max-h-[90vh]">
        <div className="flex items-center justify-between p-4 border-b border-white/10 bg-white/5">
          <h2 className="text-lg font-semibold">Prompt Templates</h2>
          <button onClick={onClose}><X size={20} className="text-text-muted hover:text-white" /></button>
        </div>

        <div className="p-6 overflow-y-auto space-y-4 flex-1">
          {error && <p role="alert" className="text-red-200 text-sm">{error}</p>}
          <div className="space-y-1">
            <label htmlFor="template-name" className="text-xs text-text-muted">Template Name</label>
            <input className="w-full bg-black/20 border border-white/10 rounded p-2 text-sm" value={name} id="template-name" onChange={e => { setName(e.target.value); setPreview([]); }} />
          </div>
          
          <div className="space-y-1">
            <label htmlFor="template-template" className="text-xs text-text-muted">Template String (use {'{braces}'})</label>
            <input className="w-full bg-black/20 border border-white/10 rounded p-2 text-sm font-mono" value={templateStr} id="template-template" onChange={e => { setTemplateStr(e.target.value); setPreview([]); }} />
          </div>

          <div className="space-y-1">
            <label htmlFor="template-variables" className="text-xs text-text-muted">Variables (key: value, value)</label>
            <textarea className="w-full h-24 bg-black/20 border border-white/10 rounded p-2 text-sm font-mono resize-none" value={varsStr} id="template-variables" onChange={e => { setVarsStr(e.target.value); setPreview([]); }} />
          </div>

          <div className="flex justify-end">
            <Button variant="secondary" onClick={handlePreview} disabled={busy} size="sm">Preview Expansion</Button>
          </div>

          {preview.length > 0 && (
            <div className="bg-black/30 rounded border border-white/5 p-2 max-h-40 overflow-y-auto">
                <ul className="text-xs space-y-1 text-text-muted">
                    {preview.map((p, i) => <li key={i}>{i+1}. {p}</li>)}
                </ul>
            </div>
          )}
        </div>

        <div className="p-4 border-t border-white/10 flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
            <Button onClick={handleApply} disabled={busy || !templateStr.trim()}>Save & Add Rows</Button>
        </div>
      </div>
    </div>
  );
}
