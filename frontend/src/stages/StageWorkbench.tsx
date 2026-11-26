import { HeadingText } from '../HeadingText';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowRight, ChevronDown, ChevronUp, ClipboardList, Inbox, Search, X } from 'lucide-react';
import { useWorkspace, type CommandResult } from '../context/WorkspaceContext';
import { getWorkItemsByStage } from '../domain/selectors';
import type { StageKey, WorkItem } from '../domain/model';
import { caseStateTone, formatStatus, formatWorkspaceCopy, practiceCaseLabel } from './presentation';
import { Status } from './shared';
import { StageAIReview } from './StageAIReview';
import { StageHeading } from './StageHeading';
import { useQueueRows } from './useQueueRows';

type QueueFilter = 'all' | 'exception' | 'review' | 'ready';
export type StageProps = {
  openSource: () => void;
  filters: { key: QueueFilter; label: string }[];
  activeFilter: QueueFilter;
  onFilterChange: (filter: QueueFilter) => void;
  query: string;
  onQueryChange: (query: string) => void;
  visibleIds: ReadonlySet<string>;
  selectedId: string;
  onSelect: (id: string) => void;
  onNavigate: (stage: StageKey, esrId: string, entityId?: string) => void;
  selectedEntityId?: string;
  onReset: () => void;
};

export { dateLabel } from './presentation';
export function resultEntityId(value: unknown) {
  return value && typeof value === 'object' && 'entityId' in value && typeof value.entityId === 'string' ? value.entityId : undefined;
}
export const modalityLabels = { 'in-person': 'In-person', 'tele-c-and-p': 'Telehealth C&P', 'ace-records-review': 'ACE records review' };

export function useStageAction() {
  const [pending, setPending] = useState(false);
  const [feedback, setFeedback] = useState<CommandResult>();
  const locked = useRef(false);
  const execute = async (command: () => Promise<CommandResult>, after?: (result: Extract<CommandResult, {ok: true}>) => void) => {
    if (locked.current) return;
    locked.current = true;
    setPending(true);
    setFeedback(undefined);
    try {
      const result = await command();
      setFeedback(result);
      if (result.ok) after?.(result);
    } finally {
      setPending(false);
      locked.current = false;
    }
  };
  return { pending, feedback, execute };
}

export function ActionFeedback({ result }: { result?: CommandResult }) {
  return result ? <p className={result.ok ? 'stage-feedback' : 'stage-feedback stage-error'} role={result.ok ? 'status' : 'alert'}>{formatWorkspaceCopy(result.message)}</p> : null;
}

export function StageActions({ children }: { children: ReactNode }) {
  return <div className="stage-actions">{children}</div>;
}

export function StageBlock({ title, children, description }: { title: string; children: ReactNode; description?: string }) {
  return <section className="stage-block"><h3><HeadingText>{title}</HeadingText></h3>{description && <p className="stage-description">{description}</p>}{children}</section>;
}

export function StageHandoff({ stage, esrId, entityId, children, onNavigate }: {stage: StageKey; esrId: string; entityId?: string; children: ReactNode; onNavigate: StageProps['onNavigate']}) {
  return <div className="triage-success" role="status">
    <div><strong>{children}</strong><p>{esrId}</p></div>
    <button type="button" className="intake-secondary" onClick={() => onNavigate(stage, esrId, entityId)}>Open {stage} <ArrowRight size={16} /></button>
  </div>;
}

function WorkDetail<T>({ item, render }: { item: WorkItem; render: (detail: T, item: WorkItem) => ReactNode }) {
  const { state, getWorkItem } = useWorkspace();
  const [detail, setDetail] = useState<T>();
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setError('');
    getWorkItem<T>(item.id, controller.signal).then(result => {
      if (!controller.signal.aborted) setDetail(result);
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) {
        setDetail(undefined);
        setError(error instanceof Error ? error.message : 'Unable to load this work item.');
      }
    });
    return () => controller.abort();
  }, [item.id, getWorkItem, state?.revision, retry]);
  return <>
    {error && <div className="stage-block"><p role="alert">{error}</p><button type="button" className="intake-secondary" onClick={() => setRetry(value => value + 1)}>Retry</button></div>}
    {detail ? render(detail, item) : !error && <p className="stage-block" role="status">Loading work item…</p>}
  </>;
}

