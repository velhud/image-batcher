import { useEffect, useState } from 'react';
import { BackendState, ExportBundleDTO, PreflightDTO } from '@/api/types';
import { api } from '@/api/client';
import { Button } from '@/components/common/Button';
import { useQueryClient } from '@tanstack/react-query';
import { CheckSquare, Square, Trash2, Play, Download, PlusSquare, StopCircle, ClipboardCheck, PackageOpen, X } from 'lucide-react';
import { runAction } from '@/lib/feedback';

export function Footer({ state }: { state: BackendState }) {
  const queryClient = useQueryClient();
  const rows = state.session.rows;
  const selectedIds = rows.filter(row => row.selected).map(row => row.id);
  const targetIds = selectedIds.length ? selectedIds : rows.map(row => row.id);
  const isGenerating = rows.some(row => row.status === 'Generating' || row.status === 'Queued');
  const [preflight, setPreflight] = useState<PreflightDTO | null>(null);
  const [exported, setExported] = useState<ExportBundleDTO | null>(null);
  const [message, setMessage] = useState('');
  const [working, setWorking] = useState(false);
  useEffect(() => { setPreflight(null); setExported(null); setMessage(''); }, [state.session.id]);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['state'] });
  const action = (fn: () => Promise<unknown>) => runAction(async () => {
    setWorking(true);
    try { await fn(); } finally { setWorking(false); }
  });
  const select = async (mode: 'all' | 'none' | 'missing' | 'pending') => {
    for (const row of rows) await api.updateRow(row.id, { selected: mode === 'all' || (mode === 'missing' && !row.images.length) || (mode === 'pending' && row.status !== 'Completed') });
    await refresh();
  };
  const addRows = async () => {
    const value = prompt('How many blank rows?', '10');
    if (value == null) return;
    const count = Number(value);
    if (!Number.isInteger(count) || count < 1 || count > 10000) throw new Error('Enter a whole number from 1 to 10000.');
    await api.addRows(Array(count).fill(''));
    await refresh();
  };
  const remove = async (ids: string[]) => {
    if (!ids.length || !confirm(`Delete ${ids.length} rows?`)) return;
    await api.deleteRows(ids);
    await refresh();
  };
  const check = async (ids = targetIds) => {
    const report = await api.preflight(ids);
    setExported(null); setMessage(''); setPreflight(report);
    return report;
  };
  const generate = async (ids: string[]) => {
    const report = await check(ids);
    if (!report.valid) return;
    if (rows.some(row => ids.includes(row.id) && row.status === 'Interrupted') && !confirm('Some requests were interrupted and may have completed at the provider. Start new requests for these rows?')) return;
    if (report.total_images >= state.session.global_settings.confirm_generate_threshold && !confirm(`Generate ${report.total_images} images across ${report.total_requests} requests?`)) return;
    const result = await api.generateRows(ids);
    setMessage(`Queued ${result.queued.length} rows.`);
    await refresh();
  };
  const exportImages = async () => {
    const folder = await api.selectFolder();
    if (!folder?.path) return;
    const result = await api.export(targetIds, folder.path);
    setMessage(`Exported ${result.exported} images to ${folder.path}`);
    setPreflight(null); setExported(null);
  };
  const exportBundle = async () => {
    const folder = await api.selectFolder();
    if (!folder?.path) return;
    const result = await api.exportBundle(folder.path, targetIds);
    setExported(result); setPreflight(null); setMessage('');
  };

  return <footer className="bg-surface border-t border-white/5 shrink-0 z-10 text-xs">
    {(preflight || exported || message) && <section className="relative border-b border-white/5 px-6 py-3 max-h-48 overflow-y-auto" aria-label="Batch results" aria-live="polite">
      <button aria-label="Dismiss batch results" className="absolute right-4 top-3 text-text-muted" onClick={() => { setPreflight(null); setExported(null); setMessage(''); }}><X size={14} /></button>
      {preflight && <>
        <p className={preflight.valid ? 'text-emerald-200' : 'text-amber-200'}>
          Last preflight: {preflight.valid ? 'ready' : 'needs attention'} · {preflight.rows.length} rows · {preflight.total_requests} requests · {preflight.total_images} requested images
        </p>
        <p className="text-text-muted mt-1">Checks settings and local files without generating images. Provider access is verified when a request is sent.</p>
        <ul className="mt-2 space-y-1 text-amber-200">
          {preflight.rows.filter(row => row.errors.length).map(row => <li key={row.row_id} className="whitespace-pre-wrap break-words">
            {row.prompt_id || row.row_id}: {row.errors.join('; ')}
          </li>)}
        </ul>
      </>}
      {exported && <>
        <p className={exported.missing.length ? 'text-amber-200' : 'text-emerald-200'}>Portable export: {exported.exported} images · {exported.missing.length} missing files</p>
        {!!exported.missing.length && <ul className="mt-1 text-amber-200">{exported.missing.map((file, index) => <li key={index}>{file.kind}: {file.name} ({file.row_id})</li>)}</ul>}
        <p className="text-text-muted mt-1 break-all">{exported.path}</p>
        <p className="text-text-muted mt-1">Includes the session, images, references, manifest{exported.contact_sheet ? ', and contact sheet' : ''}.</p>
        <button className="mt-2 text-primary underline" onClick={() => runAction(() => api.openPath(exported.path))}>Open export folder</button>
      </>}
      {message && <p className="text-emerald-200 break-words">{message}</p>}
    </section>}
    <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
      <div className="flex flex-wrap items-center gap-1">
        <span className="text-text-muted font-mono bg-white/5 px-2 py-1 rounded mr-1">{selectedIds.length} selected</span>
        <Button size="sm" variant="ghost" disabled={working} onClick={() => action(() => select('all'))} title="Select All"><CheckSquare size={14}/></Button>
        <Button size="sm" variant="ghost" disabled={working} onClick={() => action(() => select('none'))} title="Clear Selection"><Square size={14}/></Button>
        <Button size="sm" variant="ghost" disabled={working} onClick={() => action(() => select('missing'))}>No images</Button>
        <Button size="sm" variant="ghost" disabled={working} onClick={() => action(() => select('pending'))}>Pending</Button>
        <Button size="sm" variant="ghost" disabled={working} onClick={() => action(addRows)}><PlusSquare size={14} className="mr-1"/> Add rows</Button>
        <Button size="sm" variant="danger" disabled={working || isGenerating || !selectedIds.length} onClick={() => action(() => remove(selectedIds))} title="Delete selected rows"><Trash2 size={14}/></Button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" disabled={working || !rows.length} onClick={() => action(() => check())} title={selectedIds.length ? 'Check selected rows' : 'Check all rows'}><ClipboardCheck size={14} className="mr-1"/> Preflight</Button>
        <Button size="sm" variant="ghost" disabled={working || !rows.length} onClick={() => action(exportImages)} title={selectedIds.length ? 'Export selected images' : 'Export all images'}><Download size={14} className="mr-1"/> Images</Button>
        <Button size="sm" variant="secondary" disabled={working || !rows.length || isGenerating} onClick={() => action(exportBundle)} title={selectedIds.length ? 'Package selected rows' : 'Package all rows'}><PackageOpen size={14} className="mr-1"/> Portable export</Button>
        {isGenerating && <Button size="sm" variant="danger" onClick={() => runAction(async () => { await api.stopAll(); await refresh(); })}><StopCircle size={14} className="mr-1"/> Stop All</Button>}
        <Button size="sm" disabled={working || !rows.length || isGenerating} onClick={() => action(() => generate(targetIds))}><Play size={14} className="mr-1"/> {selectedIds.length ? 'Generate Selected' : 'Generate All'}</Button>
      </div>
    </div>
  </footer>;
}
