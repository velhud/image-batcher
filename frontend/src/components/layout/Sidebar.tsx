import { type ReactNode, useState } from 'react';
import { BackendState } from '@/api/types';
import { useSession } from '@/hooks/useSession';
import { 
  LayoutGrid, List, Settings, 
  PlusSquare, ChevronLeft, ChevronRight, Zap, Sliders, FileText
} from 'lucide-react';
import { GenerationSettingsPanel } from '@/components/settings/GenerationSettingsPanel';
import { AzureLogoBatchPanel } from '@/components/settings/AzureLogoBatchPanel';
import { TemplatesDialog } from '@/components/dialogs/TemplatesDialog';

export function Sidebar({ state }: { state: BackendState }) {
  const { 
    isSidebarCollapsed, toggleSidebar, 
    activeView, setView,
    setBatchOpen, setSettingsOpen
  } = useSession();
  const [isTemplatesOpen, setTemplatesOpen] = useState(false);

  return (
    <aside 
      className={`
        relative flex flex-col h-full border-r border-white/5 bg-surface/50 backdrop-blur-xl transition-all duration-300 ease-in-out z-20
        ${isSidebarCollapsed ? 'w-16' : 'w-80'}
      `}
    >
      {/* Header */}
      <div className="h-16 flex items-center justify-center border-b border-white/5 shrink-0">
        <div className={`flex items-center gap-2 ${isSidebarCollapsed ? 'justify-center' : 'px-6 w-full'}`}>
          <div className="w-8 h-8 rounded bg-gradient-to-br from-primary to-purple-600 flex items-center justify-center shadow-lg shadow-primary/20 shrink-0">
            <Zap size={18} className="text-white fill-white" />
          </div>
          {!isSidebarCollapsed && (
            <span className="font-bold text-lg tracking-tight text-transparent bg-clip-text bg-gradient-to-r from-white to-white/60">
              ImageAI
            </span>
          )}
        </div>
      </div>

      <nav className="flex-1 flex flex-col overflow-hidden">
        {/* View Modes */}
        <div className="p-2 space-y-1 shrink-0">
            <NavButton 
            icon={<List size={20} />} 
            label="List View" 
            active={activeView === 'list'} 
            onClick={() => setView('list')}
            collapsed={isSidebarCollapsed}
            />
            <NavButton 
            icon={<LayoutGrid size={20} />} 
            label="Gallery Grid" 
            active={activeView === 'grid'} 
            onClick={() => setView('grid')}
            collapsed={isSidebarCollapsed}
            />
        </div>

        <div className="h-px bg-white/5 mx-4 my-2 shrink-0" />

        {/* Global Settings Panel - Only visible when expanded */}
        {!isSidebarCollapsed ? (
            <div className="flex-1 overflow-y-auto px-4 py-2">
                <div className="flex items-center gap-2 text-xs font-semibold text-text-muted mb-4 uppercase tracking-wider">
                    <Sliders size={12} />
                    Global Defaults
                </div>
                <AzureLogoBatchPanel state={state} />
                <div className="my-5 h-px bg-white/5" />
                <GenerationSettingsPanel state={state} />
            </div>
        ) : (
            <div className="flex-1 flex justify-center pt-4" aria-label="Expand to see settings">
                <Sliders size={20} className="text-text-muted" />
            </div>
        )}
      </nav>

      {/* Footer */}
      <div className="p-2 border-t border-white/5 flex flex-col gap-2 shrink-0 bg-black/10">
        <NavButton 
          icon={<PlusSquare size={20} className="text-primary-glow" />} 
          label="Batch Import" 
          collapsed={isSidebarCollapsed}
          onClick={() => setBatchOpen(true)}
        />
        <NavButton 
          icon={<FileText size={20} />} 
          label="Templates" 
          collapsed={isSidebarCollapsed}
          onClick={() => setTemplatesOpen(true)}
        />
        <NavButton 
          icon={<Settings size={20} />} 
          label="App Settings" 
          collapsed={isSidebarCollapsed}
          onClick={() => setSettingsOpen(true)}
        />
        <button 
          onClick={toggleSidebar}
          className="flex items-center justify-center w-full h-8 hover:bg-white/5 rounded-md text-text-muted transition-colors mt-1"
        >
          {isSidebarCollapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>
      </div>
      
      <TemplatesDialog isOpen={isTemplatesOpen} onClose={() => setTemplatesOpen(false)} />
    </aside>
  );
}

function NavButton({ icon, label, active, onClick, collapsed }: { icon: ReactNode; label: string; active?: boolean; onClick: () => void; collapsed: boolean }) {
  return (
    <button
      onClick={onClick}
      className={`
        flex items-center h-10 rounded-md transition-all duration-200 group whitespace-nowrap w-full
        ${collapsed ? 'justify-center px-0' : 'px-4 gap-3'}
        ${active 
          ? 'bg-primary/10 text-primary border border-primary/20' 
          : 'text-text-muted hover:text-text-main hover:bg-white/5'}
      `}
      title={collapsed ? label : undefined}
    >
      {icon}
      {!collapsed && <span className="text-sm font-medium">{label}</span>}
    </button>
  );
}
