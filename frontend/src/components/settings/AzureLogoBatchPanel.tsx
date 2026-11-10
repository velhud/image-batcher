import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, ClipboardCheck, FileDown, PauseCircle, Play, RefreshCw, RotateCcw, ShieldCheck, Square } from 'lucide-react';
import { BackendState, BatchVerificationDTO } from '@/api/types';
import { api } from '@/api/client';
import { Button } from '@/components/common/Button';
import { runAction } from '@/lib/feedback';
import { NumberField, TextSetting } from './SettingFields';

export function AzureLogoBatchPanel({ state }: { state: BackendState }) {
  const queryClient = useQueryClient();
  const [verification, setVerification] = useState<BatchVerificationDTO | null>(null);
  const gs = state.session.global_settings;
  const rows = state.session.rows;
  const completed = rows.filter((r) => r.status === 'Completed' && r.images.length > 0).length;
  const filtered = rows.filter((r) => r.status === 'Filtered').length;
  const failed = rows.filter((r) => r.status === 'Error').length;
  const remaining = Math.max(0, rows.length - completed - filtered - failed);

  const { data: providerStatus } = useQuery({
    queryKey: ['provider-status'],
    queryFn: api.providerStatus,
    refetchInterval: 5000,
  });

  const update = async (patch: Record<string, unknown>) => {
    await api.updateGlobalSettings(patch);
    await queryClient.invalidateQueries({ queryKey: ['state'] });
  };

  const applyPreset = async () => {
    await api.applyAzureLogoPreset();
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['state'] }),
      queryClient.invalidateQueries({ queryKey: ['provider-status'] }),
    ]);
  };

  const verify = async () => {
    const result = await api.verifyBatch();
    setVerification(result);
  };

  const exportResults = async () => {
    const folder = await api.selectFolder();
    if (!folder?.path) return;
    await api.export(rows.map((r) => r.id), folder.path);
  };

  const qualityValue = gs.quality <= 3 ? 'low' : gs.quality >= 8 ? 'high' : 'medium';

  if (gs.provider_id !== 'azure-openai') return <div className="space-y-2 text-xs text-text-muted">
    <Button size="sm" variant="secondary" className="w-full" onClick={() => runAction(applyPreset)}><ShieldCheck size={14} className="mr-2"/> Azure Logo Batch preset</Button>
    <p>Switches to your Azure deployment with conservative batch settings.</p>
  </div>;

  return (
    <div className="space-y-3 rounded-lg border border-cyan-400/20 bg-cyan-400/[0.04] p-3 text-xs">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 font-semibold text-cyan-100">
          <ShieldCheck size={14} />
          Azure Logo Batch
        </div>
        {providerStatus?.azure_openai.configured ? (
          <span className="inline-flex items-center gap-1 text-emerald-300"><CheckCircle2 size={12} /> Configured</span>
        ) : (
          <span className="inline-flex items-center gap-1 text-amber-300"><AlertTriangle size={12} /> Missing config</span>
        )}
      </div>

      {providerStatus && !providerStatus.azure_openai.configured && (
        <div className="rounded border border-amber-400/20 bg-amber-400/10 p-2 text-amber-100">
          Missing: {providerStatus.azure_openai.missing.join(', ')}
        </div>
      )}

      <div className="grid grid-cols-3 gap-2">
        <Metric label="Prompts" value={rows.length} />
        <Metric label="Done" value={completed} />
        <Metric label="Left" value={remaining} />
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="mb-1 block text-text-muted">Quality</label>
          <select
            value={qualityValue}
            onChange={(e) => runAction(() => update({ quality: e.target.value === 'low' ? 2 : e.target.value === 'high' ? 8 : 5 }))}
            className="w-full rounded border border-white/10 bg-black/20 p-2"
          >
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </div>
        <div>
          <label className="mb-1 block text-text-muted">Local images/min</label>
          <NumberField label="Azure image limit per minute" min={1} max={60} value={gs.rate_limit_rpm} onCommit={value => update({ rate_limit_rpm: value || 4 })} />
        </div>
      </div>

      <div className="rounded border border-white/10 bg-black/20 p-2 text-text-muted">
        {gs.num_images} images per row · Concurrency {gs.concurrency_limit}. Actual quota and pricing depend on your deployment.
      </div>

      <div>
        <div className="mb-1 flex items-center justify-between">
          <label className="text-text-muted">Prompt Wrapper</label>
          <button
            className="inline-flex items-center gap-1 text-cyan-200 hover:text-white"
            onClick={() => runAction(() => update({ prompt_wrapper: providerStatus?.azure_logo_wrapper || '' }))}
          >
            <RotateCcw size={12} /> Reset
          </button>
        </div>
        <TextSetting label="Prompt wrapper" rows={4} value={gs.prompt_wrapper || ''} onCommit={value => update({ prompt_wrapper: value })} />
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Button size="sm" onClick={() => runAction(applyPreset)}><ClipboardCheck size={14} className="mr-1" /> Preset</Button>
        <Button size="sm" variant="secondary" onClick={() => runAction(() => api.generateMissing().then(() => queryClient.invalidateQueries({ queryKey: ['state'] })))}>
          <Play size={14} className="mr-1" /> Generate Missing
        </Button>
        <Button size="sm" variant="secondary" onClick={() => runAction(() => api.retryFailed().then(() => queryClient.invalidateQueries({ queryKey: ['state'] })))}>
          <RefreshCw size={14} className="mr-1" /> Retry Failed
        </Button>
        <Button size="sm" variant="ghost" onClick={() => runAction(() => api.stopAfterCurrent())}>
          <PauseCircle size={14} className="mr-1" /> Stop After Current
        </Button>
        <Button size="sm" variant="ghost" onClick={() => runAction(() => api.stopAll())}>
          <Square size={14} className="mr-1" /> Stop All
        </Button>
        <Button size="sm" variant="ghost" onClick={() => runAction(exportResults)}>
          <FileDown size={14} className="mr-1" /> Export
        </Button>
      </div>

      <Button size="sm" variant="secondary" className="w-full" onClick={() => runAction(verify)}>
        Verify Batch
      </Button>

      {verification && (
        <div className={`rounded border p-2 ${verification.matches_prompt_count ? 'border-emerald-400/20 bg-emerald-400/10 text-emerald-100' : 'border-amber-400/20 bg-amber-400/10 text-amber-100'}`}>
          Success: {verification.successful_images}/{verification.total_prompts} · Filtered: {verification.filtered_count} · Failed: {verification.failed_count} · Missing: {verification.missing_count}
        </div>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded border border-white/10 bg-black/20 p-2">
      <div className="text-[10px] uppercase text-text-muted">{label}</div>
      <div className="mt-1 text-base font-semibold text-text-main">{value}</div>
    </div>
  );
}
