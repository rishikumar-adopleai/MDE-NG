import type { ReactNode } from 'react';
import { LayoutDashboard } from 'lucide-react';
import type { WorkspacePage } from '../workspaceRoutes';
import { navItems } from './model';

export function StageHeading({ stage, description, children }: { stage: WorkspacePage; description?: string; children?: ReactNode }) {
  const Icon = stage === 'Dashboard' ? LayoutDashboard : navItems.find(item => item.name === stage)!.icon;
  return <header className="intake-page-heading workflow-page-heading">
    <div className="workflow-stage-heading">
      <span className="workflow-stage-icon"><Icon size={23} aria-hidden="true" /></span>
      <div>
        <h1 tabIndex={-1}>{stage}</h1>
        {description && <p>{description}</p>}
      </div>
    </div>
    {children}
  </header>;
}
