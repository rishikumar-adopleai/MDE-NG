import { useEffect, useRef, useState } from 'react';
import { ArrowRight, CheckCircle2, ChevronDown, ClipboardList, FileText, Inbox, MessageSquare, Search, X } from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import type { Contention, EvidenceDocument, TriagePlan, TriageWorkspaceView } from '../domain/model';
import { documentName, formatCategory, practiceCaseLabel } from './presentation';
import { EvidenceBrief, buildEvidenceBrief } from './EvidenceBrief';
import { Status } from './shared';
import { resultEntityId } from './StageWorkbench';
import { StageAIReview } from './StageAIReview';
import { StageHeading } from './StageHeading';

type QueueFilter = 'all' | 'exception' | 'review' | 'ready';
type TriageRow = TriageWorkspaceView['rows'][number];
type ExamChoice = TriageWorkspaceView['examChoices'][number];
type TriageProps = {
  openSource: () => void;
  query: string;
  onQueryChange: (value: string) => void;
  filters: { key: QueueFilter; label: string }[];
  activeFilter: QueueFilter;
  onFilterChange: (value: QueueFilter) => void;
  selectedId: string;
  onSelect: (id: string) => void;
  onScheduling: (id: string, entityId?: string) => void;
};

const pathwayLabels = {
  'in-person': 'In-person examination',
  'tele-c-and-p': 'Telehealth C&P',
  'ace-records-review': 'ACE records review',
};

function EvidenceList({ documents, contentionId }: { documents: EvidenceDocument[]; contentionId: string }) {
  return <section className="intake-case-content" aria-labelledby="triage-evidence-heading">
    <div className="intake-content-heading"><div><h3 id="triage-evidence-heading">Linked source documents</h3><p>Open a document for its summary. Check the cited text before approving the plan for this contention.</p></div></div>
    {documents.length ? <div className="triage-evidence-list review-document-list" role="region" tabIndex={0} aria-label="Linked evidence documents">
      {documents.map(document => {
        const brief = buildEvidenceBrief(document, undefined, contentionId);
        return <details className="triage-evidence" key={document.id}>
          <summary>
            <span className="triage-document-icon"><FileText size={19} /></span>
            <span className="triage-document-name"><strong>{documentName(brief.title)}</strong><small>{document.administrativeOnly
              ? 'Request metadata' : `${formatCategory(document.category)} · ${document.classifiedPageCount} of ${document.pageCount} pages readable`}</small></span>
            <ChevronDown className="triage-chevron" size={17} aria-hidden="true" />
          </summary>
          <div className="triage-evidence-content">
            <EvidenceBrief brief={brief} sourceName={document.sourceReference} administrative={document.category === 'va-request' && document.administrativeOnly} />
          </div>
        </details>;
      })}
    </div> : <p className="triage-inline-note">No source documents are linked to this contention.</p>}
  </section>;
}

