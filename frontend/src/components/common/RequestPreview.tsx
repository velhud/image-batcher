import { PreflightRowDTO } from '@/api/types';

export function RequestPreview({ row }: { row: PreflightRowDTO }) {
  return <div className="mt-2 space-y-2 text-xs">
    <p className="text-text-muted">{row.provider} · {row.model} · {row.requested_images} {row.requested_images === 1 ? 'image' : 'images'}</p>
    {row.request && <>
      <p className="text-text-muted">Size setting · {row.request.size}</p>
      <p className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded bg-black/30 p-3 font-mono">{row.request.prompt || 'No prompt entered.'}</p>
      {row.request.avoid && <p className="whitespace-pre-wrap break-words text-text-muted">Avoid · {row.request.avoid}</p>}
      {row.request.references.length > 0 && <p className="break-words text-text-muted">References · {row.request.references.join(', ')}</p>}
    </>}
  </div>;
}
