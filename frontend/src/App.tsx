import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api/client';
import { Sidebar } from '@/components/layout/Sidebar';
import { GlobalControls } from '@/components/layout/GlobalControls';
import { ListView } from '@/components/canvas/ListView';
import { MasonryGrid } from '@/components/canvas/MasonryGrid';
import { BatchDialog } from '@/components/dialogs/BatchDialog';
import { SettingsDialog } from '@/components/dialogs/SettingsDialog';
import { StatsDialog } from '@/components/dialogs/StatsDialog';
import { Footer } from '@/components/layout/Footer';
import { useSession } from '@/hooks/useSession';
import { useHotkeys } from '@/hooks/useHotkeys';
import { Loader2, FolderOpen, Save, RotateCcw, RotateCw, Filter, BarChart2 } from 'lucide-react';
import { Button } from '@/components/common/Button';
import { API_ERROR_EVENT, errorMessage, runAction } from '@/lib/feedback';

function App() {
  const queryClient = useQueryClient();
  const { 
    activeView, 
    isBatchOpen, setBatchOpen, 
    isSettingsOpen, setSettingsOpen 
  } = useSession();
  const [tagFilter, setTagFilter] = useState("");
  const [isStatsOpen, setStatsOpen] = useState(false);
  const [actionError, setActionError] = useState('');
  useEffect(() => {
    const listener = (event: Event) => setActionError((event as CustomEvent<string>).detail);
    window.addEventListener(API_ERROR_EVENT, listener);
    return () => window.removeEventListener(API_ERROR_EVENT, listener);
  }, []);

  const { data: state, isLoading, error } = useQuery({
    queryKey: ['state'],
    queryFn: api.fetchState,
    refetchInterval: (query) => {
      if (!query.state.data) return 1000;
      const isGenerating = query.state.data.session.rows.some(row => row.status === 'Queued' || row.status === 'Generating');
      return isGenerating ? 500 : 2000;
    }
  });

  // HOTKEYS
  useHotkeys('cmd+enter', () => {
    if (!state) return;
    const ids = state.session.rows.filter(r => r.selected).map(r => r.id);
    if (ids.length) runAction(async () => {
      const report = await api.preflight(ids);
      if (!report.valid) throw new Error(report.rows.flatMap(row => row.errors).join('\n'));
      if (state.session.rows.some(row => ids.includes(row.id) && row.status === 'Interrupted') && !confirm('Some selected requests were interrupted and may have completed at the provider. Start new requests?')) return;
      if (report.total_images >= state.session.global_settings.confirm_generate_threshold && !confirm(`Generate ${report.total_images} images across ${report.total_requests} requests?`)) return;
      await api.generateRows(ids);
    });
  });
  
  useHotkeys('cmd+n', () => runAction(async () => {
     await api.addRows([""]);
     await queryClient.invalidateQueries({ queryKey: ['state'] });
  }));

  useHotkeys('cmd+z', () => runAction(async () => {
     await api.undo();
     await queryClient.invalidateQueries({ queryKey: ['state'] });
  }));

  if (isLoading && !state) {
    return (
      <div className="h-screen w-screen flex items-center justify-center bg-background text-primary">
        <Loader2 className="animate-spin" size={32} />
      </div>
    );
  }

  if (!state) return <div className="h-screen flex flex-col items-center justify-center bg-background text-text-main gap-4 px-8">
    <h1 className="text-xl font-semibold">ImageAI backend unavailable</h1>
    <p role="alert" className="text-text-muted text-sm max-w-xl text-center">{errorMessage(error || 'Waiting for the local backend.')}</p>
    <Button onClick={() => runAction(() => queryClient.invalidateQueries({ queryKey: ['state'] }))}>Reconnect</Button>
  </div>;

  const filteredRows = tagFilter.trim()
    ? state.session.rows.filter((row) => (row.tags || []).some((t) => t.toLowerCase().includes(tagFilter.trim().toLowerCase())))
    : state.session.rows;
  const filteredSession = { ...state.session, rows: filteredRows };

  const handleUndo = async () => {
    const next = await api.undo();
    queryClient.setQueryData(['state'], next);
  };

  const handleRedo = async () => {
    const next = await api.redo();
    queryClient.setQueryData(['state'], next);
  };

  const handleSave = async () => {
    await api.saveSession();
  };

  const handleLoad = async () => {
    const path = prompt("Enter full path to .json to load:");
    if (!path) return;
    const next = await api.loadSession(path);
    queryClient.setQueryData(['state'], next);
  };

  return (
    <div className="flex h-screen w-screen bg-background text-text-main overflow-hidden">
      <Sidebar state={state} />

      <main className="flex-1 flex flex-col min-w-0 bg-gradient-to-br from-background to-[#050b14]">
        {/* Header */}
        <header className="h-16 border-b border-white/5 flex items-center justify-between px-8 gap-4 bg-background/50 backdrop-blur-sm z-10">
          <div className="min-w-[180px]">
            <h1 className="text-xl font-bold tracking-tight">
              {activeView === 'list' ? 'Studio' : 'Gallery'}
            </h1>
            <div className="flex items-center gap-2 text-xs text-text-muted mt-0.5">
              <span className={`w-2 h-2 rounded-full ${state ? 'bg-success shadow-[0_0_8px_rgba(16,185,129,0.4)]' : 'bg-danger'}`} />
              {state ? `Connected • ${state.stats.total} Rows` : 'Connecting...'}
            </div>
          </div>

          <div className="flex-1 max-w-md mx-4 relative group">
            <Filter size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted group-focus-within:text-primary transition-colors" />
            <input 
                placeholder="Filter by tags..." 
                value={tagFilter}
                onChange={(e) => setTagFilter(e.target.value)}
                className="w-full bg-black/20 border border-white/5 rounded-full py-1.5 pl-9 pr-4 text-xs focus:ring-1 focus:ring-primary focus:border-primary outline-none transition-all"
            />
          </div>

          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1">
              <Button variant="ghost" size="icon" onClick={() => runAction(handleUndo)} title="Undo">
                <RotateCcw size={16}/>
              </Button>
              <Button variant="ghost" size="icon" onClick={() => runAction(handleRedo)} title="Redo">
                <RotateCw size={16}/>
              </Button>
              <div className="h-4 w-px bg-white/10 mx-1" />
              <Button variant="ghost" size="icon" onClick={() => runAction(handleSave)} title="Quick Save">
                <Save size={16}/>
              </Button>
              <Button variant="ghost" size="icon" onClick={() => runAction(handleLoad)} title="Open Session">
                <FolderOpen size={16}/>
              </Button>
              <Button variant="ghost" size="icon" onClick={() => setStatsOpen(true)} title="Statistics">
                <BarChart2 size={16} />
              </Button>
            </div>

            <GlobalControls state={state} />
          </div>
        </header>
        {(actionError || error) && <div role="alert" className="flex gap-3 items-start px-6 py-3 bg-red-500/10 border-b border-red-400/20 text-red-200 text-sm">
          <p className="flex-1 whitespace-pre-wrap break-words">{actionError || errorMessage(error)}</p>
          {actionError && <button aria-label="Dismiss error" onClick={() => setActionError('')}>Dismiss</button>}
        </div>}

        {/* Workspace */}
        <div className="flex-1 p-8 overflow-y-auto scroll-smooth">
           {activeView === 'list' ? (
             <ListView rows={filteredRows} globalSettings={state.session.global_settings} providers={state.providers} />
           ) : (
             <MasonryGrid session={filteredSession} />
           )}
        </div>

        <Footer state={state} />
      </main>

      {/* Dialogs Layer */}
      <BatchDialog isOpen={isBatchOpen} onClose={() => setBatchOpen(false)} />
      <SettingsDialog isOpen={isSettingsOpen} onClose={() => setSettingsOpen(false)} state={state} />
      <StatsDialog isOpen={isStatsOpen} onClose={() => setStatsOpen(false)} state={state} />
    </div>
  );
}

export default App;