function PlanForm({ row, choices, item, documents, onApproval }: {
  row: TriageRow;
  choices: ExamChoice[];
  item: Contention;
  documents: EvidenceDocument[];
  onApproval: (entityId?: string) => void;
}) {
  const { saveTriageDraft, approveTriagePlan, requestTriageClarification, resumeTriageReview } = useWorkspace();
  const [section, setSection] = useState<'evidence' | 'plan'>('evidence');
  const [plan, setPlan] = useState(row.initialPlan);
  const [clarificationOpen, setClarificationOpen] = useState(false);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');
  const choice = choices.find(exam => exam.code === plan.examCode);
  const canApprove = Boolean(choice && row.readableEvidence && plan.modality && plan.dbqs.includes(choice.primaryDbq) && plan.rationale.trim());
  const changeValues = (field: 'dbqs' | 'diagnostics', value: string, selected: boolean) => {
    setPlan(current => ({ ...current, [field]: selected ? [...current[field], value] : current[field].filter(entry => entry !== value) }));
  };
  const process = async (action: 'draft' | 'approve' | 'clarification' | 'resume') => {
    setBusy(true);
    setError('');
    setFeedback('');
    try {
      const id = row.contentionId;
      const result = await (action === 'draft' ? saveTriageDraft(id, plan) :
        action === 'approve' ? approveTriagePlan(id, plan) :
        action === 'clarification' ? requestTriageClarification(id, note) : resumeTriageReview(id, note));
      if (!result.ok) { setError(result.message); return; }
      if (action === 'approve') onApproval(resultEntityId(result.value));
      if (action === 'clarification' || action === 'resume') {
        setClarificationOpen(false);
        setNote('');
      }
      setFeedback(result.message);
    } finally {
      setBusy(false);
    }
  };

  return <>
    {row.specialInstructions && (
      <div className="triage-va-instructions" role="region" aria-label="VA Instructions from Intake">
        <div className="triage-va-header">
          <span className="triage-va-title"><strong>VA Request & Special Instructions</strong>{row.serviceBranch ? ` · ${row.serviceBranch}` : ''}</span>
          <span className={`va-sim-badge ${row.isSimulated ? 'simulated' : 'live'}`}>
            {row.isSimulated ? 'SIMULATED REQUEST' : 'OFFICIAL LIVE VA REQUEST'}
          </span>
        </div>
        <p className="triage-va-text">{row.specialInstructions}</p>
        {!!row.requestedDbqs?.length && (
          <div className="triage-va-dbqs">
            <span className="va-dbqs-label">Requested DBQ Worksheets:</span>
            <div className="va-dbq-tags">
              {row.requestedDbqs.map(dbq => (
                <span key={dbq} className="va-dbq-tag">{dbq}</span>
              ))}
            </div>
          </div>
        )}
      </div>
    )}
    <nav className="intake-sections" aria-label="Contention review sections">
      <button type="button" aria-pressed={section === 'evidence'} onClick={() => setSection('evidence')}><FileText size={16} />Evidence <span>{documents.length}</span></button>
      <button type="button" aria-pressed={section === 'plan'} onClick={() => setSection('plan')}><ClipboardList size={16} />Examination plan</button>
    </nav>
    {section === 'evidence' && <EvidenceList documents={documents} contentionId={row.contentionId} />}
    <div className="intake-case-content triage-plan" hidden={section !== 'plan'}>
      <div className="intake-content-heading"><div><h3>Examination plan</h3><p>Select the exam, pathway, and required Disability Benefits Questionnaires (DBQs).</p></div></div>
      {!!row.requestedDbqs?.length && <p className="triage-inline-note">Requested forms · {row.requestedDbqs.join(', ')}</p>}
      {row.awaitingResponse ? <p className="triage-inline-note">Record the clarification response to continue the examination plan.</p> :
        <fieldset className="triage-fields" disabled={busy}>
          <div className="triage-field-grid">
            <label className="intake-field"><span>Examination type</span>
              <select value={plan.examCode} onChange={event => {
                const selected = choices.find(exam => exam.code === event.target.value);
                setPlan({ examCode: event.target.value, modality: '', dbqs: selected ? [selected.primaryDbq] : [], diagnostics: [], rationale: plan.rationale });
              }}>
                <option value="">Select an examination</option>
                {choices.map(exam => <option key={exam.code} value={exam.code}>{exam.title}</option>)}
              </select>
              {item.status === 'awaiting-review' && row.initialPlan.examCode && plan.examCode === row.initialPlan.examCode &&
                <small className="triage-suggestion">Suggested from the request. Confirm against the evidence.</small>}
            </label>
            <label className="intake-field"><span>Examination pathway</span>
              <select disabled={!choice} value={plan.modality} onChange={event => setPlan({ ...plan, modality: event.target.value as TriagePlan['modality'] })}>
                <option value="">Select a pathway</option>
                {row.pathwaysByExam[plan.examCode]?.map(value => <option key={value} value={value}>{pathwayLabels[value]}</option>)}
              </select>
            </label>
          </div>
          {choice && <>
            <p className="triage-specialty"><b>{choice.specialty}</b> · {choice.credentials.join(" · ")}</p>
            <fieldset className="triage-options"><legend>Required DBQs</legend>
              {choice.dbqs.map(dbq => <label key={dbq}><input type="checkbox" checked={plan.dbqs.includes(dbq)} disabled={dbq === choice.primaryDbq} onChange={event => changeValues('dbqs', dbq, event.target.checked)} />{dbq}{dbq === choice.primaryDbq && <small>Required</small>}</label>)}
            </fieldset>
            {choice.diagnostics.length > 0 && <fieldset className="triage-options"><legend>Diagnostics <small>Optional</small></legend>
              {choice.diagnostics.map(name => <label key={name}><input type="checkbox" checked={plan.diagnostics.includes(name)} onChange={event => changeValues('diagnostics', name, event.target.checked)} />{name}</label>)}
            </fieldset>}
          </>}
          <label className="intake-field"><span>Reviewer rationale</span>
            <textarea value={plan.rationale} rows={3} maxLength={3000} placeholder="Summarize the evidence and why this pathway fits." onChange={event => setPlan({ ...plan, rationale: event.target.value })} />
          </label>
        </fieldset>}
    </div>
    <section className="triage-clarification" aria-label="Clarification">
      {row.awaitingResponse || clarificationOpen ? <>
        <h3>{row.awaitingResponse ? 'Clarification requested' : 'Request clarification'}</h3>
        {row.awaitingResponse && <p>{item.clarificationReason}</p>}
        <label className="intake-field"><span>{row.awaitingResponse ? 'Response received' : 'What needs clarification?'}</span>
          <textarea maxLength={3000} rows={3} value={note} onChange={event => setNote(event.target.value)} />
        </label>
        <div className="triage-action-buttons">
          {!row.awaitingResponse && <button type="button" className="intake-secondary" disabled={busy} onClick={() => { setClarificationOpen(false); setNote(''); }}>Cancel</button>}
          <button type="button" className="intake-secondary" disabled={busy || !note.trim()} onClick={() => void process(row.awaitingResponse ? 'resume' : 'clarification')}>
            {row.awaitingResponse ? 'Record response and reopen' : 'Record request'}
          </button>
        </div>
      </> : <>
        {item.clarificationResponse && <details className="triage-response"><summary>Recorded clarification response</summary><p>{item.clarificationResponse}</p></details>}
        <button type="button" className="intake-text-action" disabled={busy} onClick={() => setClarificationOpen(true)}><MessageSquare size={16} />Request clarification</button>
      </>}
    </section>
    {error && <p className="triage-error" role="alert">{error}</p>}
    <div className="intake-case-footer">
      <div>
        <span className="intake-readiness" data-ready={!row.awaitingResponse && row.readableEvidence}>
          <CheckCircle2 size={18} />{row.awaitingResponse ? 'Awaiting clarification' : row.readableEvidence ? 'Readable evidence linked' : 'Approval requires readable evidence'}
        </span>
        <span className="triage-feedback" role="status">{feedback}</span>
      </div>
      {!row.awaitingResponse && <div className="triage-action-buttons">
        {section === 'plan' ? <>
          <button type="button" className="intake-secondary" disabled={busy} onClick={() => void process('draft')}>Save draft</button>
          <button type="button" className="intake-primary" disabled={busy || !canApprove} onClick={() => void process('approve')}>Approve and send to Scheduling <ArrowRight size={16} /></button>
        </> : <button type="button" className="intake-primary" onClick={() => setSection('plan')}>Review examination plan <ArrowRight size={16} /></button>}
      </div>}
    </div>
  </>;
}

