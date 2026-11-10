import { ImageResultDTO } from '@/api/types';

export function ResultDetails({ image }: { image: ImageResultDTO }) {
  const details = image.metadata || {};
  const settings = details.generation_settings;
  const references = Array.isArray(details.reference_images) ? details.reference_images : [];
  const fields = [
    ['Provider', details.provider],
    ['Model', details.model],
    ['Prompt ID', details.prompt_id],
    ['Category ID', details.category_id],
    ['Actual size', details.width && details.height ? `${details.width}×${details.height}` : null],
    ['Format', details.output_format],
    ['Provider request ID', details.request_id],
    ['File SHA-256', details.sha256],
  ].filter(([, value]) => typeof value === 'string' && value.length > 0);
  return <aside aria-label="Result details" className="min-h-0 max-h-[40%] sm:max-h-none w-full sm:w-80 shrink-0 overflow-y-auto border-t sm:border-t-0 sm:border-l border-white/10 bg-surface p-4 text-xs">
    <h2 className="text-base font-semibold mb-3">Result details</h2>
    {fields.length > 0 && <dl className="space-y-2">
      {fields.map(([label, value]) => <div key={label}>
        <dt className="text-text-muted">{label}</dt>
        <dd className="break-all">{value}</dd>
      </div>)}
    </dl>}
    {typeof details.full_prompt === 'string'
      ? <div className="mt-4">
          <h3 className="font-semibold">Recorded prompt</h3>
          <p className="mt-1 whitespace-pre-wrap break-words rounded bg-black/30 p-2">{details.full_prompt}</p>
          {settings?.negative_prompt && <p className="mt-2 whitespace-pre-wrap break-words text-text-muted">Avoid · {settings.negative_prompt}</p>}
        </div>
      : <p className="mt-3 text-text-muted">No original request was recorded for this image.</p>}
    {references.length > 0 && <div className="mt-4">
      <h3 className="font-semibold">Recorded references</h3>
      <ul className="mt-1 space-y-2">
        {references.map((ref, index) => <li key={index} className="break-all">
          {String(ref?.source_path || ref?.path || 'Reference').split(/[/\\]/).pop()}
          {typeof ref?.sha256 === 'string' && <div className="text-text-muted">{ref.sha256}</div>}
        </li>)}
      </ul>
    </div>}
    {settings && <details className="mt-4">
      <summary className="cursor-pointer font-semibold">Recorded generation settings</summary>
      <pre className="mt-1 whitespace-pre-wrap break-all rounded bg-black/30 p-2">{JSON.stringify(settings, null, 2)}</pre>
    </details>}
  </aside>;
}
