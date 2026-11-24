import { useEffect, useRef, useState, type ReactNode } from 'react';
import {
  Activity, AlertCircle, ArrowRight, Check, CheckCircle2, ChevronDown,
  ChevronRight, FileText, FlaskConical, Folder, Inbox, Layers, Plus, RefreshCw, Search, ShieldAlert, ShieldCheck, Trash2, X,
} from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import type { EvidenceDocument, IntakeStatus, LogicalDocument } from '../domain/model';
import { CasePanel, Status } from './shared';
import { DocumentReviewAssistant } from './DocumentReviewAssistant';
import { StageHeading } from './StageHeading';
import { IntakeImport } from './IntakeImport';
import { caseStateTone, dateLabel, documentName, formatActivityLabel, formatCategory, formatStatus, formatWorkspaceCopy, practiceCaseLabel, toCaseItem } from './presentation';

type IntakeFilter = 'all' | IntakeStatus;
type QueueFilter = 'all' | 'exception' | 'review' | 'ready';
type CaseSection = 'documents' | 'exceptions' | 'activity';
type IntakeProps = {
  intakeStatus: IntakeFilter;
  onIntakeStatus: (status: IntakeFilter) => void;
  query: string;
  onQueryChange: (value: string) => void;
  filters: { key: QueueFilter; label: string; count: number }[];
  activeFilter: QueueFilter;
  onFilterChange: (filter: QueueFilter) => void;
  visibleIds: ReadonlySet<string>;
  selectedId: string;
  onSelect: (id: string) => void;
  onReceived: (id: string) => void;
  onHistory: () => void;
  notify: (message: string) => void;
  onContinue: (esrId: string) => void;
};

const steps = ['Receipt', 'Acknowledgement', 'Documents', 'Exceptions', 'Triage handoff'];
const sections: { key: CaseSection; label: string; icon: typeof FileText }[] = [
  { key: 'documents', label: 'Documents', icon: FileText },
  { key: 'exceptions', label: 'Exceptions', icon: AlertCircle },
  { key: 'activity', label: 'Activity', icon: Activity },
];

function ImportDialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);

  return <dialog className="intake-dialog" ref={dialog} aria-labelledby="intake-dialog-title"
    onCancel={event => { event.preventDefault(); onClose(); }}>
    <div className="intake-dialog-heading">
      <h2 id="intake-dialog-title">{title}</h2>
      <button type="button" className="intake-icon-button" aria-label="Close import" onClick={onClose}><X size={19} /></button>
    </div>
    {children}
  </dialog>;
}

const CATEGORY_OPTIONS = [
  { value: 'service-treatment', label: 'Service Treatment Records' },
  { value: 'va-medical-records', label: 'VA Medical Records' },
  { value: 'private-medical-records', label: 'Private Medical Records' },
  { value: 'laboratory', label: 'Lab Reports' },
  { value: 'imaging', label: 'Diagnostic Imaging' },
  { value: 'prior-examination', label: 'Previous DBQs' },
  { value: 'other', label: 'Other Documents' },
];

