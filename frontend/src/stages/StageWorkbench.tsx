import { HeadingText } from '../HeadingText';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowRight, ChevronDown, Inbox, Search } from 'lucide-react';
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

export function StageWorkbench<T>({ stage, description, title, query, onQueryChange, filters, activeFilter, onFilterChange, visibleIds, selectedId, selectedEntityId, onSelect, onReset, handoff, render, openSource }: StageProps & {
  stage: StageKey; title: string; description: string; handoff?: ReactNode; render: (detail: T, item: WorkItem) => ReactNode;
}) {
  const { state } = useWorkspace();
  const [focusId, setFocusId] = useState('');
  const [open, setOpen] = useState(false);
  const matches = state ? getWorkItemsByStage(state, stage).filter(item => visibleIds.has(item.esrId)) : [];
  const blocked = (item: WorkItem) => item.status === 'blocked' || item.blockingReasons.length > 0;
  const groups = {
    all: matches,
    exception: matches.filter(blocked),
    review: matches.filter(item => item.status === 'in-progress' && !blocked(item)),
    ready: matches.filter(item => item.status === 'queued' && !blocked(item)),
  };
  const rows = groups[activeFilter];
  const active = rows.find(item => item.id === focusId && item.esrId === selectedId) ?? rows.find(item => item.esrId === selectedId && item.entityId === selectedEntityId) ?? rows.find(item => item.esrId === selectedId) ?? rows[0];
  const claimant = (item: WorkItem) => state?.veteransById[state.esrsById[item.esrId]?.veteranId]?.displayName ?? item.esrId;
  const appointmentLabel = (item: WorkItem) => {
    const appointment = state?.appointmentsById[item.entityId];
    const exam = appointment && state?.examinationsById[appointment.examinationId];
    const requirement = exam && state?.examinationRequirementsById[exam.requirementIds[0]];
    return requirement?.title ?? item.title;
  };
  const workStatus = (item: WorkItem) => state?.appointmentsById[item.entityId]?.status ?? item.status;
  const queueList = useQueueRows(active?.id, open, `${activeFilter}:${query}`);
  const heading = useRef<HTMLHeadingElement>(null);
  return <div className="intake-page operations-page queue-workbench">
    <StageHeading stage={stage} description={description}><button type="button" className="intake-secondary" onClick={openSource}>Reference library</button></StageHeading>
    {handoff}
    <div className="intake-board">
      <aside className="intake-queue-card" aria-labelledby="stage-queue-heading">
        <div className="intake-queue-heading"><h2 id="stage-queue-heading"><HeadingText>{title}</HeadingText></h2><span>{rows.length} open</span></div>
        <button type="button" className="intake-queue-toggle" aria-expanded={open} aria-controls="stage-queue" onClick={() => setOpen(!open)}><Inbox size={17} />{open ? 'Hide work queue' : 'Choose a work item'}<small>{rows.length} open</small><ChevronDown size={16} /></button>
        <div id="stage-queue" className={`intake-queue-controls${open ? ' is-open' : ''}`}>
          <div className="intake-queue-toolbar"><label className="intake-search"><Search size={17} /><input type="search" value={query} onChange={event => onQueryChange(event.target.value)} placeholder="Search work queue" aria-label={`Search ${stage} queue`} /></label>
            <div className="intake-queue-filters" role="group" aria-label="Queue filters">{filters.map(filter =>
              <button key={filter.key} type="button" aria-pressed={activeFilter === filter.key} onClick={() => onFilterChange(filter.key)}>{filter.key === 'all' ? 'All work' : filter.label}<span>{groups[filter.key].length}</span></button>
            )}</div>
          </div>
          {rows.length ? <ul ref={queueList} className="intake-queue-list">{rows.map(item => <li key={item.id}>
            <button type="button" className="intake-queue-row" aria-pressed={item.id === active?.id} onClick={() => {
              setFocusId(item.id); onSelect(item.esrId);
              if (open) { setOpen(false); requestAnimationFrame(() => heading.current?.focus()); }
            }}>
              <span className="intake-queue-row-main"><strong>{appointmentLabel(item)}</strong><small>{claimant(item)}</small><small className="workflow-queue-reference">{practiceCaseLabel(state?.esrsById[item.esrId]) ?? item.esrId}</small></span>
              <Status text={formatStatus(workStatus(item))} tone={caseStateTone(workStatus(item))} />
            </button>
          </li>)}</ul> : <div className="intake-queue-empty"><p>No work matches this view.</p><button type="button" className="intake-text-action" onClick={onReset}>Reset filters</button></div>}
        </div>
      </aside>
      {active ? <section className="intake-case-card queue-detail" key={active.id} aria-labelledby="stage-work-title" tabIndex={0}>
        <header className="intake-case-header"><span className="intake-case-avatar"><Inbox size={20} aria-hidden="true" /></span><div className="intake-case-identity">
          <span>{practiceCaseLabel(state?.esrsById[active.esrId]) ?? active.esrId} · {claimant(active)}</span><h2 ref={heading} tabIndex={-1} id="stage-work-title">{active.title}</h2>
        </div><span className="intake-case-status"><Status text={formatStatus(active.status)} tone={caseStateTone(active.status)} /></span></header>
        <StageAIReview stage={stage} entityId={active.entityId} />
        <WorkDetail<T> item={active} render={render} />
      </section> : <section className="intake-case-empty"><Inbox size={25} /><h2><HeadingText>No work item selected</HeadingText></h2><p>Eligible records appear here as they enter this stage.</p></section>}
    </div>
  </div>;
}
