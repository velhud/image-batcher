import { useEffect, useRef } from 'react';
import { PreflightDTO } from '@/api/types';
import { Button } from '@/components/common/Button';
import { RequestPreview } from '@/components/common/RequestPreview';

export function SampleDialog({ report, busy, onClose, onGenerate }: {
  report: PreflightDTO;
  busy: boolean;
  onClose: () => void;
  onGenerate: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const modal = dialog.current;
    modal?.showModal();
    return () => modal?.close();
  }, []);
  return <dialog ref={dialog} aria-labelledby="sample-title" onCancel={event => {
    event.preventDefault();
    if (!busy) onClose();
  }} className="m-auto max-h-[90vh] w-11/12 max-w-lg overflow-y-auto rounded-xl border border-white/10 bg-surface p-5 text-text-main shadow-2xl backdrop:bg-black/70">
    <h2 id="sample-title" className="text-lg font-semibold">Review one-image sample</h2>
    <p className="mt-1 text-sm text-text-muted">Keeps existing images and preserves the batch image count. A matching sample counts toward Generate Missing.</p>
    {report.rows[0] && <RequestPreview row={report.rows[0]} />}
    {report.valid
      ? <p className="mt-2 text-xs text-text-muted">{report.total_requests} initial provider {report.total_requests === 1 ? 'request' : 'requests'}{report.total_requests > 0 && '. Cloud providers may bill for this sample.'}</p>
      : <p role="alert" className="mt-2 text-sm text-amber-200">{report.rows.flatMap(row => row.errors).join(' · ')}</p>}
    <div className="mt-4 flex justify-end gap-2">
      <Button autoFocus variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button>
      <Button disabled={busy || !report.valid} onClick={onGenerate}>Generate Sample</Button>
    </div>
  </dialog>;
}
