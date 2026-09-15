import { MachinesPanel } from '../runtime/MachinesPanel';
import { WorkspacesPanel } from '../runtime/WorkspacesPanel';

export function MachinesStep() {
  return <div className="space-y-6">
    <div className="space-y-2">
      <h1 className="text-2xl font-bold">Machines</h1>
      <p className="text-sm text-(--text-secondary)">Choose where commands run: this computer or a machine over SSH. You can set this up now or continue and add a machine later.</p>
    </div>
    <MachinesPanel />
  </div>;
}

export function WorkspacesStep({ onAddMachine }: { onAddMachine: () => void }) {
  return <div className="space-y-6">
    <div className="space-y-2">
      <h1 className="text-2xl font-bold">Workspaces</h1>
      <p className="text-sm text-(--text-secondary)">Give a project directory a name and choose its machine. Reuse it across chats with Attach. New chats start without a workspace, even when you create one here.</p>
      <p className="text-xs text-(--text-muted)">Optional — you can add workspaces later.</p>
    </div>
    <WorkspacesPanel onboarding onAddMachine={onAddMachine} />
  </div>;
}