function StackedWorkCard<T>({
  index,
  stage,
  item,
  statusText,
  defaultOpen,
  render,
}: {
  index: number;
  stage: StageKey;
  item: WorkItem;
  statusText: string;
  defaultOpen: boolean;
  render: (detail: T, item: WorkItem) => ReactNode;
}) {
  const [cardOpen, setCardOpen] = useState(defaultOpen);
  return (
    <article className="triage-card">
      <header
        className="triage-card-header"
        style={{ cursor: 'pointer' }}
        onClick={() => setCardOpen(v => !v)}
      >
        <div className="triage-card-title-group">
          <span className="triage-card-number">{String(index + 1).padStart(2, '0')}</span>
          <div>
            <h3>{item.title}</h3>
            <div className="triage-card-tags">
              <span className="triage-practice-tag">{stage === 'Internal QA' ? 'Signed DBQ Report' : 'Work Item'}</span>
            </div>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Status text={formatStatus(statusText)} tone={caseStateTone(statusText)} />
          <button
            type="button"
            className="intake-icon-button"
            aria-expanded={cardOpen}
            aria-label={cardOpen ? `Collapse ${item.title}` : `Expand ${item.title}`}
            onClick={e => {
              e.stopPropagation();
              setCardOpen(v => !v);
            }}
          >
            {cardOpen ? <ChevronUp size={17} /> : <ChevronDown size={17} />}
          </button>
        </div>
      </header>
      {cardOpen && (
        <div style={{ marginTop: 14, display: 'grid', gap: 14 }}>
          <StageAIReview stage={stage} entityId={item.entityId} />
          <WorkDetail<T> item={item} render={render} />
        </div>
      )}
    </article>
  );
}

