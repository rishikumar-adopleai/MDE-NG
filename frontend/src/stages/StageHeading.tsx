import type { ReactNode } from 'react';
import { navItems, type StageKey } from './model';

export function StageHeading({ stage, description, children }: { stage: StageKey; description: string; children?: ReactNode }) {
  const Icon = navItems.find(item => item.name === stage)!.icon;
  return <header className="intake-page-heading workflow-page-heading">
    <div className="workflow-stage-heading">
      <span className="workflow-stage-icon"><Icon size={23} aria-hidden="true" /></span>
      <div>
        <h1 tabIndex={-1}>{stage}</h1>
        <p>{description}</p>
      </div>
    </div>
    {children}
  </header>;
}
