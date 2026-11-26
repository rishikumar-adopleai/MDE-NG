import { HeadingText } from '../HeadingText';
import { useLayoutEffect, useRef, useState } from 'react';
import {
  Activity, AlertCircle, ArrowRight, Check, CheckCircle2, ChevronDown,
  ChevronRight, FileText, Inbox, Plus, RefreshCw, Search, Trash2, X,
} from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import type { EvidenceDocument, IntakeStatus, LogicalDocument } from '../domain/model';
import { CasePanel, Status } from './shared';
import { DocumentReviewAssistant } from './DocumentReviewAssistant';
import { StageHeading } from './StageHeading';
import { useQueueRows } from './useQueueRows';
import { IntakeBrief } from './IntakeBrief';
import { IntakeImport } from './IntakeImport';
import { EvidenceBrief, buildEvidenceBrief } from './EvidenceBrief';
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

function SourceRow({ document, parent, categories, onRetry, onReclassify, busy, isDuplicate }: {
  document: LogicalDocument | EvidenceDocument;
  parent?: EvidenceDocument;
  categories: {key: string; label: string}[];
  onRetry?: () => void;
  onReclassify?: (category: string) => void;
  busy: boolean;
  isDuplicate?: boolean;
}) {
  const isLogical = 'sectionId' in document;
  const source = isLogical ? parent : document;
  const result = source?.processing;
  const pending = source?.classificationStatus === 'queued' || source?.classificationStatus === 'processing';
  const indexed = !pending && document.classificationStatus === 'indexed';
  const review = document.classificationStatus === 'needs-human-review';
  const brief = buildEvidenceBrief(document, parent);
  const quality = source?.quality;
  const sections = isLogical ? undefined : source?.sections;
  const filename = isLogical ? document.parentFileName : document.sourceReference;
  const formatRaw = Array.from(filename.matchAll(/\.(pdf|xml|txt|png|jpe?g|tiff?|bmp|zip)(?=$|[\s(·])/gi)).at(-1)?.[1].toUpperCase() ?? 'FILE';
  const category = document.category === 'hospital' ? 'va-medical-records' : document.category;
  const categoryLabel = categories.find(item => item.key === category)?.label ?? formatCategory(category);
  const ocrApplied = isLogical ? document.ocrApplied : quality?.ocrApplied;
  const wrongPatient = isLogical ? document.wrongPatient : quality?.claimantMatch === 'mismatch';
  const readabilityScore = !pending && quality ? (isLogical ? document.readabilityScore : quality.readabilityScore) : undefined;
  const actionRequired = isLogical ? document.actionRequired : review;

  return <li className={`intake-source${actionRequired ? ' is-action-required' : ''}`}>
    <span className="intake-source-icon"><FileText size={19} aria-hidden="true" /></span>
    <div className="intake-source-content">
      <div className="intake-document-title">
        <span className="intake-file-type">{formatRaw}</span><b>{documentName(brief.title)}</b>
        <span className="intake-source-tag">{categoryLabel}</span>
        {ocrApplied && (
          <span className="intake-ocr-badge" title="Optical Character Recognition performed on scanned pages">
            OCR used
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
            <span>{document.pageCount ? (document.startPage === document.endPage ? `p. ${document.startPage}` : `pp. ${document.startPage}–${document.endPage}`) : 'Page count pending'}</span>
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

      <details className="intake-excerpts">
        <summary><ChevronRight size={15} aria-hidden="true" /> <HeadingText>Review summary</HeadingText></summary>
        <EvidenceBrief brief={brief} sourceName={filename} administrative={source?.category === 'va-request' && source.administrativeOnly} />
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

        {!document.administrativeOnly && onReclassify && <label className="intake-correct-category">Document category
          <select aria-label={`Category for ${document.title}`} value={category} disabled={busy || pending} onChange={event => onReclassify(event.target.value)}>
            {!categories.some(item => item.key === category) && <option value={category}>{categoryLabel}</option>}
            {categories.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}
          </select></label>}
        {result?.error && <p className="intake-source-error">{formatWorkspaceCopy(result.error)}</p>}
        {document.extractedData?.labPanels && document.extractedData.labPanels.length > 0 && (
          <details className="evidence-disclosure"><summary><ChevronRight size={15} aria-hidden="true" /><HeadingText>Laboratory results</HeadingText> ({document.extractedData.labPanels.length} {document.extractedData.labPanels.length === 1 ? 'panel' : 'panels'})</summary>
            <div className="intake-lab-findings evidence-reader" role="region" tabIndex={0} aria-label={`Laboratory results for ${brief.title}`}>
            {document.extractedData.labPanels.map((panel, idx) => (
              <div key={idx} className="intake-lab-panel">
                <div className="intake-lab-panel-title">
                  <span>{panel.panelName}</span>
                  <small>{panel.specimen && `${panel.specimen} · `}Collected: {panel.collectionDate || 'On file'}</small>
                </div>
                <table className="intake-lab-table">
                  <thead>
                    <tr>
                      <th><HeadingText>Test Name</HeadingText></th>
                      <th><HeadingText>Result</HeadingText></th>
                      <th><HeadingText>Units</HeadingText></th>
                      <th><HeadingText>Reference Range</HeadingText></th>
                      <th><HeadingText>Status</HeadingText></th>
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
          </details>
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
  const importOpener = useRef('intake-new-request');
  const [mobileQueueOpen, setMobileQueueOpen] = useState(false);
  const [reviewNotes, setReviewNotes] = useState<Record<string, string>>({});
  const [importTarget, setImportTarget] = useState<{ type: 'esr' } | { type: 'source'; esrId: string }>();
  const [processing, setProcessing] = useState(false);
  const [receiptReferences, setReceiptReferences] = useState<Record<string, string>>({});
  const caseHeading = useRef<HTMLHeadingElement>(null);
  const queueList = useQueueRows(selectedId, mobileQueueOpen, `${intakeStatus}:${activeFilter}:${query}`);

  useLayoutEffect(() => {
    setSection('documents');
    setSelectedCategory('all');
  }, [selectedId]);

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
  const activeDocuments = documents.filter(doc => doc.classificationStatus !== 'excluded');
  const isMetadata = (doc: EvidenceDocument) => doc.administrativeOnly || ['va-request', 'examination-requests'].includes(doc.category);
  const medicalDocuments = activeDocuments.filter(doc => !isMetadata(doc));
  const metadataDocuments = activeDocuments.filter(isMetadata);

  const organizer = view?.aiOrganizer;
  const logicalMetadataDocs = organizer?.logicalMetadataDocuments ?? [];

  // When logical document segmentation is available, make it the primary organization unit
  const medicalItems: (LogicalDocument | EvidenceDocument)[] = organizer?.logicalDocuments ?? medicalDocuments;

  const duplicateDocIds = new Set(organizer?.duplicateDocumentIds ?? []);

  const categories = organizer?.categories ?? Array.from(new Set(medicalDocuments.map(doc => doc.category))).map(key => ({key, label: formatCategory(key)}));
  const categoryOf = (doc: LogicalDocument | EvidenceDocument) => doc.category === 'hospital' ? 'va-medical-records' : doc.category;
  const categoryFolders = Array.from(new Set(medicalItems.map(categoryOf))).map(key => ({key,
    label: categories.find(item => item.key === key)?.label ?? formatCategory(key), count: medicalItems.filter(item => categoryOf(item) === key).length,
  }));
  const currentCategory = categoryFolders.some(item => item.key === selectedCategory) ? selectedCategory : 'all';
  const filteredMedicalDocuments = medicalItems.filter(item => currentCategory === 'all' || currentCategory === categoryOf(item));
  const openImport = (opener: HTMLButtonElement, target: NonNullable<typeof importTarget>) => {
    importOpener.current = opener.id;
    setImportTarget(target);
  };
  function closeImport() {
    setImportTarget(undefined);
    requestAnimationFrame(() => {
      (window.document.getElementById(importOpener.current) ?? caseHeading.current)?.focus();
    });
  }

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
    onSelect(id);
    if (mobileQueueOpen) {
      setMobileQueueOpen(false);
      requestAnimationFrame(() => caseHeading.current?.focus());
    }
  }

  function imported(id: string) {
    setImportTarget(undefined);
    setSelectedCategory('all');
    requestAnimationFrame(() => caseHeading.current?.focus());
    if (importTarget?.type === 'source') {
      notify('Source document queued.');
    } else {
      setSection('documents');
      setMobileQueueOpen(false);
      onReceived(id);
    }
  }

  return <div className={importTarget ? 'intake-page' : 'intake-page queue-workbench'}>
    <StageHeading stage="Intake" description="Receive exam requests and review source documents before triage.">
      {!importTarget && <button type="button" id="intake-new-request" className="intake-primary intake-new-esr" onClick={event => openImport(event.currentTarget, { type: 'esr' })}><Plus size={19} /> New ESR</button>}
    </StageHeading>

    {importTarget ? <IntakeImport esrId={importTarget.type === 'source' ? importTarget.esrId : undefined} onReceived={imported} onCancel={closeImport} /> : <div className={`intake-board${!selected ? ' intake-board-empty' : ''}`}>
      <aside className="intake-queue-card" aria-label="Intake queue">
        <button type="button" className="intake-queue-toggle" aria-expanded={mobileQueueOpen || !selected}
          aria-controls="intake-queue-controls" disabled={!selected} onClick={() => setMobileQueueOpen(value => !value)}>
          <Inbox size={18} /><span><HeadingText>Intake queue</HeadingText></span><small>{esrs.length} {esrs.length === 1 ? 'case' : 'cases'}</small><ChevronDown size={17} />
        </button>
        <div id="intake-queue-controls" className={`intake-queue-controls${mobileQueueOpen || !selected ? ' is-open' : ''}`}>
          <header className="intake-queue-heading"><h2><HeadingText>Intake queue</HeadingText></h2><span>{esrs.length} {esrs.length === 1 ? 'case' : 'cases'}</span></header>
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
          </ul> : <div className="intake-queue-empty" role="status"><p>{query || activeFilter !== 'all' || intakeStatus !== 'all' ? 'No cases match these filters.' : 'No requests in Intake yet.'}</p>
            <button type="button" hidden={!query && activeFilter === 'all' && intakeStatus === 'all'} className="intake-text-action" onClick={() => { onQueryChange(''); onFilterChange('all'); onIntakeStatus('all'); }}>Reset filters</button>
          </div>}
        </div>
      </aside>

      {selected && view && caseItem ? <div className="intake-case-card" key={selected.id}>
        <section className="queue-detail" aria-labelledby="intake-case-title" tabIndex={0}>
        <header className="intake-case-header">
          <span className="intake-case-avatar" aria-hidden="true">{caseItem.initials}</span>
          <div className="intake-case-identity">
            <span>{practiceCaseLabel(selected) ?? <>{selected.id} <span aria-hidden="true">·</span> {caseItem.claim}</>}</span>
            <h2 ref={caseHeading} tabIndex={-1} id="intake-case-title">{caseItem.name}</h2>
            <p>{caseItem.exam}</p>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span className="intake-case-status"><Status text={view.queueLabel} tone={view.canValidate ? 'green' : caseStateTone(view.queueLabel)} /></span>
            <button
              type="button"
              className="intake-icon-button"
              aria-label="Close case details"
              title="Close case"
              onClick={() => onSelect('')}
            >
              <X size={18} />
            </button>
          </div>
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

        <IntakeBrief view={view} onExceptions={() => setSection('exceptions')} />

        <div className="intake-sections" role="group" aria-label="Case sections">
          {sections.map(({ key, label, icon: Icon }) => <button type="button" key={key} aria-pressed={section === key} onClick={() => setSection(key)}>
            <Icon size={16} aria-hidden="true" />{label}
            {key === 'documents' && <span>{documents.length}</span>}
            {key === 'exceptions' && view.openExceptions + view.escalatedExceptions > 0 && <span className="intake-alert-count">{view.openExceptions + view.escalatedExceptions}</span>}
          </button>)}
        </div>

        <div className="intake-case-content">
          <div hidden={section !== 'documents'}>
            <div className="intake-content-heading">
              <div>
                <h3><HeadingText>Evidence library</HeadingText></h3>
                <p>
                  {medicalItems.length} {medicalItems.length === 1 ? 'report' : 'reports'} · {medicalItems.reduce((sum, item) => sum + item.pageCount, 0)} pages
                  {view.unreadablePages > 0 && ` · ${view.unreadablePages} unreadable pages`}
                </p>
              </div>
              <div className="intake-document-tools">
                {view.canAttach && <button type="button" id="intake-add-source" className="intake-secondary" onClick={event => openImport(event.currentTarget, { type: 'source', esrId: selected.id })}><Plus size={16} /> Add source</button>}
              </div>
            </div>

            {categoryFolders.length > 0 && <label className="intake-library-filter">Category
              <select value={currentCategory} onChange={event => setSelectedCategory(event.target.value)}>
                <option value="all">All evidence ({medicalItems.length})</option>
                {categoryFolders.map(item => <option key={item.key} value={item.key}>{item.label} ({item.count})</option>)}
              </select></label>}

            {filteredMedicalDocuments.length ? (
              <ul className="intake-sources review-document-list" aria-label="Evidence library documents">
                {filteredMedicalDocuments.map(item => (
                  <SourceRow
                    key={item.id}
                    document={item} categories={categories} parent={'parentDocumentId' in item ? state.evidenceDocumentsById[item.parentDocumentId] : undefined}
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
                  <h4><HeadingText>No supporting evidence yet</HeadingText></h4>
                  <p>
                    {metadataDocuments.length > 0
                      ? 'Request metadata is on file. Attach supporting records before handoff.'
                      : 'Attach a source document to start evidence review.'}
                  </p>
                </div>
                {view.canAttach && (
                  <button
                    type="button"
                    className="intake-primary"
                    id="intake-attach-first-source"
                    onClick={event => openImport(event.currentTarget, { type: 'source', esrId: selected.id })}
                  >
                    <Plus size={16} /> Attach documents
                  </button>
                )}
              </div>
            )}
            {(logicalMetadataDocs.length > 0 || metadataDocuments.length > 0) && <details className="intake-subdetails intake-metadata-details">
              <summary><ChevronRight size={16} /><HeadingText>Request metadata</HeadingText> ({logicalMetadataDocs.length || metadataDocuments.length})</summary>
              <ul className="intake-sources review-document-list" aria-label="Request metadata documents">{(logicalMetadataDocs.length ? logicalMetadataDocs : metadataDocuments).map(doc => <SourceRow key={doc.id} document={doc} parent={'parentDocumentId' in doc ? state.evidenceDocumentsById[doc.parentDocumentId] : undefined} categories={categories} busy={processing} />)}</ul>
            </details>}
            {view.canProcessLegacy && <button type="button" className="intake-text-action" disabled={processing}
              onClick={() => { void run(completeDocumentProcessing(selected.id)); }}><RefreshCw size={15} /> Update document status</button>}
            <DocumentReviewAssistant esrId={selected.id} />
          </div>

          {section === 'exceptions' && <>
            <div className="intake-content-heading"><div><h3><HeadingText>Exceptions</HeadingText></h3><p>{view.openExceptions} open · {view.escalatedExceptions} escalated</p></div></div>
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
                    {issue.documentId && (
                      <button
                        type="button"
                        className="intake-secondary"
                        style={{ color: '#b91c1c', borderColor: '#fca5a5' }}
                        disabled={processing}
                        onClick={() => {
                          const exclusionNote = note.trim() || 'Source file excluded from evidence by staff.';
                          void run(reviewIntakeException(issue.id, 'excluded', exclusionNote));
                        }}
                        title="Exclude the entire source file from this case’s evidence"
                      >
                        <Trash2 size={16} /> Exclude source file
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
            <div className="intake-content-heading"><div><h3><HeadingText>Recent activity</HeadingText></h3>
              <p>{view.receiptReady ? 'Receipt acknowledged' : 'Acknowledgement pending'}
                {view.receiptReady && selected.receiptReference && <> · {selected.receiptReference.replace(/^LOCAL-(?=(?:AEP|ESR)-)/i, '')}</>}
              </p></div>
              <button type="button" className="intake-secondary" onClick={onHistory}>Full history</button>
            </div>
            {timeline.length ? <ol className="intake-timeline">{timeline.map(event => <li key={event.id}><CheckCircle2 size={17} /><div>
              <b>{formatActivityLabel(event.action)}</b><time dateTime={event.at}>{dateLabel(event.at)}</time>
              {event.rationale && <p>{formatWorkspaceCopy(event.rationale)}</p>}
            </div></li>)}</ol> : <p className="intake-inline-empty">No activity recorded for this ESR.</p>}
            <details className="intake-subdetails"><summary><ChevronRight size={16} /> <HeadingText>Case details</HeadingText></summary><CasePanel item={caseItem} stage="Intake" state={state} /></details>
          </>}
        </div>

        </section>
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
      </div> : <section className="intake-case-empty"><Inbox size={28} /><h2><HeadingText>No case selected</HeadingText></h2><p>Choose a case from the queue or receive a new ESR.</p></section>}
    </div>}
  </div>;
}
