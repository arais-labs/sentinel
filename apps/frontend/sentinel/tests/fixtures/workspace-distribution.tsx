import { createRoot } from 'react-dom/client';
import { WorkspaceEditor } from '../../src/components/runtime/WorkspaceEditor';
import '../../src/index.css';
document.documentElement.classList.add('dark');
const params = new URLSearchParams(location.search);
const distribution = params.get("distribution") ?? (params.get("desktop") === "lxqt" ? "alpine" : "ubuntu");
createRoot(document.getElementById('root')!).render(<WorkspaceEditor workspace={params.has("edit") ? { id: "workspace-a", name: "Existing", machine_id: "mac", directory: "/projects/test", distribution, desktop: params.get("desktop") ?? "none", development_tools: ["git"], resources: { cpus: 2, memory_gib: 2, disk_gib: 32 } } as any : "new"} machines={[{ id: 'mac', name: 'Mac' } as any]} saving={false} onClose={() => {}} onSave={async value => { Object.assign(window, { savedWorkspace: value }); }} />);
