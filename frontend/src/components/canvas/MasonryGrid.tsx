import { SessionDTO } from '@/api/types';
import { Badge } from '@/components/common/Badge';
import { imageUrl } from '@/api/client';

interface MasonryGridProps {
  session: SessionDTO;
}

export function MasonryGrid({ session }: MasonryGridProps) {
  // Flatten all images into a single list with metadata
  const allImages = session.rows.flatMap(row => 
    row.images.map(img => ({
      ...img,
      prompt: String(img.metadata?.original_prompt || row.prompt),
      rowStatus: row.status
    }))
  ).reverse(); // Newest first

  if (allImages.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-text-muted opacity-50">
        <p>No images generated yet.</p>
      </div>
    );
  }

  return (
    <div className="columns-2 md:columns-3 lg:columns-4 xl:columns-5 gap-4 space-y-4 pb-20 mx-auto max-w-[1600px]">
      {allImages.map((img) => (
        <div key={img.id} className="break-inside-avoid relative group rounded-lg overflow-hidden bg-surface border border-white/5 hover:border-white/20 transition-all hover:-translate-y-1 hover:shadow-xl cursor-pointer">
          <img 
            src={imageUrl(img.file_path)} 
            alt={img.prompt}
            className="w-full h-auto object-cover"
            loading="lazy"
          />
          
          {/* Hover Overlay */}
          <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/20 to-transparent opacity-0 group-hover:opacity-100 transition-opacity p-4 flex flex-col justify-end">
            <p className="text-white text-xs line-clamp-2 font-medium mb-2">{img.prompt}</p>
            <div className="flex justify-between items-center">
               <Badge status={img.rowStatus} className="scale-75 origin-left" />
               <button 
                 onClick={(e) => {
                    e.stopPropagation();
                    navigator.clipboard.writeText(img.file_path);
                 }}
                 className="text-[10px] text-white/60 hover:text-white uppercase tracking-wider"
               >
                 Copy Path
               </button>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
