import { useEffect, useRef, useState } from 'react';
import { GlobalSettingsDTO, ProviderInfo, RowDTO } from '@/api/types';
import { api, imageUrl } from '@/api/client';
import { copyImage, copyText, desktopBridge } from '@/api/desktop';
import { Badge } from '@/components/common/Badge';
import { Button } from '@/components/common/Button';
import { Play, Trash2, Copy, Image as ImageIcon, Settings2, FolderOpen, StopCircle, Files, Paperclip, XCircle, Download } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import { RowSettingsOverride } from './RowSettingsOverride';
import { ImageLightbox } from '@/components/dialogs/ImageLightbox';
import { effectiveRowSettings, modelForSettings, qualityLabel } from '@/lib/capabilities';
import { runAction } from '@/lib/feedback';

const generateId = () => {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return Math.random().toString(36).slice(2);
};

interface RowCardProps {
  row: RowDTO;
  index: number;
  globalSettings: GlobalSettingsDTO;
  providers: ProviderInfo[];
}

export function RowCard({ row, index, globalSettings, providers }: RowCardProps) {
  const [prompt, setPrompt] = useState(row.prompt || "");
  const [tags, setTags] = useState((row.tags || []).join(", "));
  const [showSettings, setShowSettings] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState<number | null>(null);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  
  const queryClient = useQueryClient();
  const busy = row.status === 'Generating' || row.status === 'Queued';
  const effective = effectiveRowSettings(globalSettings, row.settings);
  const caps = modelForSettings(providers, effective)?.capabilities;

  useEffect(() => {
    if (document.activeElement !== promptRef.current) {
      setPrompt(row.prompt || "");
    }
  }, [row.prompt]);
  useEffect(() => { setTags((row.tags || []).join(", ")); }, [row.tags]);

  const handleBlurPrompt = () => { if (prompt !== row.prompt) runAction(() => api.updateRow(row.id, { prompt })); };
  const handleBlurTags = () => { runAction(() => api.updateRow(row.id, { tags: tags.split(',').map(t => t.trim()).filter(Boolean) })); };

  const handleGenerate = async () => { 
    await api.updateRow(row.id, { prompt }); 
    const report = await api.preflight([row.id]);
    if (!report.valid) throw new Error(report.rows.flatMap(item => item.errors).join('\n'));
    if (row.status === 'Interrupted' && !confirm('This request was interrupted and may have completed at the provider. Start a new request for this row?')) return;
    await api.generateRows([row.id], row.images.length > 0);
    queryClient.invalidateQueries({ queryKey: ['state'] }); 
  };
  const handleStop = async () => { await api.cancelRow(row.id); await queryClient.invalidateQueries({ queryKey: ['state'] }); };
  const handleDelete = async () => { if (confirm("Delete?")) { await api.deleteRows([row.id]); queryClient.invalidateQueries({ queryKey: ['state'] }); }};
  const handleDuplicate = async () => { await api.duplicateRow(row.id); queryClient.invalidateQueries({ queryKey: ['state'] }); };
  const handleAddAttachments = async () => {
    const paths = await desktopBridge().selectReferences();
    const existing = new Set(row.attachments.map(item => item.file_path));
    const added = [...new Set(paths)].filter(path => !existing.has(path));
    if (!added.length) return;
    if (row.attachments.length + added.length > (caps?.max_attachments ?? 0)) {
      throw new Error(`This model accepts at most ${caps?.max_attachments ?? 0} reference images per row.`);
    }
    await api.updateRow(row.id, {
      attachments: [...row.attachments, ...added.map(file_path => ({
        id: generateId(), file_path,
        mime_type: /\.webp$/i.test(file_path) ? 'image/webp' : /\.jpe?g$/i.test(file_path) ? 'image/jpeg' : 'image/png',
      }))],
    });
    await queryClient.invalidateQueries({ queryKey: ['state'] });
  };

  const exportRow = async () => {
    const folder = await desktopBridge().selectFolder(globalSettings.export_folder);
    if (folder) await api.export([row.id], folder);
  };

  const removeAttachment = async (attId: string) => {
    const updated = (row.attachments || []).filter((a) => a.id !== attId);
    await api.updateRow(row.id, { attachments: updated });
    queryClient.invalidateQueries({ queryKey: ['state'] });
  };

  const displayImage = row.images.length > 0 ? row.images[row.images.length - 1] : null;
  const outputName = displayImage?.file_path.split(/[/\\]/).pop();
  const fullPromptPreview = globalSettings.prompt_wrapper?.trim()
    ? `${prompt.trim()}\n\n${globalSettings.prompt_wrapper.trim()}`
    : prompt;

  return (
    <>
    <div className={`group relative bg-surface/40 hover:bg-surface/60 border ${row.selected ? 'border-primary/50 bg-primary/5' : 'border-white/5'} rounded-lg p-4 transition-all duration-200`}>
      <div className="flex gap-4">
        {/* 1. Left Controls */}
        <div className="flex flex-col items-center gap-3 pt-1">
            <span className="text-xs font-mono text-text-muted opacity-50">{index}</span>
            <input 
                type="checkbox" 
                checked={row.selected}
                onChange={(e) => { const selected = e.target.checked; runAction(async () => { await api.updateRow(row.id, { selected }); await queryClient.invalidateQueries({ queryKey: ['state'] }); }); }}
                className="rounded border-white/20 bg-black/20 text-primary cursor-pointer w-4 h-4"
            />
        </div>

        {/* 2. Main Input Area */}
        <div className="flex-1 flex flex-col gap-2">
            <div className="flex items-center justify-between">
                <div className="flex gap-2 items-center">
                    <Badge status={row.status} />
                    {row.images.length > 0 && <span className="text-xs bg-white/10 px-1.5 py-0.5 rounded text-text-muted">{row.images.length} images</span>}
                    {(row.category_id || row.prompt_id) && (
                      <span className="text-xs bg-cyan-400/10 text-cyan-200 border border-cyan-400/20 px-1.5 py-0.5 rounded">
                        {[row.category_id, row.prompt_id].filter(Boolean).join(" / ")}
                      </span>
                    )}
                </div>
            </div>
            {row.error_message && <p role="alert" className="text-xs text-amber-200 whitespace-pre-wrap break-words">{row.error_message}</p>}
            
            <div className="relative">
                <textarea
                    ref={promptRef}
                    value={prompt}
                    disabled={busy}
                    onChange={(e) => setPrompt(e.target.value)}
                    onBlur={handleBlurPrompt}
                    placeholder="Enter prompt..."
                    className="w-full bg-black/20 rounded-md border border-white/5 focus:border-primary/50 focus:ring-1 focus:ring-primary/50 p-3 text-sm font-mono text-text-main resize-none h-24 transition-all placeholder:text-text-muted/30 pr-16"
                />
                <span className="absolute bottom-2 right-2 text-[10px] text-white/20 pointer-events-none">
                    {prompt.length} chars
                </span>
            </div>

            <div className="flex items-center gap-2">
                <input 
                    value={tags}
                    onChange={(e) => setTags(e.target.value)}
                    onBlur={handleBlurTags}
                    placeholder="Tags (comma separated)..."
                    className="flex-1 bg-transparent border-b border-white/5 focus:border-white/20 text-xs py-1 px-1 outline-none text-text-muted focus:text-text-main transition-colors"
                />
            </div>

            <div className="flex flex-wrap gap-2 text-[10px] text-text-muted">
                <span>Provider: {row.settings.provider_id || globalSettings.provider_id}</span>
                {caps?.supports_quality && <span>Quality: {qualityLabel(effective.quality)}</span>}
                <span>Size: {row.settings.size_preset || globalSettings.size_preset}</span>
                {outputName && <span className="text-cyan-200">Output: {outputName}</span>}
            </div>

            <div className="flex flex-wrap gap-2 mb-1">
                {row.attachments.length > 0 && <Button size="sm" variant="ghost" disabled={busy} onClick={() => runAction(async () => {
                  await api.updateRow(row.id, { attachments: [] });
                  await queryClient.invalidateQueries({ queryKey: ['state'] });
                })}>Clear references</Button>}
                {(row.attachments || []).map((att) => (
                    <div
                        key={att.id}
                        className="relative group bg-white/5 rounded px-2 py-1 flex items-center gap-2 border border-white/10"
                    >
                        <span className="text-xs text-text-muted max-w-[120px] truncate">
                            {att.file_path.split(/[/\\]/).pop()}
                        </span>
                        <button
                            onClick={() => runAction(() => removeAttachment(att.id))}
                            disabled={busy}
                            className="text-white/30 hover:text-red-400 transition-colors"
                            title="Remove attachment"
                        >
                            <XCircle size={12} />
                        </button>
                    </div>
                ))}
            </div>
            
            {/* Toolbar - Complete feature set */}
            <div className="flex items-center gap-1.5 mt-2 flex-wrap">
                {row.status === 'Generating' || row.status === 'Queued' ? (
                    <Button size="sm" variant="danger" onClick={() => runAction(handleStop)}>
                        <StopCircle size={14} className="mr-2" /> Stop Row
                    </Button>
                ) : (
                    <Button size="sm" onClick={() => runAction(handleGenerate)}>
                        <Play size={14} className="mr-2" /> {row.status === 'Interrupted' ? 'Review & Retry' : row.images.length > 0 ? "Regenerate" : "Generate"}
                    </Button>
                )}
                
                <div className="h-4 w-px bg-white/10 mx-1" />

                <Button size="sm" variant={showSettings ? "secondary" : "ghost"} onClick={() => setShowSettings(!showSettings)} title="Row Settings">
                    <Settings2 size={14} />
                </Button>
                <Button size="sm" variant="ghost" title={caps?.supports_attachments ? `Attach reference image (maximum ${caps.max_attachments})` : 'This model does not accept reference images'}
                  disabled={busy || !caps?.supports_attachments || row.attachments.length >= caps.max_attachments} onClick={() => runAction(handleAddAttachments)}>
                    <Paperclip size={14} className={row.attachments?.length ? "text-primary" : ""} />
                </Button>
                <Button size="sm" variant="ghost" title="Duplicate Row" onClick={() => runAction(handleDuplicate)}>
                    <Files size={14} />
                </Button>
                <Button size="sm" variant="ghost" title="Copy Prompt" onClick={() => runAction(() => copyText(prompt))}>
                    <Copy size={14} />
                </Button>
                
                <div className="flex-1" />
                
                {displayImage && (
                    <>
                        <Button size="sm" variant="ghost" title="Copy Image" onClick={() => runAction(() => copyImage(imageUrl(displayImage.file_path)))}>
                            <Copy size={14} />
                        </Button>
                        <Button size="sm" variant="ghost" title="Reveal image in file manager" onClick={() => runAction(() => desktopBridge().revealImage(displayImage.file_path))}>
                            <FolderOpen size={14} />
                        </Button>
                        <Button size="sm" variant="ghost" title="Export row images" onClick={() => runAction(exportRow)}>
                            <Download size={14} />
                        </Button>
                    </>
                )}
                <Button size="sm" variant="danger" title="Delete Row" disabled={busy} onClick={() => runAction(handleDelete)}>
                    <Trash2 size={14} />
                </Button>
            </div>

            {showSettings && (
                <div className="mt-2 p-3 bg-black/20 rounded border border-white/5">
                    <RowSettingsOverride row={row} providers={providers} globalSettings={globalSettings} />
                    <div className="mt-3 border-t border-white/5 pt-3 text-xs">
                      <div className="mb-1 text-text-muted">Next Request Preview</div>
                      <div className="max-h-28 overflow-y-auto rounded bg-black/30 p-2 font-mono text-[11px] leading-relaxed text-text-main">
                        {fullPromptPreview}
                      </div>
                    </div>
                </div>
            )}
        </div>

        {/* 3. Image Preview */}
        <div className="w-48 flex flex-col gap-2">
            <div 
                className="aspect-square bg-black/30 rounded-lg border border-white/5 flex items-center justify-center overflow-hidden relative cursor-pointer group/img"
                onClick={() => { if(displayImage) setLightboxIndex(row.images.length - 1); }}
            >
                {displayImage ? (
                    <>
                        <img src={imageUrl(displayImage.file_path)} alt={String(displayImage.metadata?.original_prompt || row.prompt)} className="w-full h-full object-contain" />
                        <div className="absolute inset-0 bg-black/50 opacity-0 group-hover/img:opacity-100 flex items-center justify-center transition-opacity text-xs text-white">
                            Click to Expand
                        </div>
                    </>
                ) : (
                    <div className="flex flex-col items-center text-text-muted/20">
                        <ImageIcon size={32} />
                        <span className="text-[10px] mt-2">No Image</span>
                    </div>
                )}
            </div>
        </div>
      </div>
    </div>

    {/* Lightbox Portal */}
    {lightboxIndex !== null && (
        <ImageLightbox 
            images={row.images} 
            initialIndex={lightboxIndex}
            defaultExportFolder={globalSettings.export_folder}
            onClose={() => setLightboxIndex(null)} 
        />
    )}
    </>
  );
}