function SourceRow({ document, onRetry, onReclassify, busy, isDuplicate }: {
  document: LogicalDocument | EvidenceDocument;
  onRetry?: () => void;
  onReclassify?: (category: string) => void;
  busy: boolean;
  isDuplicate?: boolean;
}) {
  const result = 'processing' in document ? document.processing : undefined;
  const isLogical = 'sectionId' in document;
  const indexed = document.classificationStatus === 'indexed';
  const review = document.classificationStatus === 'needs-human-review';
  const ai = result?.provider === 'ai';
  const excerpts = result?.sources?.length ? result.sources : Array.from(new Map(
    ('syntheticExcerpts' in document ? document.syntheticExcerpts : undefined)?.map(source => [source.pageNumber, { page: source.pageNumber, text: source.text }]) ?? [],
  ).values());
  const summary = isLogical ? document.summary : result?.summary;
  const showSummary = Boolean(summary);
  const quality = 'quality' in document ? document.quality : undefined;
  const sections = 'sections' in document ? document.sections : undefined;
  const formatRaw = (quality?.format || document.sourceReference.split('.').pop() || 'PDF').toUpperCase();
  const formatBadgeClass = formatRaw.includes('XML') ? 'xml' : ['PNG', 'JPG', 'JPEG', 'TIFF', 'BMP'].includes(formatRaw) ? 'image' : 'pdf';
  const ocrApplied = isLogical ? document.ocrApplied : quality?.ocrApplied;
  const ocrConfidence = isLogical ? document.ocrConfidence : quality?.ocrConfidence;
  const wrongPatient = isLogical ? document.wrongPatient : quality?.claimantMatch === 'mismatch';
  const readabilityScore = isLogical ? document.readabilityScore : quality?.readabilityScore;
  const actionRequired = isLogical ? document.actionRequired : review;

  return <li className={`intake-source${actionRequired ? ' is-action-required' : ''}`}>
    <span className="intake-source-icon"><FileText size={19} aria-hidden="true" /></span>
    <div className="intake-source-content">
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
        <span className={`intake-format-badge ${formatBadgeClass}`}>{formatRaw}</span>
        <b>{documentName(document.title)}</b>

        {!document.administrativeOnly && onReclassify ? (
          <select
            className="intake-category-select"
            value={document.category === 'hospital' ? 'va-medical-records' : document.category}
            onChange={e => onReclassify(e.target.value)}
            disabled={busy}
            title="Review or correct document classification"
          >
            {CATEGORY_OPTIONS.map(opt => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        ) : (
          <span className="intake-source-tag">{formatCategory(document.category)}</span>
        )}

        {actionRequired && (
          <span className="intake-issue-pill" style={{ background: '#fef2f2', color: '#b91c1c', borderColor: '#fca5a5', fontWeight: 600 }}>
            Action Required
          </span>
        )}
        {ocrApplied && (
          <span className="intake-ocr-badge" title="Optical Character Recognition performed on scanned pages">
            OCR Applied ({ocrConfidence ?? 95}%)
          </span>
        )}
        {(isDuplicate || (isLogical && document.isDuplicate)) && (
          <span className="intake-issue-pill duplicate" title={isLogical && document.duplicateOf ? `Duplicate of ${document.duplicateOf}` : undefined}>
            {isLogical && document.duplicateOf ? `Duplicate (${document.duplicateOf})` : 'Possible Duplicate'}
          </span>
        )}
        {wrongPatient && <span className="intake-issue-pill mismatch">Claimant Mismatch</span>}
        {(readabilityScore === 0 || ('unreadablePageCount' in document && document.unreadablePageCount > 0)) && (
          <span className="intake-issue-pill unreadable">Unreadable Scan</span>
        )}
      </div>

      <p>
        {isLogical ? (
          <>
            <strong style={{ color: '#0f172a' }}>{document.parentFileName}</strong>
            {' · '}
            <span>pp. {document.startPage}–{document.endPage} ({document.pageCount} {document.pageCount === 1 ? 'page' : 'pages'})</span>
            {readabilityScore !== undefined && ` · ${readabilityScore}% readable`}
          </>
        ) : (
          <>
            {document.sourceReference !== document.title && <>{documentName(document.sourceReference)} · </>}
            {document.administrativeOnly ? (document.category === 'va-request' ? 'Request metadata' : 'Document reference') : document.pageCount
              ? `${document.pageCount} ${document.pageCount === 1 ? 'page' : 'pages'}` : 'Page count pending'}
            {readabilityScore !== undefined && ` · ${readabilityScore}% readable`}
            {document.sourceHash && ` · SHA: ${document.sourceHash.slice(0, 8)}`}
          </>
        )}
      </p>

      {sections && sections.length > 1 && (
        <div style={{ marginTop: '8px' }}>
          <small style={{ fontWeight: 600, color: 'var(--intake-muted)', display: 'block', marginBottom: '3px' }}>
            Multi-Report Boundary Split ({sections.length} logical reports detected):
          </small>
          <div className="intake-section-chips">
            {sections.map(sec => (
              <span key={sec.sectionId} className="intake-section-chip" title={sec.summary || sec.title}>
                {sec.title} (pp. {sec.startPage}–{sec.endPage})
              </span>
            ))}
          </div>
        </div>
      )}

      <details className="intake-excerpts">
        <summary><ChevronRight size={15} aria-hidden="true" /> Report excerpts & extracted data</summary>
        {result?.error && <p className="intake-source-error">{formatWorkspaceCopy(result.error)}</p>}
        {result?.aiUnavailable && <p>Document checks completed. AI summary unavailable.</p>}

        {isLogical && document.issues && document.issues.length > 0 && (
          <div style={{ background: '#fff1f2', border: '1px solid #fecdd3', padding: '8px 10px', borderRadius: '6px', margin: '8px 0', fontSize: '12px', color: '#9f1239' }}>
            <b>Intake Issues Identified:</b>
            <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
              {document.issues.map((iss, idx) => <li key={idx}>{iss}</li>)}
            </ul>
          </div>
        )}

        {showSummary && <div className="intake-document-summary">
          <b>{ai ? 'AI summary' : 'Clinical summary'}</b>
          <p>{summary}</p>
        </div>}

        {excerpts.map(source => <blockquote key={source.page}><b>Page {source.page}</b><p>{source.text}</p></blockquote>)}

        {document.extractedData?.labPanels && document.extractedData.labPanels.length > 0 && (
          <div className="intake-lab-findings">
            <div className="intake-lab-header">
              <FlaskConical size={16} />
              <span>Extracted Clinical Laboratory Findings ({document.extractedData.labPanels.length} {document.extractedData.labPanels.length === 1 ? 'panel' : 'panels'})</span>
            </div>
            {document.extractedData.labPanels.map((panel, idx) => (
              <div key={idx} className="intake-lab-panel">
                <div className="intake-lab-panel-title">
                  <span>{panel.panelName}</span>
                  <small>{panel.specimen && `${panel.specimen} · `}Collected: {panel.collectionDate || 'On file'}</small>
                </div>
                <table className="intake-lab-table">
                  <thead>
                    <tr>
                      <th>Test Name</th>
                      <th>Result</th>
                      <th>Units</th>
                      <th>Reference Range</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {panel.results.map((r, rIdx) => (
                      <tr key={rIdx} className={r.flag !== 'normal' ? 'is-abnormal' : ''}>
                        <td><b>{r.name}</b></td>
                        <td><b>{r.value}</b></td>
                        <td>{r.units}</td>
                        <td>{r.referenceRange}</td>
                        <td>
                          <span className={`intake-lab-flag ${r.flag}`}>
                            {r.flag === 'critical' ? 'Critical' : r.flag === 'abnormal' ? 'Abnormal' : 'Normal'}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        )}
      </details>
      {result?.retryable && onRetry && <button type="button" className="intake-text-action" disabled={busy} onClick={onRetry}><RefreshCw size={15} /> {result.aiUnavailable ? 'Retry AI summary' : 'Retry document'}</button>}
    </div>
    <div className="intake-source-actions">
      <Status
        text={actionRequired ? 'Action Required' : indexed ? 'Indexed' : review ? 'Needs Review' : 'Processing'}
        tone={actionRequired ? 'amber' : indexed ? 'green' : review ? 'amber' : 'blue'}
      />
    </div>
  </li>;
}

export function IntakePage(props: IntakeProps) {
  const { intakeStatus, onIntakeStatus, query, onQueryChange, filters, activeFilter, onFilterChange,
    visibleIds, selectedId, onSelect, onReceived, onHistory, notify, onContinue } = props;
  const { state, retryIntakeDocument, reviewIntakeException, recordIntakeReceipt,
    completeDocumentProcessing, updateDocumentCategory, validateIntake } = useWorkspace();
  const [section, setSection] = useState<CaseSection>('documents');
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [expandedInstructions, setExpandedInstructions] = useState(false);
  const [mobileQueueOpen, setMobileQueueOpen] = useState(false);
  const [reviewNotes, setReviewNotes] = useState<Record<string, string>>({});
  const [importTarget, setImportTarget] = useState<{ type: 'esr' } | { type: 'source'; esrId: string }>();
  const [processing, setProcessing] = useState(false);
  const [receiptReferences, setReceiptReferences] = useState<Record<string, string>>({});
  const caseHeading = useRef<HTMLHeadingElement>(null);
  const queueList = useRef<HTMLUListElement>(null);

  useEffect(() => {
    const list = queueList.current;
    const active = list?.querySelector('button[aria-pressed="true"]');
    if (!list?.clientHeight || !active) return;
    const viewport = list.getBoundingClientRect();
    const row = active.getBoundingClientRect();
    if (row.top < viewport.top) list.scrollTop += row.top - viewport.top;
    if (row.bottom > viewport.bottom) list.scrollTop += row.bottom - viewport.bottom;
  }, [selectedId, mobileQueueOpen, query, intakeStatus, activeFilter]);

  if (!state) return null;

  const esrs = (state.intake?.queueIds ?? []).filter(id => visibleIds.has(id)).map(id => state.esrsById[id]);
  const selected = state.esrsById[selectedId];
  const view = state.intake?.viewsByEsrId[selectedId];
  const caseItem = selected ? toCaseItem(state, selected) : undefined;
  const documents = view?.documentIds.map(id => state.evidenceDocumentsById[id]) ?? [];
  const exceptions = view?.exceptionIds.map(id => state.intakeExceptionsById[id]) ?? [];
  const timeline = view?.activityIds.map(id => state.activitiesById[id]) ?? [];
  const receiptReference = receiptReferences[selectedId] ?? '';

  // Separate clinical evidence documents from administrative request metadata
  const medicalDocuments = documents.filter(d => !d.administrativeOnly && d.category !== 'va-request');
  const metadataDocuments = documents.filter(d => d.administrativeOnly || d.category === 'va-request');

  const organizer = view?.aiOrganizer;
  const logicalMedicalDocs = organizer?.logicalDocuments ?? [];
  const logicalMetadataDocs = organizer?.logicalMetadataDocuments ?? [];

  // When logical document segmentation is available, make it the primary organization unit
  const medicalItems = logicalMedicalDocs.length > 0 ? logicalMedicalDocs : medicalDocuments;

  const duplicateDocIds = new Set(organizer?.duplicateDocumentIds ?? []);

  const ALL_CATEGORY_CONFIG = [
    { key: 'claims-and-applications', label: 'Claims and Applications' },
    { key: 'service-treatment', label: 'Service Treatment Records (STR)' },
    { key: 'military-personnel', label: 'Military Personnel Records' },
    { key: 'va-medical-records', label: 'VA Medical Records' },
    { key: 'private-medical-records', label: 'Private Medical Records' },
    { key: 'prior-examination', label: 'Previous Examinations & DBQs' },
    { key: 'laboratory', label: 'Laboratory Reports' },
    { key: 'imaging', label: 'Diagnostic Imaging' },
    { key: 'diagnostic-tests', label: 'Diagnostic & Ancillary Tests' },
    { key: 'hospital-surgical', label: 'Hospital & Surgical Records' },
    { key: 'medication-prescription', label: 'Medication & Prescription Records' },
    { key: 'medical-opinions', label: 'Medical Opinions' },
    { key: 'health-assessments', label: 'Health Assessments' },
    { key: 'authorization-consent', label: 'Authorization & Consent' },
    { key: 'lay-witness', label: 'Lay & Witness Statements' },
    { key: 'representation', label: 'Representation Documents' },
    { key: 'decisions-appeals', label: 'Disability Decisions & Appeals' },
    { key: 'insurance-financial', label: 'Insurance & Financial Records' },
    { key: 'administrative-correspondence', label: 'Administrative Correspondence' },
    { key: 'other', label: 'Others' },
  ];

  const categoryFolders = [
    {
      key: 'all',
      label: 'All Medical Evidence',
      docCount: medicalItems.length,
      pageCount: medicalItems.reduce((sum, d) => sum + (d.pageCount || 0), 0),
    },
    ...ALL_CATEGORY_CONFIG.map(cfg => {
      const matching = medicalItems.filter(d => d.category === cfg.key || (cfg.key === 'va-medical-records' && d.category === 'hospital'));
      return {
        key: cfg.key,
        label: cfg.label,
        docCount: matching.length,
        pageCount: matching.reduce((sum, d) => sum + (d.pageCount || 0), 0),
      };
    })
  ].filter(item => item.key === 'all' || item.docCount > 0);

  const filteredMedicalDocuments = selectedCategory === 'all'
    ? medicalItems
    : selectedCategory === 'va-medical-records'
      ? medicalItems.filter(d => d.category === 'va-medical-records' || d.category === 'hospital')
      : medicalItems.filter(d => d.category === selectedCategory);

  async function run(promise: Promise<{ ok: boolean; message: string }>, after?: () => void) {
    setProcessing(true);
    try {
      const result = await promise;
      notify(result.message);
      if (result.ok) after?.();
    } finally {
      setProcessing(false);
    }
  }

  function pickCase(id: string) {
    setSection('documents');
    setSelectedCategory('all');
    onSelect(id);
    if (mobileQueueOpen) {
      setMobileQueueOpen(false);
      requestAnimationFrame(() => caseHeading.current?.focus());
    }
  }

  function imported(id: string) {
    setImportTarget(undefined);
    if (importTarget?.type === 'source') {
      notify('Source document queued.');
    } else {
      setSection('documents');
      setMobileQueueOpen(false);
      onReceived(id);
    }
  }

  return <div className="intake-page">
    <StageHeading stage="Intake" description="Receive exam requests and review source documents before triage.">
      <button type="button" className="intake-primary" onClick={() => setImportTarget({ type: 'esr' })}><Plus size={19} /> New ESR</button>
    </StageHeading>

    <div className={`intake-board${!selected ? ' intake-board-empty' : ''}`}>
      <aside className="intake-queue-card" aria-label="Intake queue">
        <button type="button" className="intake-queue-toggle" aria-expanded={mobileQueueOpen || !selected}
          aria-controls="intake-queue-controls" disabled={!selected} onClick={() => setMobileQueueOpen(value => !value)}>
          <Inbox size={18} /><span>Intake queue</span><small>{esrs.length} cases</small><ChevronDown size={17} />
        </button>
        <div id="intake-queue-controls" className={`intake-queue-controls${mobileQueueOpen || !selected ? ' is-open' : ''}`}>
          <header className="intake-queue-heading"><h2>Intake queue</h2><span>{esrs.length} cases</span></header>
          <div className="intake-queue-toolbar">
            <label className="intake-search"><Search size={17} aria-hidden="true" /><input value={query}
              aria-label="Search Intake cases" placeholder="Search ESR or claimant"
              onChange={event => onQueryChange(event.target.value)} />
              {query && <button type="button" className="intake-icon-button" aria-label="Clear Intake search" onClick={() => onQueryChange('')}><X size={16} /></button>}
            </label>
            <div className="intake-queue-filters" role="group" aria-label="Case queue filters">
              {filters.map(filter => <button type="button" key={filter.key} aria-pressed={activeFilter === filter.key} onClick={() => onFilterChange(filter.key)}>
                {filter.label}<span>{filter.count}</span>
              </button>)}
            </div>
            <label className="intake-status-filter"><span>Status</span><span className="intake-status-control"><select aria-label="Filter ESR intake queue" value={intakeStatus}
              onChange={event => onIntakeStatus(event.target.value as IntakeFilter)}>
              <option value="all">All statuses</option><option value="new">New</option>
              <option value="acknowledgement-pending">Acknowledgement pending</option>
              <option value="processing">Processing</option><option value="requires-attention">Requires attention</option>
            </select><ChevronDown size={15} aria-hidden="true" /></span></label>
          </div>
          {esrs.length ? <ul className="intake-queue-list intake-case-list" ref={queueList} aria-label="Intake cases">
            {esrs.map(esr => {
              const veteran = state.veteransById[esr.veteranId];
              const facts = state.intake?.viewsByEsrId[esr.id];
              const issueCount = (facts?.openExceptions ?? 0) + (facts?.escalatedExceptions ?? 0);
              const label = issueCount ? `${issueCount} ${issueCount === 1 ? 'exception' : 'exceptions'}` : facts?.queueLabel ?? formatStatus(esr.intakeStatus);
              const badge = issueCount ? label : facts?.canValidate ? 'Ready' : facts?.needsReadableEvidence ? 'Needs evidence' : label;
              return <li key={esr.id}><button type="button" className="intake-queue-row" aria-pressed={selectedId === esr.id} onClick={() => pickCase(esr.id)}>
                <strong>{veteran?.displayName ?? esr.id}</strong>
                <small title={practiceCaseLabel(esr) ?? esr.id}>{esr.practiceScenario?.exercise ?? practiceCaseLabel(esr) ?? esr.id}</small>
                <span className="intake-queue-status" title={label}><Status text={badge} tone={issueCount || facts?.needsReadableEvidence ? 'amber' : caseStateTone(label)} /></span>
              </button></li>;
            })}
          </ul> : <div className="intake-queue-empty" role="status"><p>No cases match these filters.</p>
            <button type="button" className="intake-text-action" onClick={() => { onQueryChange(''); onFilterChange('all'); onIntakeStatus('all'); }}>Reset filters</button>
          </div>}
        </div>
      </aside>

      {selected && view && caseItem ? <section className="intake-case-card" aria-labelledby="intake-case-title">
        <header className="intake-case-header">
          <span className="intake-case-avatar" aria-hidden="true">{caseItem.initials}</span>
          <div className="intake-case-identity">
            <span>{practiceCaseLabel(selected) ?? <>{selected.id} <span aria-hidden="true">·</span> {caseItem.claim}</>}</span>
            <h2 ref={caseHeading} tabIndex={-1} id="intake-case-title">{caseItem.name}</h2>
            <p>{caseItem.exam}</p>
          </div>
          <span className="intake-case-status"><Status text={view.queueLabel} tone={view.canValidate ? 'green' : caseStateTone(view.queueLabel)} /></span>
        </header>

        <div className="intake-progress-strip">
          <p aria-hidden="true">{steps[Math.min(view.steps, steps.length - 1)]}</p>
          <ol aria-label="Intake progress">{steps.map((step, index) => {
            const complete = index < view.steps;
            const current = index === view.steps;
            return <li key={step} className={complete ? 'is-complete' : current ? 'is-current' : ''} aria-current={current ? 'step' : undefined}>
              <span>{complete ? <Check size={13} aria-hidden="true" /> : index + 1}</span><b>{step}</b>
            </li>;
          })}</ol>
        </div>

        {/* Dedicated VA Examination Request Section for this particular patient */}
        <div className="va-instructions-panel" role="region" aria-label="VA Examination Request">
          <div className="va-instructions-header">
            <div className="va-instructions-title">
              <ShieldCheck size={18} className="va-instructions-icon" aria-hidden="true" />
              <h4>VA Examination Request</h4>
            </div>
            <div className="va-instructions-simulation-badge">
              {view.vaInstructions?.isSimulated ? (
                <span className="va-sim-badge simulated" title="Simulated test request · Not connected to live VA exchange">
                  SIMULATED REQUEST
                </span>
              ) : (
                <span className="va-sim-badge live" title="Official live VA examination directive">
                  OFFICIAL LIVE VA REQUEST
                </span>
              )}
            </div>
          </div>

          <div className="va-instructions-grid">
            <div className="va-grid-row">
              <span className="va-grid-label">{view.vaInstructions?.contentions && view.vaInstructions.contentions.length > 1 ? 'Contentions' : 'Contention'}</span>
              <span className="va-grid-value">
                {view.vaInstructions?.contentions && view.vaInstructions.contentions.length > 0
                  ? view.vaInstructions.contentions.map(c => c.title).join(', ')
                  : 'Not specified'}
              </span>
            </div>
            <div className="va-grid-row">
              <span className="va-grid-label">Service branch</span>
              <span className="va-grid-value">
                {view.vaInstructions?.serviceBranch || 'Not specified'}
              </span>
            </div>
            <div className="va-grid-row">
              <span className="va-grid-label">Priority</span>
              <span className="va-grid-value">
                {view.vaInstructions?.priorityHandling
                  ? view.vaInstructions.priorityHandling.charAt(0).toUpperCase() + view.vaInstructions.priorityHandling.slice(1).toLowerCase()
                  : 'Routine'}
              </span>
            </div>
            <div className="va-grid-row">
              <span className="va-grid-label">
                {(view.vaInstructions?.requestedDbqs && view.vaInstructions.requestedDbqs.length > 1) || (view.vaInstructions?.contentions && view.vaInstructions.contentions.some(c => c.dbqs && c.dbqs.length > 1)) ? 'Requested DBQs' : 'Requested DBQ'}
              </span>
              <span className="va-grid-value">
                {(() => {
                  const allDbqs = Array.from(new Set([
                    ...(view.vaInstructions?.requestedDbqs || []),
                    ...(view.vaInstructions?.contentions?.flatMap(c => c.dbqs || []) || [])
                  ])).filter(Boolean);
                  return allDbqs.length > 0 ? allDbqs.join(', ') : 'DBQ worksheet to be determined';
                })()}
              </span>
            </div>
          </div>

          {/* Additional instructions: Displayed ONLY when available */}
          {view.vaInstructions?.specialInstructions ? (
            <>
              <div className={`va-instructions-text-container ${expandedInstructions ? 'expanded' : 'collapsed'}`}>
                <div className="va-instructions-raw-text">
                  {view.vaInstructions.specialInstructions}
                </div>
              </div>
              <div className="va-instructions-footer">
                <span className="va-instructions-mode-label">
                  Original request · Read-only
                </span>
                <button
                  type="button"
                  className="va-instructions-toggle-btn"
                  onClick={() => setExpandedInstructions(!expandedInstructions)}
                >
                  {expandedInstructions ? 'Collapse instructions ⌃' : 'View full instructions ⌵'}
                </button>
              </div>
            </>
          ) : (
            <div className="va-instructions-footer">
              <span className="va-instructions-mode-label">
                Original request · Read-only · No additional instructions specified
              </span>
            </div>
          )}
        </div>

        <div className="intake-sections" role="group" aria-label="Case sections">
          {sections.map(({ key, label, icon: Icon }) => <button type="button" key={key} aria-pressed={section === key} onClick={() => setSection(key)}>
            <Icon size={16} aria-hidden="true" />{label}
            {key === 'documents' && <span>{documents.length}</span>}
            {key === 'exceptions' && view.openExceptions > 0 && <span className="intake-alert-count">{view.openExceptions}</span>}
          </button>)}
        </div>

        <div className="intake-case-content">
          <div hidden={section !== 'documents'}>
            {/* AI Document Organizer Dashboard */}
            <div className="intake-organizer-card">
              <div className="intake-organizer-header">
                <div className="intake-organizer-title">
                  <span className="intake-source-icon" style={{ background: '#e9f2ef', color: '#16584d' }}>
                    <Layers size={20} aria-hidden="true" />
                  </span>
                  <div>
                    <h4>AI Document Organizer</h4>
                    <p>Automated multi-format extraction, logical section splitting, and clinical categorization</p>
                  </div>
                </div>
                <Status 
                  text={
                    view.aiOrganizer?.corruptedCount || view.aiOrganizer?.mismatchCount
                      ? 'Review Attention'
                      : view.aiOrganizer?.unreadableCount
                        ? 'Unreadable Content'
                        : view.aiOrganizer?.processingComplete
                          ? 'Organized & Verified'
                          : 'Processing Evidence'
                  }
                  tone={
                    view.aiOrganizer?.corruptedCount || view.aiOrganizer?.mismatchCount
                      ? 'red'
                      : view.aiOrganizer?.unreadableCount
                        ? 'amber'
                        : view.aiOrganizer?.processingComplete
                          ? 'green'
                          : 'blue'
                  }
                />
              </div>

              <div className="intake-organizer-metrics">
                <div className="intake-organizer-stat">
                  <small>Clinical Evidence</small>
                  <b>
                    {organizer?.totalPhysicalMedicalFiles ?? medicalDocuments.length} physical {medicalDocuments.length === 1 ? 'file' : 'files'} · {medicalItems.length} logical reports ({organizer?.totalMedicalPages ?? medicalDocuments.reduce((sum, d) => sum + (d.pageCount || 0), 0)} pgs)
                  </b>
                </div>
                <div className="intake-organizer-stat">
                  <small>Request Metadata</small>
                  <b>{organizer?.totalMetadataDocs ?? metadataDocuments.length} administrative {metadataDocuments.length === 1 ? 'file' : 'files'}</b>
                </div>
                <div className="intake-organizer-stat">
                  <small>Readability Quality</small>
                  <b>{organizer?.qualityScore ?? view.qualityRate ?? 100}% readable</b>
                </div>
                <div className="intake-organizer-stat">
                  <small>File Integrity</small>
                  <span className={`stat-badge ${organizer?.corruptedCount ? 'alert' : 'good'}`}>
                    {organizer?.corruptedCount ? `⚠ ${organizer.corruptedCount} Corrupted` : '✓ Checksum Validated'}
                  </span>
                </div>
                <div className="intake-organizer-stat">
                  <small>Claimant Match</small>
                  <span className={`stat-badge ${organizer?.mismatchCount ? 'alert' : 'good'}`}>
                    {organizer?.mismatchCount ? `⚠ ${organizer.mismatchCount} Mismatch (Action Required)` : '✓ Claimant Aligned'}
                  </span>
                </div>
                <div className="intake-organizer-stat">
                  <small>Duplicate Check</small>
                  <span className={`stat-badge ${organizer?.duplicateCount ? 'warn' : 'good'}`}>
                    {organizer?.duplicateCount ? `⚠ ${organizer.duplicateCount} Duplicate(s) (Action Required)` : '✓ No Duplicates'}
                  </span>
                </div>
              </div>
            </div>

            {/* ESR Request Metadata (XML) — Preserved separately from medical evidence */}
            {(metadataDocuments.length > 0 || logicalMetadataDocs.length > 0) && (
              <div className="intake-metadata-box">
                <div className="intake-metadata-heading">
                  <h4><FileText size={16} /> ESR Request Metadata (Prime XML / Ingestion References)</h4>
                  <span>Administrative Only · Kept Distinct from Clinical Evidence</span>
                </div>
                <ul className="intake-metadata-list">
                  {(logicalMetadataDocs.length > 0 ? logicalMetadataDocs : metadataDocuments).map(doc => (
                    <li key={doc.id} className="intake-metadata-item">
                      <div className="intake-metadata-info">
                        <span className="intake-format-badge xml">{doc.sourceReference?.toLowerCase().endsWith('.xml') ? 'XML' : 'ADMIN'}</span>
                        <strong>{documentName(doc.title)}</strong>
                        <span style={{ color: '#64748b' }}>({doc.sourceReference})</span>
                      </div>
                      <Status text="Administrative Record" tone="slate" />
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className="intake-content-heading">
              <div>
                <h3>Medical Evidence Documents</h3>
                <p>
                  {organizer?.totalPhysicalMedicalFiles ?? medicalDocuments.length} physical {medicalDocuments.length === 1 ? 'file' : 'files'} · {medicalItems.length} categorized logical reports ({organizer?.totalMedicalPages ?? medicalDocuments.reduce((sum, d) => sum + (d.pageCount || 0), 0)} total pages)
                  {view.unreadablePages > 0 && ` · ${view.unreadablePages} unreadable pages`}
                </p>
              </div>
              <div className="intake-document-tools">
                {view.canAttach && <button type="button" className="intake-secondary" onClick={() => setImportTarget({ type: 'source', esrId: selected.id })}><Plus size={16} /> Add source</button>}
              </div>
            </div>

            {/* Categorized Document Folders */}
            {categoryFolders.length > 1 && (
              <div className="intake-category-folders" role="group" aria-label="Document category folders">
                {categoryFolders.map(folder => (
                  <button
                    key={folder.key}
                    type="button"
                    className="intake-folder-pill"
                    aria-pressed={selectedCategory === folder.key}
                    onClick={() => setSelectedCategory(folder.key)}
                  >
                    <Folder size={14} />
                    <span>{folder.label}</span>
                    <small>{folder.docCount} docs · {folder.pageCount} pgs</small>
                  </button>
                ))}
              </div>
            )}

            {filteredMedicalDocuments.length ? (
              <ul className="intake-sources">
                {filteredMedicalDocuments.map(item => (
                  <SourceRow
                    key={item.id}
                    document={item}
                    busy={processing}
                    isDuplicate={duplicateDocIds.has(item.id)}
                    onReclassify={(newCat) => { void run(updateDocumentCategory(item.id, newCat)); }}
                    onRetry={() => { void run(retryIntakeDocument(selected.id, 'parentDocumentId' in item ? (item as LogicalDocument).parentDocumentId : item.id)); }}
                  />
                ))}
              </ul>
            ) : (
              <div className="intake-empty-evidence-box">
                <FileText size={28} className="intake-empty-evidence-icon" />
                <div className="intake-empty-evidence-text">
                  <h4>No clinical medical records attached yet</h4>
                  <p>
                    {metadataDocuments.length > 0
                      ? 'The ESR Request XML has been recorded (claimant details & examination instructions verified). Clinical medical evidence (PDF/scanned images) must now be attached before triage handoff.'
                      : 'No clinical source documents match the selected category filter.'}
                  </p>
                </div>
                {view.canAttach && (
                  <button
                    type="button"
                    className="intake-primary"
                    onClick={() => setImportTarget({ type: 'source', esrId: selected.id })}
                  >
                    <Plus size={16} /> Attach Medical Evidence (PDF / Folder)
                  </button>
                )}
              </div>
            )}
            {view.canProcessLegacy && <button type="button" className="intake-text-action" disabled={processing}
              onClick={() => { void run(completeDocumentProcessing(selected.id)); }}><RefreshCw size={15} /> Update document status</button>}
            <DocumentReviewAssistant key={selected.id} esrId={selected.id} />
          </div>

          {section === 'exceptions' && <>
            <div className="intake-content-heading"><div><h3>Exceptions</h3><p>{view.openExceptions} open · {view.escalatedExceptions} escalated</p></div></div>
            {exceptions.length === 0 && <p className="intake-inline-empty">No exceptions recorded for this ESR.</p>}
            <div className="intake-issues">{exceptions.map(issue => {
              const note = reviewNotes[issue.id] ?? '';
              return <article key={issue.id} className="intake-issue">
                <header><h4>{issue.title}</h4><Status text={formatStatus(issue.status)} tone={issue.status === 'open' ? 'red' : issue.status === 'escalated' ? 'amber' : 'green'} /></header>
                <p>{formatWorkspaceCopy(issue.detail)}</p>
                {issue.sourceReference && <small>{issue.sourceReference}</small>}
                {issue.resolution && <p className="intake-disposition">{issue.resolution}</p>}
                {['open', 'escalated'].includes(issue.status) && <>
                  <label className="intake-field"><span>Review note</span><textarea rows={2} value={note} aria-label={`Review note for ${issue.title}`}
                    onChange={event => setReviewNotes(notes => ({ ...notes, [issue.id]: event.target.value }))} /></label>
                  <div className="intake-issue-actions">
                    {issue.status === 'open' && <button type="button" className="intake-secondary" disabled={processing || !note.trim()}
                      onClick={() => { void run(reviewIntakeException(issue.id, 'escalated', note)); }}>Escalate</button>}
                    {Boolean(issue.documentId || issue.kind === 'duplicate-document') && (
                      <button
                        type="button"
                        className="intake-secondary"
                        style={{ color: '#b91c1c', borderColor: '#fca5a5' }}
                        disabled={processing}
                        onClick={() => {
                          const exclusionNote = note.trim() || 'Duplicate file excluded from evidence by staff.';
                          void run(reviewIntakeException(issue.id, 'excluded', exclusionNote));
                        }}
                        title="Exclude this duplicate file from evidence"
                      >
                        <Trash2 size={16} /> Exclude Duplicate
                      </button>
                    )}
                    <button type="button" className="intake-secondary" disabled={processing}
                      onClick={() => {
                        const resolveNote = note.trim() || 'Staff verified and approved document to keep.';
                        void run(reviewIntakeException(issue.id, 'resolved', resolveNote));
                      }}
                      title="Keep this document and remove the duplicate/action flags"
                    >
                      <Check size={16} /> {issue.kind === 'duplicate-document' ? 'Resolve & Keep' : 'Resolve'}
                    </button>
                  </div>
                </>}
              </article>;
            })}</div>
          </>}

          {section === 'activity' && <>
            <div className="intake-content-heading"><div><h3>Recent activity</h3>
              <p>{view.receiptReady ? 'Receipt acknowledged' : 'Acknowledgement pending'}
                {view.receiptReady && selected.receiptReference && <> · {selected.receiptReference.replace(/^LOCAL-(?=(?:AEP|ESR)-)/i, '')}</>}
              </p></div>
              <button type="button" className="intake-secondary" onClick={onHistory}>Full history</button>
            </div>
            {timeline.length ? <ol className="intake-timeline">{timeline.map(event => <li key={event.id}><CheckCircle2 size={17} /><div>
              <b>{formatActivityLabel(event.action)}</b><time dateTime={event.at}>{dateLabel(event.at)}</time>
              {event.rationale && <p>{formatWorkspaceCopy(event.rationale)}</p>}
            </div></li>)}</ol> : <p className="intake-inline-empty">No activity recorded for this ESR.</p>}
            <details className="intake-subdetails"><summary><ChevronRight size={16} /> Case details</summary><CasePanel item={caseItem} stage="Intake" state={state} /></details>
          </>}
        </div>

        <footer className="intake-case-footer">
          <div className="intake-readiness" data-ready={view.canValidate} role="status">
            {view.canValidate ? <CheckCircle2 size={19} /> : <AlertCircle size={19} />}
            <span>{view.canValidate ? 'Ready for staff validation' : view.blocker && formatWorkspaceCopy(view.blocker)}</span>
          </div>
          {!view.receiptReady ? <div className="intake-receipt-action">
            <label className="intake-field"><span>Receipt reference</span><input value={receiptReference}
              placeholder="Automatic if left blank" onChange={event => setReceiptReferences(values => ({ ...values, [selectedId]: event.target.value }))} /></label>
            <button type="button" className="intake-primary" disabled={processing} onClick={() => { void run(recordIntakeReceipt(selected.id, receiptReference)); }}>Record acknowledgement</button>
          </div> : <button type="button" className="intake-primary" disabled={processing || !view.canValidate}
            onClick={() => { void run(validateIntake(selected.id), () => onContinue(selected.id)); }}>
            {processing ? 'Saving…' : 'Validate and send to Triage'}<ArrowRight size={17} />
          </button>}
        </footer>
      </section> : <section className="intake-case-empty"><Inbox size={28} /><h2>No case selected</h2><p>Choose a case from the queue or receive a new ESR.</p></section>}
    </div>

    {importTarget && <ImportDialog title={importTarget.type === 'esr' ? 'Receive a new ESR' : 'Add a source document'} onClose={() => setImportTarget(undefined)}>
      <IntakeImport esrId={importTarget.type === 'source' ? importTarget.esrId : undefined} onReceived={imported} />
    </ImportDialog>}
  </div>;
}
