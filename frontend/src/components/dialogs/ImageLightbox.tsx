import { useCallback, useEffect, useState } from 'react';
import { ImageResultDTO } from '@/api/types';
import { api, imageUrl } from '@/api/client';
import { copyImage, copyText, desktopBridge } from '@/api/desktop';
import { runAction } from '@/lib/feedback';
import { Button } from '@/components/common/Button';
import { ResultDetails } from './ResultDetails';
import { X, ChevronLeft, ChevronRight, FolderOpen, Download, Copy } from 'lucide-react';

interface ImageLightboxProps {
  images: ImageResultDTO[];
  initialIndex: number;
  defaultExportFolder?: string | null;
  onClose: () => void;
}

export function ImageLightbox({ images, initialIndex, defaultExportFolder, onClose }: ImageLightboxProps) {
  const [index, setIndex] = useState(initialIndex);
  const current = images[index];
  const [feedback, setFeedback] = useState('');
  const [showDetails, setShowDetails] = useState(false);

  useEffect(() => {
    if (index >= images.length) setIndex(0);
  }, [images.length, index]);

  const handleNext = useCallback(() => {
    setIndex((prev) => (prev + 1) % images.length);
  }, [images.length]);

  const handlePrev = useCallback(() => {
    setIndex((prev) => (prev - 1 + images.length) % images.length);
  }, [images.length]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'ArrowRight') handleNext();
      if (e.key === 'ArrowLeft') handlePrev();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleNext, handlePrev, onClose]);

  if (!current) return null;
  const exportImages = async (single: boolean) => {
    const folder = await desktopBridge().selectFolder(defaultExportFolder);
    if (!folder) return;
    const result = await api.export([current.row_id], folder, single ? current.id : undefined);
    setFeedback(`Exported ${result.exported} images.`);
  };
  const perform = (action: () => Promise<unknown>) => runAction(async () => {
    try {
      await action();
    } catch (error) {
      setFeedback(error instanceof Error ? error.message : String(error));
    }
  });

  return (
    <div className="fixed inset-0 z-[100] bg-black/95 backdrop-blur-md flex flex-col animate-in fade-in duration-200">
      <div className="h-16 flex items-center justify-between px-6 border-b border-white/10">
        <span className="text-sm font-mono text-text-muted">
            {index + 1} / {images.length} • {current.file_path.split(/[/\\]/).pop()}
        </span>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="secondary" aria-expanded={showDetails} onClick={() => setShowDetails(value => !value)}>Result Details</Button>
          <button aria-label="Close image" onClick={onClose} className="p-2 hover:bg-white/10 rounded-full transition-colors">
            <X size={24} />
          </button>
        </div>
      </div>

      <div className="flex-1 min-h-0 flex flex-col sm:flex-row">
        <div className="flex-1 min-h-0 min-w-0 flex items-center justify-center relative p-8">
          <img
              src={imageUrl(current.file_path)}
              className="max-w-full max-h-full object-contain shadow-2xl rounded-sm"
              alt="Full size"
          />
        
          {images.length > 1 && (
              <>
                  <button onClick={handlePrev} className="absolute left-4 p-3 rounded-full bg-black/50 hover:bg-white/20 transition-colors backdrop-blur-sm">
                      <ChevronLeft size={32} />
                  </button>
                  <button onClick={handleNext} className="absolute right-4 p-3 rounded-full bg-black/50 hover:bg-white/20 transition-colors backdrop-blur-sm">
                      <ChevronRight size={32} />
                  </button>
              </>
          )}
        </div>

        {showDetails && <ResultDetails image={current} />}
      </div>

      {feedback && <p role="status" className="text-center text-sm text-text-muted pb-2">{feedback}</p>}
      <div className="min-h-20 py-3 flex-wrap border-t border-white/10 flex items-center justify-center gap-4 bg-black/40">
        <Button variant="secondary" onClick={() => perform(() => desktopBridge().revealImage(current.file_path))}>
          <FolderOpen size={16} className="mr-2" /> Reveal in file manager
        </Button>
        <Button variant="secondary" onClick={() => perform(() => copyText(current.file_path))}>Copy path</Button>
        <Button variant="secondary" onClick={() => perform(async () => {
          await copyImage(imageUrl(current.file_path));
          setFeedback('Image copied.');
        })}><Copy size={16} className="mr-2" /> Copy image</Button>
        <Button variant="primary" onClick={() => perform(() => exportImages(true))}>
          <Download size={16} className="mr-2" /> Export this image
        </Button>
        {images.length > 1 && <Button variant="secondary" onClick={() => perform(() => exportImages(false))}>Export row images</Button>}
      </div>
    </div>
  );
}
