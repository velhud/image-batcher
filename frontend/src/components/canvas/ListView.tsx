import { GlobalSettingsDTO, ProviderInfo, RowDTO } from '@/api/types';
import { RowCard } from '@/components/row/RowCard';
import { Button } from '@/components/common/Button';
import { Plus } from 'lucide-react';
import { api } from '@/api/client';
import { useQueryClient } from '@tanstack/react-query';

interface ListViewProps {
  rows: RowDTO[];
  globalSettings: GlobalSettingsDTO;
  providers: ProviderInfo[];
}

export function ListView({ rows, globalSettings, providers }: ListViewProps) {
  const queryClient = useQueryClient();

  const handleAddRow = async () => {
    await api.addRows([""]);
    queryClient.invalidateQueries({ queryKey: ['state'] });
  };

  return (
    <div className="max-w-5xl mx-auto pb-24">
      <div className="flex flex-col gap-4">
        {rows.map((row, idx) => (
          <RowCard key={row.id} row={row} index={idx + 1} globalSettings={globalSettings} providers={providers} />
        ))}
      </div>

      {/* Empty State / Add Button */}
      {rows.length === 0 && (
        <div className="text-center py-20 opacity-50">
            <p>No rows in this session.</p>
        </div>
      )}

      <div className="mt-8 flex justify-center">
        <Button variant="secondary" onClick={handleAddRow} className="w-full max-w-sm border-dashed">
            <Plus size={16} className="mr-2" />
            Add New Row
        </Button>
      </div>
    </div>
  );
}