export function TriagePage({ query, onQueryChange, filters, activeFilter, onFilterChange, selectedId, onSelect, onScheduling, openSource }: TriageProps) {
  const { state } = useWorkspace();
  const [selectedContention, setSelectedContention] = useState('');
  const [queueOpen, setQueueOpen] = useState(false);
  const [approved, setApproved] = useState<{ esrId: string; title: string; entityId?: string }>();
  const caseHeading = useRef<HTMLHeadingElement>(null);
  const queueList = useRef<HTMLUListElement>(null);
  const { esrsById = {}, veteransById = {}, contentionsById = {}, evidenceDocumentsById = {}, triage } = state ?? {};
  const search = query.trim().toLowerCase();
  const matches = (triage?.rows ?? []).filter(row => {
    const esr = esrsById[row.esrId];
    const claimant = veteransById[esr.veteranId];
    return [row.esrId, esr.sourceReference, esr.practiceScenario?.name, esr.practiceScenario?.exercise, claimant?.displayName, contentionsById[row.contentionId].title]
      .some(value => value?.toLowerCase().includes(search));
  });
  const groups = {
    all: matches,
    exception: matches.filter(row => row.awaitingResponse || !row.readableEvidence),
    review: matches.filter(row => contentionsById[row.contentionId].status === 'in-review'),
    ready: matches.filter(row => contentionsById[row.contentionId].status === 'awaiting-review' && row.readableEvidence),
  };
  const rows = groups[activeFilter];
  const activeRow = rows.find(row => row.esrId === selectedId && row.contentionId === selectedContention) ??
    rows.find(row => row.esrId === selectedId) ?? rows[0];
  const active = activeRow && contentionsById[activeRow.contentionId];
  const activeEsr = activeRow && esrsById[activeRow.esrId];
  const activeClaimant = activeEsr && veteransById[activeEsr.veteranId]?.displayName;

  useEffect(() => {
    const list = queueList.current;
    const selected = list?.querySelector('button[aria-pressed="true"]');
    if (!list?.clientHeight || !selected) return;
    const bounds = list.getBoundingClientRect();
    const row = selected.getBoundingClientRect();
    if (row.top < bounds.top) list.scrollTop += row.top - bounds.top;
    if (row.bottom > bounds.bottom) list.scrollTop += row.bottom - bounds.bottom;
  }, [activeRow?.contentionId, queueOpen]);

  return <div className="intake-page triage-page">
    <StageHeading stage="Medical Triage" description="Review the evidence and prepare examination plans for Scheduling."><button type="button" className="intake-secondary" onClick={openSource}>Reference library</button></StageHeading>
    {approved && <div className="triage-success" role="status">
      <CheckCircle2 size={21} />
      <div><strong>Examination plan sent to Scheduling</strong><p>{approved.title} · {approved.esrId}</p></div>
      <button type="button" className="intake-secondary" onClick={() => onScheduling(approved.esrId, approved.entityId)}>Open Scheduling <ArrowRight size={16} /></button>
      <button type="button" className="intake-icon-button" aria-label="Dismiss approval message" onClick={() => setApproved(undefined)}><X size={17} /></button>
    </div>}
    <div className="intake-board">
      <aside className="intake-queue-card" aria-labelledby="triage-queue-title">
        <div className="intake-queue-heading"><h2 id="triage-queue-title">Triage queue</h2><span>{rows.length} {rows.length === 1 ? 'contention' : 'contentions'}</span></div>
        <button type="button" className="intake-queue-toggle" aria-expanded={queueOpen} aria-controls="triage-queue-content" onClick={() => setQueueOpen(!queueOpen)}>
          <Inbox size={17} />{queueOpen ? 'Hide triage queue' : 'Choose a contention'}<small>{rows.length} open</small><ChevronDown size={16} />
        </button>
        <div className={`intake-queue-controls${queueOpen ? ' is-open' : ''}`} id="triage-queue-content">
          <div className="intake-queue-toolbar">
            <label className="intake-search"><Search size={17} aria-hidden="true" /><input placeholder="Search contention or ESR" aria-label="Search Medical Triage queue" value={query} onChange={event => onQueryChange(event.target.value)} /></label>
            <div className="intake-queue-filters" role="group" aria-label="Triage queue filters">
              {filters.map(filter => <button type="button" key={filter.key} aria-pressed={activeFilter === filter.key} onClick={() => onFilterChange(filter.key)}>{filter.label}<span>{groups[filter.key].length}</span></button>)}
            </div>
          </div>
          {rows.length ? <ul className="intake-queue-list" ref={queueList}>{rows.map(row => {
            const item = contentionsById[row.contentionId];
            const esr = esrsById[row.esrId];
            const claimant = veteransById[esr.veteranId]?.displayName ?? esr.id;
            return <li key={row.contentionId}><button type="button" className="intake-queue-row" aria-pressed={activeRow?.contentionId === row.contentionId}
              onClick={() => { setSelectedContention(row.contentionId); onSelect(row.esrId); setQueueOpen(false); requestAnimationFrame(() => caseHeading.current?.focus()); }}>
              <span className="intake-queue-row-main"><strong>{item.title}</strong><small>{claimant}</small>{esr.practiceScenario && <small>{esr.practiceScenario.exercise ?? 'Practice case'}</small>}</span>
              {row.awaitingResponse ? <Status text="Clarify" tone="amber" /> : row.priority === 'urgent' ? <Status text="Urgent" tone="amber" /> : null}
            </button></li>;
          })}</ul> : <div className="intake-queue-empty"><p>No contentions in this view.</p>{(query || activeFilter !== 'all') && <button type="button" className="intake-text-action" onClick={() => { onQueryChange(''); onFilterChange('all'); }}>Reset filters</button>}</div>}
        </div>
      </aside>
      {active && activeRow && activeEsr ? <section className="intake-case-card" aria-labelledby="triage-case-title">
        <header className="intake-case-header">
          <span className="intake-case-avatar" aria-hidden="true"><ClipboardList size={21} /></span>
          <div className="intake-case-identity">
            <span>{practiceCaseLabel(activeEsr) ?? activeEsr.id}{activeClaimant && ` · ${activeClaimant}`}</span>
            <h2 id="triage-case-title" tabIndex={-1} ref={caseHeading}>{active.title}</h2>
          </div>
          <span className="intake-case-status"><Status text={activeRow.awaitingResponse ? 'Awaiting clarification' : active.status === 'in-review' ? 'In review' : 'Awaiting review'} tone={activeRow.awaitingResponse ? 'amber' : 'slate'} /></span>
        </header>
        <StageAIReview key={`ai-${active.id}`} stage="Medical Triage" entityId={active.id} />
        <PlanForm key={active.id} row={activeRow} item={active} choices={triage?.examChoices ?? []}
          documents={activeRow.documentIds.flatMap(id => evidenceDocumentsById[id] ? [evidenceDocumentsById[id]] : [])}
          onApproval={entityId => setApproved({ esrId: activeRow.esrId, title: active.title, entityId })} />
      </section> : <section className="intake-case-empty"><Inbox size={24} /><h2>No contentions to review</h2><p>Validated Intake cases appear here when they are ready for Medical Triage.</p></section>}
    </div>
  </div>;
}