export function StageWorkbench<T>({ stage, description, title, query, onQueryChange, filters, activeFilter, onFilterChange, visibleIds, selectedId, selectedEntityId, onSelect, onReset, handoff, render, openSource }: StageProps & {
  stage: StageKey; title: string; description: string; handoff?: ReactNode; render: (detail: T, item: WorkItem) => ReactNode;
}) {
  const { state } = useWorkspace();
  const [open, setOpen] = useState(false);
  const [deselected, setDeselected] = useState(false);
  const matches = state ? getWorkItemsByStage(state, stage).filter(item => visibleIds.has(item.esrId)) : [];
  const blocked = (item: WorkItem) => item.status === 'blocked' || item.blockingReasons.length > 0;
  const workStatus = (item: WorkItem) => state?.appointmentsById[item.entityId]?.status ?? item.status;

  type CaseGroup = {
    esrId: string;
    claimantName: string;
    initials: string;
    claimIdentifier: string;
    priority: string;
    isSimulated: boolean;
    items: WorkItem[];
  };

  const groupsByEsr = new Map<string, CaseGroup>();
  for (const item of matches) {
    let grp = groupsByEsr.get(item.esrId);
    if (!grp) {
      const esr = state?.esrsById[item.esrId];
      const vet = esr ? state?.veteransById[esr.veteranId] : undefined;
      const claimantName = vet?.displayName || item.esrId;
      const initials =
        vet?.initials ||
        claimantName
          .split(/\s+/)
          .filter(Boolean)
          .slice(0, 2)
          .map(w => w[0]?.toUpperCase() ?? '')
          .join('') ||
        'VA';
      const claimIdentifier = vet?.claimIdentifier || esr?.sourceReference || practiceCaseLabel(esr) || item.esrId;
      const isSimulated = Boolean(
        esr?.isSimulated ||
          item.esrId.startsWith('esr-local') ||
          item.esrId.toLowerCase().includes('syn'),
      );
      grp = {
        esrId: item.esrId,
        claimantName,
        initials,
        claimIdentifier,
        priority: esr?.priority || item.priority || 'routine',
        isSimulated,
        items: [],
      };
      groupsByEsr.set(item.esrId, grp);
    }
    grp.items.push(item);
  }

  const allCaseGroups = Array.from(groupsByEsr.values());
  const caseGroups = {
    all: allCaseGroups,
    exception: allCaseGroups.filter(g => g.items.some(blocked)),
    review: allCaseGroups.filter(g => g.items.some(item => item.status === 'in-progress' && !blocked(item))),
    ready: allCaseGroups.filter(g => g.items.every(item => !blocked(item)) && g.items.some(item => item.status === 'queued')),
  };

  const visibleGroups = caseGroups[activeFilter];
  const activeGroup = deselected
    ? undefined
    : (visibleGroups.find(g => g.esrId === selectedId) ?? visibleGroups[0]);
  const itemNoun = stage === 'Internal QA' ? 'report' : stage === 'VA Delivery & Review' ? 'package' : 'item';

  const summarizeGroup = (items: WorkItem[]) => {
    const hasBlocked = items.some(blocked);
    if (hasBlocked) return { text: 'Exception', tone: 'red' as const };
    if (items.length === 1) {
      const st = workStatus(items[0]);
      return { text: formatStatus(st), tone: caseStateTone(st) };
    }
    const inProgress = items.filter(i => i.status === 'in-progress').length;
    if (inProgress > 0) {
      return { text: `${inProgress}/${items.length} In Progress`, tone: 'amber' as const };
    }
    return { text: `${items.length} Queued`, tone: 'slate' as const };
  };

  const queueList = useQueueRows(activeGroup?.esrId, open, `${activeFilter}:${query}`);
  const heading = useRef<HTMLHeadingElement>(null);
  return <div className="intake-page operations-page queue-workbench">
    <StageHeading stage={stage} description={description}><button type="button" className="intake-secondary" onClick={openSource}>Reference library</button></StageHeading>
    {handoff}
    <div className="intake-board">
      <aside className="intake-queue-card" aria-labelledby="stage-queue-heading">
        <div className="intake-queue-heading"><h2 id="stage-queue-heading"><HeadingText>{title}</HeadingText></h2><span>{visibleGroups.length} {visibleGroups.length === 1 ? 'case' : 'cases'}</span></div>
        <button type="button" className="intake-queue-toggle" aria-expanded={open} aria-controls="stage-queue" onClick={() => setOpen(!open)}><Inbox size={17} />{open ? 'Hide work queue' : 'Choose a patient case'}<small>{visibleGroups.length} open</small><ChevronDown size={16} /></button>
        <div id="stage-queue" className={`intake-queue-controls${open ? ' is-open' : ''}`}>
          <div className="intake-queue-toolbar"><label className="intake-search"><Search size={17} /><input type="search" value={query} onChange={event => { setDeselected(false); onQueryChange(event.target.value); }} placeholder="Search claimant, ESR, or report" aria-label={`Search ${stage} queue`} /></label>
            <div className="intake-queue-filters" role="group" aria-label="Queue filters">{filters.map(filter =>
              <button key={filter.key} type="button" aria-pressed={activeFilter === filter.key} onClick={() => { setDeselected(false); onFilterChange(filter.key); }}>{filter.key === 'all' ? 'All cases' : filter.label}<span>{caseGroups[filter.key].length}</span></button>
            )}</div>
          </div>
          {visibleGroups.length ? <ul ref={queueList} className="intake-queue-list">{visibleGroups.map(group => {
            const grpStatus = summarizeGroup(group.items);
            return <li key={group.esrId}>
              <button type="button" className="intake-queue-row" aria-pressed={group.esrId === activeGroup?.esrId} onClick={() => {
                setDeselected(false);
                onSelect(group.esrId);
                if (open) { setOpen(false); requestAnimationFrame(() => heading.current?.focus()); }
              }}>
                <span className="intake-queue-row-main">
                  <strong>{group.claimantName}</strong>
                  <small>{group.claimIdentifier} · {group.items.length} {itemNoun}{group.items.length === 1 ? '' : 's'}</small>
                </span>
                <Status text={grpStatus.text} tone={grpStatus.tone} />
              </button>
            </li>;
          })}</ul> : <div className="intake-queue-empty"><p>No cases match this view.</p><button type="button" className="intake-text-action" onClick={onReset}>Reset filters</button></div>}
        </div>
      </aside>
      {activeGroup ? <section className="intake-case-card triage-case-workspace queue-detail" key={activeGroup.esrId} aria-labelledby="stage-work-title" tabIndex={0}>
        <header className="triage-case-header">
          <div className="triage-claimant-block">
            <span className="intake-avatar">{activeGroup.initials}</span>
            <div>
              <div className="triage-meta-line">
                <span>{activeGroup.claimIdentifier}</span>
                {activeGroup.isSimulated && <><span>·</span><span className="triage-sim-pill">Simulated</span></>}
              </div>
              <h2 ref={heading} tabIndex={-1} id="stage-work-title">{activeGroup.claimantName}</h2>
              <p className="triage-case-sub">Priority: {activeGroup.priority} · {activeGroup.items.length} {itemNoun}{activeGroup.items.length === 1 ? '' : 's'}</p>
            </div>
          </div>
          <div className="triage-overview-actions">
            {(() => {
              const s = summarizeGroup(activeGroup.items);
              return <Status text={s.text} tone={s.tone} />;
            })()}
            <button type="button" className="intake-icon-button" aria-label="Close case details" title="Close case" onClick={() => { setDeselected(true); onSelect(''); }}><X size={18} /></button>
          </div>
        </header>
        {activeGroup.items.length === 1 ? (
          <>
            <StageAIReview stage={stage} entityId={activeGroup.items[0].entityId} />
            <WorkDetail<T> item={activeGroup.items[0]} render={render} />
          </>
        ) : (
          <section className="triage-cards-section" aria-label={`${stage} items`}>
            <div className="intake-kicker" style={{ marginBottom: 10 }}>
              <ClipboardList size={15} />
              <span><HeadingText>{stage === 'Internal QA' ? `Signed DBQ Reports (${activeGroup.items.length})` : `Active Items (${activeGroup.items.length})`}</HeadingText></span>
            </div>
            <div className="triage-cards-stack">
              {activeGroup.items.map((item, idx) => (
                <StackedWorkCard<T>
                  key={item.id}
                  index={idx}
                  stage={stage}
                  item={item}
                  statusText={workStatus(item)}
                  defaultOpen={selectedEntityId ? item.entityId === selectedEntityId : idx === 0}
                  render={render}
                />
              ))}
            </div>
          </section>
        )}
      </section> : <section className="intake-case-empty"><Inbox size={25} /><h2><HeadingText>No case selected</HeadingText></h2><p>{visibleGroups.length ? 'Select a patient case from the queue to review details.' : 'Eligible cases appear here as they enter this stage.'}</p></section>}
    </div>
  </div>;
}
