import type { ReactNode, Ref } from 'react';

type RecordHeaderProps = {
  name: string;
  initials: string;
  metadata: ReactNode;
  summary?: ReactNode;
  actions?: ReactNode;
  headingId?: string;
  headingRef?: Ref<HTMLHeadingElement>;
  flush?: boolean;
};

export function RecordHeader({
  name, initials, metadata, summary, actions, headingId, headingRef, flush,
}: RecordHeaderProps) {
  return <header className={flush ? 'record-header record-header-flush' : 'record-header'}>
    <span className="record-avatar" aria-hidden="true">{initials}</span>
    <div className="record-identity">
      <h2 id={headingId} ref={headingRef} tabIndex={-1}>{name}</h2>
      <div className="record-metadata">{metadata}</div>
      {summary && <div className="record-summary">{summary}</div>}
    </div>
    {actions && <div className="record-actions">{actions}</div>}
  </header>;
}
