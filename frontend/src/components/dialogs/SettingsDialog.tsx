import { useEffect, useState } from 'react';
import { api } from '@/api/client';
import { desktopBridge } from '@/api/desktop';
import { runAction } from '@/lib/feedback';
import { BackendState } from '@/api/types';
import { Button } from '@/components/common/Button';
import { useQueryClient } from '@tanstack/react-query';
import { X, Save } from 'lucide-react';

interface SettingsDialogProps {
  isOpen: boolean;
  onClose: () => void;
  state: BackendState | undefined;
}

export function SettingsDialog({ isOpen, onClose, state }: SettingsDialogProps) {
  const queryClient = useQueryClient();
  const [concurrency, setConcurrency] = useState(2);
  const [rpm, setRpm] = useState(60);
  const [threshold, setThreshold] = useState(300);
  const [folder, setFolder] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Sync state when dialog opens
  useEffect(() => {
    if (isOpen && state) {
      setError('');
      setConcurrency(state.session.global_settings.concurrency_limit);
      setRpm(state.session.global_settings.rate_limit_rpm);
      setThreshold(state.session.global_settings.confirm_generate_threshold);
      setFolder(state.session.global_settings.export_folder || "");
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleSave = async () => {
    setLoading(true);
    setError('');
    try {
      if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 16) throw new Error('Concurrency must be a whole number from 1 to 16.');
      if (!Number.isInteger(rpm) || rpm < 1 || rpm > 6000) throw new Error('The rate limit must be a whole number from 1 to 6000.');
      if (!Number.isInteger(threshold) || threshold < 1 || threshold > 100000) throw new Error('The confirmation threshold must be a whole number from 1 to 100000.');
      await api.updateGlobalSettings({
        concurrency_limit: concurrency,
        rate_limit_rpm: rpm,
        confirm_generate_threshold: threshold,
        export_folder: folder || null,
      });
      await queryClient.invalidateQueries({ queryKey: ['state'] });
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="w-full max-w-lg bg-surface border border-white/10 rounded-xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200">
        <div className="flex items-center justify-between p-4 border-b border-white/10 bg-white/5">
          <h2 className="text-lg font-semibold">Application Settings</h2>
          <button onClick={onClose} className="text-text-muted hover:text-white transition-colors">
            <X size={20} />
          </button>
        </div>

        <div className="p-6 space-y-6">
          {error && <p role="alert" className="text-red-200 text-sm">{error}</p>}
          {/* Concurrency */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-text-muted">Concurrency Limit (Threads)</label>
            <input 
              type="number" 
              min={1} max={16}
              value={concurrency}
              onChange={e => setConcurrency(parseInt(e.target.value))}
              className="w-full bg-black/20 border border-white/10 rounded-md p-2 text-sm"
            />
            <p className="text-xs text-text-muted opacity-50">Higher values use more system resources.</p>
          </div>

          {/* RPM */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-text-muted">Rate Limit (Requests per Minute)</label>
            <input 
              type="number" 
              min={1} max={6000}
              value={rpm}
              onChange={e => setRpm(parseInt(e.target.value))}
              className="w-full bg-black/20 border border-white/10 rounded-md p-2 text-sm"
            />
          </div>

          <div className="space-y-2">
            <label htmlFor="confirmation-threshold" className="text-sm font-medium text-text-muted">Confirm batches at this many images</label>
            <input id="confirmation-threshold" type="number" min={1} max={100000}
              value={Number.isNaN(threshold) ? '' : threshold} onChange={e => setThreshold(e.target.valueAsNumber)}
              className="w-full bg-black/20 border border-white/10 rounded-md p-2 text-sm" />
          </div>

          {/* Export Folder */}
          <div className="space-y-2">
            <label className="text-sm font-medium text-text-muted">Default Export Folder</label>
            <div className="flex gap-2">
                <input 
                  type="text" 
                  value={folder}
                  onChange={e => setFolder(e.target.value)}
                  placeholder="/Users/name/Pictures/Imagen"
                  className="flex-1 bg-black/20 border border-white/10 rounded-md p-2 text-sm font-mono"
                />
                <Button variant="secondary" onClick={() => runAction(async () => {
                  const selected = await desktopBridge().selectFolder(folder);
                  if (selected) setFolder(selected);
                })}>Browse</Button>
            </div>
          </div>
        </div>

        <div className="p-4 border-t border-white/10 bg-white/5 flex justify-end gap-3">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button onClick={handleSave} disabled={loading}>
            <Save size={16} className="mr-2" />
            Save Changes
          </Button>
        </div>
      </div>
    </div>
  );
}
