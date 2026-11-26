import { HeadingText } from './HeadingText';
import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertCircle, CheckCircle2, X, Hospital } from 'lucide-react';
import { WorkspaceProvider, useWorkspace } from './context/WorkspaceContext';
import { getWorkItemsByStage, searchESRs } from './domain/selectors';
import { AccountAccess } from './context/DemoSignIn';
import type { IntakeStatus } from './domain/model';
import { ReadOnlyStageQueue } from './stages/ReadOnlyStageQueue';
import { IntakePage } from './stages/IntakePage';
import { TriagePage } from './stages/TriagePage';
import { SchedulingPage } from './stages/SchedulingPage';
import { ClinicalPage } from './stages/ClinicalPage';
import { QAPage } from './stages/QAPage';
import { DeliveryPage } from './stages/DeliveryPage';
import { SourceStageRecords } from './stages/SourceStageRecords';
import type { StageKey } from './stages/model';
import { dateLabel, formatActivityLabel, formatWorkspaceCopy, toCaseItem } from './stages/presentation';
import { WorkspaceNavigation, WorkspaceTopbar } from './WorkspaceNavigation';
import { stageFromSearch, stageHref } from './workspaceRoutes';

function WorkspaceApp() {
  const { state, loading, storageError, runtime } = useWorkspace();
  const [stage, setStage] = useState<StageKey>(() => stageFromSearch(window.location.search));
  const [selectedId, setSelectedId] = useState('');
  const [selectedEntityId, setSelectedEntityId] = useState<string>();
  const [intakeStatus, setIntakeStatus] = useState<IntakeStatus | 'all'>('all');
  const [stageView, setStageView] = useState<'queue' | 'source'>();
  const [query, setQuery] = useState('');
  const [queueFilter, setQueueFilter] = useState<'all' | 'exception' | 'review' | 'ready'>('all');
  const [mobileNav, setMobileNav] = useState(false);
  const [toast, setToast] = useState<{ message: string; esrId?: string; stage: StageKey }>();
  const toastTimer = useRef<number | undefined>(undefined);
  const [activityOpen, setActivityOpen] = useState(false);
  const activityCloseRef = useRef<HTMLButtonElement>(null);
  const pageScrollRef = useRef<HTMLDivElement>(null);
  const focusStageHeading = useRef(false);

  const resetStage = useCallback((name: StageKey) => {
    focusStageHeading.current = true;
    setStage(name);
    setStageView(undefined);
    setSelectedEntityId(undefined);
    setQueueFilter('all');
    setQuery('');
    setActivityOpen(false);
    setMobileNav(false);
  }, []);

  useEffect(() => {
    const onPopState = () => resetStage(stageFromSearch(window.location.search));
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, [resetStage]);

  useEffect(() => {
    const scroll = pageScrollRef.current;
    if (scroll) scroll.scrollTop = 0;
    if (!scroll || !focusStageHeading.current) return;
    const frame = requestAnimationFrame(() => {
      focusStageHeading.current = false;
      scroll.querySelector('h1')?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [stage]);
  useEffect(() => {
    if (!activityOpen || stage !== 'Intake') return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    activityCloseRef.current?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setActivityOpen(false);
      if (event.key === 'Tab') {
        event.preventDefault();
        activityCloseRef.current?.focus();
      }
    };
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('keydown', handleKey);
      opener?.focus();
    };
  }, [activityOpen, stage]);

  const selectStage = (name: StageKey) => {
    if (stage === name) {
      setMobileNav(false);
      return;
    }
    window.history.pushState(null, '', stageHref(name, window.location.search));
    resetStage(name);
  };
  const selectESR = setSelectedId;

  if (loading) return <div className="app-backdrop"><div className="workspace-loading"><Hospital size={24} /><b>Loading examination workspace</b></div></div>;
  if (!state && runtime?.mode === 'dev' && runtime.authRequired && !storageError) return <AccountAccess />;
  if (!state) return <div className="app-backdrop"><div className="workspace-loading" role="alert"><AlertCircle size={24} /><b>Workspace unavailable</b><span>{storageError || 'Production sign-in requires a configured identity provider.'}</span><button className="btn btn-outline" onClick={() => window.location.reload()}>Retry</button></div></div>;

  const stageItems = getWorkItemsByStage(state, stage);
  const showSource = runtime?.simulationEnabled && stage !== 'Intake' && stageView === 'source';
  const stageIds = new Set(stageItems.map(item => item.esrId));
  const matches = searchESRs(state, query).filter(item => stageIds.has(item.id) &&
    (stage !== 'Intake' || intakeStatus === 'all' || item.intakeStatus === intakeStatus));
  const matchedIds = new Set(matches.map(item => item.id));
  const intakeViews = stage === 'Intake' ? state.intake?.viewsByEsrId : undefined;
  const hasException = (item: typeof stageItems[number]) => item.status === 'blocked' || item.blockingReasons.length > 0 ||
    Boolean(intakeViews?.[item.esrId]?.needsReadableEvidence);
  const stageExceptionCount = stage === 'Medical Triage' && state.triage
    ? new Set(state.triage.rows.filter(row => row.awaitingResponse || !row.readableEvidence).map(row => row.esrId)).size
    : new Set(stageItems.filter(hasException).map(item => item.esrId)).size;
  const exceptionIds = new Set(stageItems.filter(item => matchedIds.has(item.esrId) && hasException(item)).map(item => item.esrId));
  const reviewIds = new Set(stageItems.filter(item => matchedIds.has(item.esrId) &&
    (item.status === 'in-progress' || ['Medical Triage', 'Clinical Evaluation', 'Internal QA'].includes(stage)))
    .map(item => item.esrId));
  const readyIds = new Set(stageItems.filter(item => matchedIds.has(item.esrId) &&
    (intakeViews ? intakeViews[item.esrId]?.canValidate : item.status === 'queued' && !exceptionIds.has(item.esrId)))
    .map(item => item.esrId));
  const queueItems = [
    { label: 'All cases', key: 'all' as const, ids: matchedIds },
    { label: 'Exceptions', key: 'exception' as const, ids: exceptionIds },
    { label: 'Review', key: 'review' as const, ids: reviewIds },
    { label: 'Ready', key: 'ready' as const, ids: readyIds },
  ];
  const visibleIds = (queueItems.find(item => item.key === queueFilter) ?? queueItems[0]).ids;
  const actualSelectedId = visibleIds.has(selectedId) ? selectedId
    : intakeViews ? state.intake?.queueIds.find(id => visibleIds.has(id))
      : matches.find(item => visibleIds.has(item.id))?.id;
  const notify = (message: string) => {
    window.clearTimeout(toastTimer.current);
    setToast({ message, esrId: actualSelectedId, stage });
    toastTimer.current = window.setTimeout(() => setToast(undefined), 3600);
  };
  const selectedESR = actualSelectedId ? state.esrsById[actualSelectedId] : undefined;
  const stageChange = (name: StageKey) => selectStage(name);
  const nextStage: Partial<Record<StageKey, StageKey>> = { Intake: 'Medical Triage', 'Medical Triage': 'Scheduling', Scheduling: 'Clinical Evaluation', 'Clinical Evaluation': 'Internal QA', 'Internal QA': 'VA Delivery & Review' };
  const onContinue = (esrId: string) => {
    const next = nextStage[stage];
    if (next) {
      selectESR(esrId);
      stageChange(next);
    }
  };
  const openSource = () => setStageView('source');
  const stageProps = {selectedEntityId, openSource, filters: queueItems.map(({key, label}) => ({key, label})),
    activeFilter: queueFilter, onFilterChange: setQueueFilter, query, onQueryChange: setQuery, visibleIds: matchedIds, selectedId, onSelect: selectESR,
    onReset: () => { setQuery(''); setQueueFilter('all'); },
    onNavigate: (target: StageKey, id: string, entityId?: string) => { selectESR(id); selectStage(target); setSelectedEntityId(entityId); },
  };
  const activities = selectedESR ? Object.values(state.activitiesById).filter((item) => item.esrId === selectedESR.id).sort((a, b) => b.at.localeCompare(a.at)).slice(0, 100) : [];

  return <div className="app-backdrop"><div className="app-shell workflow-shell" data-stage={stage}>
    <a className="workflow-skip-link" href="#workspace-content">Skip to content</a>
    <WorkspaceNavigation stage={stage} onNavigate={selectStage} open={mobileNav} onClose={() => setMobileNav(false)} />
    <main className="main-area" id="workspace-content" tabIndex={-1}>
      <WorkspaceTopbar openNavigation={() => setMobileNav(true)} navigationOpen={mobileNav}
        canSignOut={runtime?.mode === 'dev'} exceptions={stageExceptionCount} exceptionsSelected={queueFilter === 'exception'}
        onExceptions={() => {
          setStageView('queue');
          setQuery('');
          setIntakeStatus('all');
          setQueueFilter(queueFilter === 'exception' ? 'all' : 'exception');
        }} />
      {storageError && <div className="storage-warning" role="alert"><AlertCircle size={16} /><span><b>Workspace is read-only</b> The service returned an error: {storageError} Changes cannot be saved.</span></div>}
      <div className="page-scroll" ref={pageScrollRef}><div className="page-main">
        {runtime?.simulationEnabled === false && <ReadOnlyStageQueue key={stage} stage={stage} state={state}
          rows={matches.filter(item => visibleIds.has(item.id)).map(item => toCaseItem(state, item))} selectedId={actualSelectedId} onSelect={selectESR}
          filters={queueItems.map(({key, label, ids}) => ({key, label, count: ids.size}))} activeFilter={queueFilter} onFilterChange={setQueueFilter}
          query={query} onQueryChange={setQuery} onReset={stageProps.onReset} onHistory={() => setActivityOpen(true)} />}
        {runtime?.simulationEnabled && stage === 'Intake' && <IntakePage onReceived={id => { setQuery(''); setQueueFilter('all'); setIntakeStatus('all'); selectESR(id); }} intakeStatus={intakeStatus} onIntakeStatus={setIntakeStatus} query={query} onQueryChange={setQuery} filters={queueItems.map(({ key, label, ids }) => ({ key, label, count: ids.size }))} activeFilter={queueFilter} onFilterChange={setQueueFilter} onHistory={() => setActivityOpen(true)} visibleIds={visibleIds} selectedId={actualSelectedId ?? ''} onSelect={selectESR} notify={notify} onContinue={onContinue} />}
        {showSource && <SourceStageRecords key={stage} stage={stage} openQueue={() => setStageView('queue')} />}
        {runtime?.simulationEnabled && !showSource && stage === 'Medical Triage' && <TriagePage openSource={openSource} query={query} onQueryChange={setQuery} filters={queueItems.map(({ key, label }) => ({ key, label: key === 'all' ? 'All contentions' : label }))} activeFilter={queueFilter} onFilterChange={setQueueFilter} selectedId={selectedId} onSelect={selectESR} onScheduling={(id, entityId) => stageProps.onNavigate('Scheduling', id, entityId)} />}
        {runtime?.simulationEnabled && !showSource && stage === 'Scheduling' && <SchedulingPage {...stageProps} />}
        {runtime?.simulationEnabled && !showSource && stage === 'Clinical Evaluation' && <ClinicalPage {...stageProps} />}
        {runtime?.simulationEnabled && !showSource && stage === 'Internal QA' && <QAPage {...stageProps} />}
        {runtime?.simulationEnabled && !showSource && stage === 'VA Delivery & Review' && <DeliveryPage {...stageProps} />}
      </div></div></main>
  </div>{toast && <div className="toast workflow-toast" role="status"><span className="toast-icon"><CheckCircle2 size={18} /></span><span className="toast-copy"><small><HeadingText>{toast.message.toLowerCase().includes('could not') || toast.message.toLowerCase().includes('required') ? 'Action needs attention' : 'Action recorded'}</HeadingText></small><b>{toast.message}</b><em>{toast.esrId} · {toast.stage}</em></span><button type="button" aria-label="Dismiss notification" onClick={() => setToast(undefined)}><X size={15} /></button></div>}
    {activityOpen && selectedESR && <div className="activity-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setActivityOpen(false); }}><section className="activity-dialog" role="dialog" aria-modal="true" aria-labelledby="activity-title"><header><div><small><HeadingText>Workspace history</HeadingText></small><h2 id="activity-title">{selectedESR.id} Activity</h2></div><button type="button" className="icon-button" aria-label="Close activity history" ref={activityCloseRef} onClick={() => setActivityOpen(false)}><X size={18} /></button></header><p>These records summarize workspace activity. They are not an immutable audit record or proof of VA activity.</p><div className="activity-list">{activities.map((event) => <article key={event.id}><span><b>{formatActivityLabel(event.action)}</b><small>{event.entityType} · {event.entityId}</small></span><time dateTime={event.at}>{dateLabel(event.at)}</time>{event.rationale && <p>{formatWorkspaceCopy(event.rationale)}</p>}</article>)}{activities.length === 0 && <div className="empty-state">No workflow events have been recorded for this ESR.</div>}</div></section></div>}
  </div>;
}

export default function App() {
  return <WorkspaceProvider><WorkspaceApp /></WorkspaceProvider>;
}
