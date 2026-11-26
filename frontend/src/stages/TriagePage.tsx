import { HeadingText } from '../HeadingText';
import { useEffect, useRef, useState } from 'react';
import { useQueueRows } from './useQueueRows';
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  ClipboardList,
  FileSearch,
  FileText,
  Inbox,
  Loader2,
  MessageSquare,
  RefreshCw,
  Search,
  ShieldAlert,
  Sparkles,
  Stethoscope,
  X,
} from 'lucide-react';
import { useWorkspace } from '../context/WorkspaceContext';
import type {
  Contention,
  EvidenceDocument,
  Modality,
  TriageCaseContention,
  TriageCaseView,
  TriagePlan,
  TriageRowView,
  TriageWorkspaceView,
} from '../domain/model';
import { EvidenceBrief, buildEvidenceBrief } from './EvidenceBrief';
import { StageHeading } from './StageHeading';
import { resultEntityId } from './StageWorkbench';
import { documentName, formatCategory, practiceCaseLabel } from './presentation';
import { Status } from './shared';

type QueueFilter = 'all' | 'exception' | 'review' | 'ready';
type ExamChoice = TriageWorkspaceView['examChoices'][number];
type SourceTarget = {
  documentId?: string;
  documentTitle: string;
  pageNumber?: number;
  citationLabel: string;
  fallbackExcerpt?: string;
};

type GroupedCitation = {
  documentId?: string;
  documentTitle: string;
  pages: number[];
  label: string;
  firstExcerpt?: string;
};

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

const pathwayLabels: Record<Modality, string> = {
  'in-person': 'In-person examination',
  'tele-c-and-p': 'Telehealth C&P',
  'ace-records-review': 'ACE records review',
};

function parseCitationLabel(label: string): { title: string; page?: number } {
  const match = label.match(/^(.*?)\s*[·-]\s*p+\.?\s*(\d+)/i);
  if (match) {
    return { title: match[1].trim(), page: Number(match[2]) };
  }
  return { title: label.trim() };
}

function formatPageRange(pages: number[]): string {
  const sorted = [...new Set(pages)].sort((a, b) => a - b);
  if (!sorted.length) return '';
  if (sorted.length === 1) return `p. ${sorted[0]}`;
  const ranges: string[] = [];
  let start = sorted[0];
  let end = sorted[0];
  for (let i = 1; i < sorted.length; i += 1) {
    if (sorted[i] === end + 1) {
      end = sorted[i];
    } else {
      ranges.push(start === end ? `${start}` : `${start}–${end}`);
      start = sorted[i];
      end = sorted[i];
    }
  }
  ranges.push(start === end ? `${start}` : `${start}–${end}`);
  return `pp. ${ranges.join(', ')}`;
}

function groupCitations(
  citations: string[],
  matchedEvidence: { documentId?: string; documentTitle: string; page: number; excerpt?: string }[] = [],
): GroupedCitation[] {
  const byTitle = new Map<string, GroupedCitation>();
  for (const cite of citations) {
    const parsed = parseCitationLabel(cite);
    const match = matchedEvidence.find(m => `${m.documentTitle} · p. ${m.page}` === cite || m.documentTitle === parsed.title);
    const title = match?.documentTitle || parsed.title;
    const entry = byTitle.get(title) ?? {
      documentId: match?.documentId,
      documentTitle: title,
      pages: [],
      label: title,
      firstExcerpt: match?.excerpt,
    };
    if (parsed.page && !entry.pages.includes(parsed.page)) {
      entry.pages.push(parsed.page);
    }
    byTitle.set(title, entry);
  }
  for (const item of byTitle.values()) {
    const rangeStr = formatPageRange(item.pages);
    item.label = rangeStr ? `${documentName(item.documentTitle)} · ${rangeStr}` : documentName(item.documentTitle);
  }
  return [...byTitle.values()];
}

function SourceDrawer({
  target,
  documents,
  onClose,
}: {
  target: SourceTarget;
  documents: EvidenceDocument[];
  onClose: () => void;
}) {
  const closeBtnRef = useRef<HTMLButtonElement>(null);
  const parsed = parseCitationLabel(target.citationLabel);
  const matchedDoc =
    documents.find(d => d.id === target.documentId) ??
    documents.find(
      d =>
        d.title.toLowerCase().includes(parsed.title.toLowerCase()) ||
        d.sourceReference.toLowerCase().includes(parsed.title.toLowerCase()),
    );

  const processingAny = matchedDoc?.processing as
    | (NonNullable<EvidenceDocument['processing']> & { allPages?: { page: number; text: string }[] })
    | undefined;
  const pages = processingAny?.allPages?.length
    ? processingAny.allPages
    : processingAny?.sources?.length
      ? processingAny.sources
      : matchedDoc?.syntheticExcerpts?.map(e => ({ page: e.pageNumber, text: e.text })) ?? [];

  const [activePage, setActivePage] = useState<number>(target.pageNumber ?? parsed.page ?? pages[0]?.page ?? 1);

  useEffect(() => {
    setActivePage(target.pageNumber ?? parsed.page ?? pages[0]?.page ?? 1);
  }, [target.citationLabel, target.pageNumber, parsed.page]);

  useEffect(() => {
    closeBtnRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const currentPageEntry = pages.find(p => p.page === activePage) ?? pages[0];
  const displayText = currentPageEntry?.text || target.fallbackExcerpt || 'Extracted page text is unavailable for this citation.';

  return (
    <div className="triage-drawer-backdrop" role="presentation" onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <aside className="triage-source-drawer" role="dialog" aria-modal="true" aria-label="Source clinical document viewer">
        <header className="triage-drawer-header">
          <div>
            <span className="intake-kicker"><FileSearch size={14} /> <HeadingText>Original Source Record</HeadingText></span>
            <h3>{documentName(matchedDoc?.title || target.documentTitle)}</h3>
            <small>
              {matchedDoc ? `${formatCategory(matchedDoc.category)} · ${matchedDoc.pageCount} page(s)` : target.citationLabel}
            </small>
          </div>
          <button
            ref={closeBtnRef}
            type="button"
            className="intake-icon-button"
            aria-label="Close source drawer"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </header>

        {pages.length > 1 && (
          <div className="triage-drawer-pages" role="navigation" aria-label="Document pages">
            <span>Pages:</span>
            {pages.map(p => (
              <button
                key={p.page}
                type="button"
                className={`triage-page-chip${p.page === (currentPageEntry?.page ?? activePage) ? ' is-active' : ''}`}
                onClick={() => setActivePage(p.page)}
              >
                p. {p.page}
              </button>
            ))}
          </div>
        )}

        <div className="triage-drawer-body">
          <div className="triage-drawer-page-banner">
            <strong>Page {currentPageEntry?.page ?? activePage}</strong>
            <span>{matchedDoc?.sourceReference || target.citationLabel}</span>
          </div>
          <pre className="triage-drawer-text">{displayText}</pre>
        </div>
      </aside>
    </div>
  );
}

function ContentionPlanningCard({
  entry,
  item,
  choices,
  documents,
  isBvaRemand,
  onOpenCitation,
  onApproved,
  onScheduling,
}: {
  entry: TriageCaseContention;
  item: Contention;
  choices: ExamChoice[];
  documents: EvidenceDocument[];
  isBvaRemand: boolean;
  onOpenCitation: (target: SourceTarget) => void;
  onApproved: (esrId: string, title: string, entityId?: string) => void;
  onScheduling?: (esrId: string, entityId?: string) => void;
}) {
  const { saveTriageDraft, approveTriagePlan, requestTriageClarification, resumeTriageReview, authorizeTriageAce } = useWorkspace();
  const row: TriageRowView | undefined | null = entry.row;
  const dossier = entry.dossier ?? row?.dossier;
  const approvedReq = entry.approvedRequirement;
  const isApproved = entry.status === 'approved-for-exam';

  const [plan, setPlan] = useState<TriagePlan>(() =>
    row?.initialPlan ?? {
      examCode: dossier?.examSuggestion?.examCode || '',
      modality: '',
      dbqs: dossier?.examSuggestion?.suggestedDbqs || [],
      diagnostics: [],
      rationale: '',
    },
  );
  const [expandedEvidence, setExpandedEvidence] = useState(false);
  const [editOpen, setEditOpen] = useState(!isApproved && !approvedReq);
  const [clarificationOpen, setClarificationOpen] = useState(false);
  const [aceAuthOpen, setAceAuthOpen] = useState(false);
  const [aceNote, setAceNote] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [feedback, setFeedback] = useState('');

  useEffect(() => {
    if (row?.initialPlan) {
      setPlan(row.initialPlan);
    }
  }, [entry.contentionId, row?.initialPlan.examCode, row?.aceAuthorized]);

  const choice = choices.find(exam => exam.code === plan.examCode);
  const rulesEval = dossier?.rulesEval;
  const gaps = rulesEval?.evidenceGaps ?? [];
  const metrics = dossier?.extractedMetrics ?? [];
  const codes = dossier?.medicalCodes ?? [];
  const matchedEvidence = dossier?.matchedEvidence ?? [];
  const groupedCites = groupCitations(dossier?.citations ?? [], matchedEvidence);
  const previewCites = groupedCites.slice(0, 2);
  const hasMoreEvidence = matchedEvidence.length > 0 || groupedCites.length > 2 || documents.length > 0;

  const vaDbqs = dossier?.examSuggestion?.vaRequestedDbqs ?? entry.requestedDbqs ?? [];
  const vaSpecifiedPathway = (rulesEval?.vaSpecifiedPathway || '') as Modality | '';
  const isRuleUnverified = rulesEval?.ruleVerificationStatus === 'unverified-requires-review' || dossier?.examSuggestion?.ruleVerificationStatus === 'unverified-requires-review';
  const permittedForExam = (row?.pathwaysByExam[plan.examCode] ?? []).filter(
    mod => !vaSpecifiedPathway || mod === vaSpecifiedPathway,
  );
  const canSelectAce = Boolean(choice && permittedForExam.includes('ace-records-review'));
  const choiceAllowsAceInRules = Boolean(choice?.modalities?.includes('ace-records-review') && rulesEval?.aceEligible !== false);
  const selectedModalityReason = plan.modality ? rulesEval?.modalityEligibilityReasons?.[plan.modality] : '';
  const canApprove = Boolean(
    choice &&
      row?.readableEvidence &&
      plan.modality &&
      permittedForExam.includes(plan.modality as Modality) &&
      plan.dbqs.length > 0 &&
      (plan.examCode === 'dynamic' || !choice.primaryDbq || plan.dbqs.includes(choice.primaryDbq)) &&
      !isRuleUnverified &&
      plan.rationale.trim(),
  );

  const changeValues = (field: 'dbqs' | 'diagnostics', value: string, selected: boolean) => {
    setPlan(current => ({
      ...current,
      [field]: selected ? [...current[field], value] : current[field].filter(v => v !== value),
    }));
  };

  const runAction = async (action: 'draft' | 'approve' | 'clarification' | 'resume' | 'authorizeAce') => {
    setBusy(true);
    setError('');
    setFeedback('');
    try {
      const id = entry.contentionId;
      const result = await (action === 'draft'
        ? saveTriageDraft(id, plan)
        : action === 'approve'
          ? approveTriagePlan(id, plan)
          : action === 'clarification'
            ? requestTriageClarification(id, note)
            : action === 'resume'
              ? resumeTriageReview(id, note)
              : authorizeTriageAce(id, aceNote));
      if (!result.ok) {
        setError(result.message);
        return;
      }
      if (action === 'approve') {
        onApproved(item.esrId, item.title, resultEntityId(result.value));
      }
      if (action === 'clarification' || action === 'resume') {
        setClarificationOpen(false);
        setNote('');
      }
      if (action === 'authorizeAce') {
        setAceAuthOpen(false);
        setAceNote('');
      }
      setFeedback(result.message);
    } finally {
      setBusy(false);
    }
  };

  const cardStatusLabel = isApproved
    ? 'Plan Approved'
    : entry.status === 'clarification-required'
      ? 'Clarification Required'
      : entry.status === 'in-review'
        ? 'In Review'
        : 'Pending Review';
  const cardStatusTone = isApproved ? 'green' : entry.status === 'clarification-required' ? 'red' : 'amber';

  return (
    <article className={`triage-card${isApproved ? ' is-approved' : ''}`} aria-labelledby={`contention-h-${entry.contentionId}`}>
      <header className="triage-card-header">
        <div className="triage-card-title-group">
          <span className="triage-card-number">{String(entry.index).padStart(2, '0')}</span>
          <div>
            <h3 id={`contention-h-${entry.contentionId}`}>{entry.title}</h3>
            <div className="triage-card-tags">
              {dossier?.examSuggestion?.specialty && <span className="triage-tag-specialty">{dossier.examSuggestion.specialty}</span>}
              {vaDbqs.length > 0 ? (
                vaDbqs.map(dbq => (
                  <span key={dbq} className="triage-tag-dbq">VA-requested DBQ: {dbq}</span>
                ))
              ) : (
                <span className="triage-tag-dbq suggested">
                  Suggested DBQ: {dossier?.examSuggestion?.suggestedDbqs?.[0] || 'Needs Clinician Selection'}
                </span>
              )}
              {isRuleUnverified && (
                <span className="triage-unverified-badge">Unverified Rule — Requires Authorized Review</span>
              )}
              {codes.slice(0, 4).map(c => (
                <span key={`${c.system}:${c.code}`} className="triage-tag-code" title={c.display}>
                  {c.system}: {c.code}
                </span>
              ))}
            </div>
          </div>
        </div>
        <Status text={cardStatusLabel} tone={cardStatusTone} />
      </header>

      {/* Compact Evidence Preview + View More */}
      <div className="triage-card-evidence-box">
        <div className="triage-box-label">
          <Stethoscope size={14} /> <HeadingText>Relevant medical evidence</HeadingText> ({matchedEvidence.length} matched page{matchedEvidence.length === 1 ? '' : 's'})
          {dossier && dossier.negatedCount > 0 && (
            <span className="triage-negation-note">{dossier.negatedCount} negated/denied statement(s) separated</span>
          )}
        </div>
        <p className="triage-card-summary">{dossier?.aiSummary || 'No clinical summary extracted yet.'}</p>

        {previewCites.length > 0 && (
          <div className="triage-citation-row" aria-label="Key evidence citations">
            {previewCites.map(g => (
              <button
                key={g.documentTitle}
                type="button"
                className="triage-citation-chip"
                onClick={() =>
                  onOpenCitation({
                    documentId: g.documentId,
                    documentTitle: g.documentTitle,
                    pageNumber: g.pages[0],
                    citationLabel: g.label,
                    fallbackExcerpt: g.firstExcerpt,
                  })
                }
              >
                <FileText size={13} /> {g.label}
              </button>
            ))}
          </div>
        )}

        {metrics.length > 0 && (
          <div className="triage-metrics-grid" aria-label="Extracted clinical measurements">
            {metrics.map((metric, idx) => (
              <div key={idx} className="triage-metric-tile">
                <strong>{metric.value}</strong>
                <span>{metric.label}</span>
                <small>{metric.date ? `${metric.date} · ` : ''}{metric.source}</small>
              </div>
            ))}
          </div>
        )}

        {hasMoreEvidence && (
          <div className="triage-transparent-evidence">
            <button
              type="button"
              className="intake-text-action"
              aria-expanded={expandedEvidence}
              onClick={() => setExpandedEvidence(v => !v)}
            >
              <ChevronDown size={15} style={{ transform: expandedEvidence ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }} />
              {expandedEvidence ? 'Show less' : `View more (${matchedEvidence.length} excerpt(s) · ${documents.length} document(s))`}
            </button>

            {expandedEvidence && (
              <div className="triage-expanded-evidence">
                {matchedEvidence.length > 0 && (
                  <div className="triage-matched-excerpts-list">
                    <h4><HeadingText>All matched clinical excerpts</HeadingText> ({matchedEvidence.length})</h4>
                    {matchedEvidence.map((mp, idx) => (
                      <div key={`${mp.documentId}-${mp.page}-${idx}`} className="triage-excerpt-item">
                        <div className="triage-excerpt-header">
                          <button
                            type="button"
                            className="triage-citation-chip"
                            onClick={() =>
                              onOpenCitation({
                                documentId: mp.documentId,
                                documentTitle: mp.documentTitle,
                                pageNumber: mp.page,
                                citationLabel: `${mp.documentTitle} · p. ${mp.page}`,
                                fallbackExcerpt: mp.excerpt,
                              })
                            }
                          >
                            <FileText size={13} /> {documentName(mp.documentTitle)} · Page {mp.page}
                          </button>
                          {mp.clinicalDate && <span className="triage-excerpt-date">{mp.clinicalDate}</span>}
                          <span className="triage-matched-terms">Matched: {mp.matchedTerms.slice(0, 5).join(', ')}</span>
                        </div>
                        <p>{mp.excerpt}</p>
                      </div>
                    ))}
                  </div>
                )}

                {documents.length > 0 && (
                  <div className="triage-linked-docs-list">
                    <h4><HeadingText>Linked source documents</HeadingText> ({documents.length})</h4>
                    {documents.map(doc => {
                      const brief = buildEvidenceBrief(doc, undefined, entry.contentionId);
                      return (
                        <details className="triage-evidence" key={doc.id}>
                          <summary>
                            <span className="triage-document-icon"><FileText size={17} /></span>
                            <span className="triage-document-name">
                              <strong>{documentName(brief.title)}</strong>
                              <small>{formatCategory(doc.category)} · {doc.classifiedPageCount} of {doc.pageCount} pages readable</small>
                            </span>
                            <ChevronDown className="triage-chevron" size={16} aria-hidden="true" />
                          </summary>
                          <div className="triage-evidence-content">
                            <EvidenceBrief brief={brief} sourceName={doc.sourceReference} administrative={doc.administrativeOnly} />
                          </div>
                        </details>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Stale Plan Invalidation Banner when evidence was revised */}
      {!isApproved && row?.triageStaleReason && (
        <div className="triage-stale-banner" role="alert">
          <AlertTriangle size={15} />
          <div>
            <strong><HeadingText>Previous Plan Invalidated by Revised Evidence</HeadingText></strong>
            <span>{row.triageStaleReason}</span>
          </div>
        </div>
      )}

      {/* Separated Evidence Gaps: Blocking Issues, Clinical Review Requirements, Information to Collect During Examination */}
      {gaps.length > 0 && (() => {
        const groups: { key: 'blocking-issue' | 'clinical-review-requirement' | 'collect-during-exam'; title: string; items: typeof gaps }[] = [
          {
            key: 'blocking-issue',
            title: 'Blocking Issues',
            items: gaps.filter(g => g.gapGroup === 'blocking-issue' || (!g.gapGroup && g.severity === 'critical')),
          },
          {
            key: 'clinical-review-requirement',
            title: 'Clinical Review Requirements',
            items: gaps.filter(
              g =>
                g.gapGroup === 'clinical-review-requirement' ||
                (!g.gapGroup && ['additional-dbq-consideration', 'contradiction', 'abnormal-lab', 'unverified-rule'].includes(g.category)),
            ),
          },
          {
            key: 'collect-during-exam',
            title: 'Information to Collect During Examination',
            items: gaps.filter(
              g =>
                g.gapGroup === 'collect-during-exam' ||
                (!g.gapGroup &&
                  g.severity !== 'critical' &&
                  !['additional-dbq-consideration', 'contradiction', 'abnormal-lab', 'unverified-rule'].includes(g.category)),
            ),
          },
        ];
        return (
          <div className="triage-gaps-box" role="region" aria-label="Evidence gaps and clinical suggestions">
            {groups
              .filter(group => group.items.length > 0)
              .map(group => (
                <div key={group.key} className="triage-gap-group">
                  <div className="triage-gap-group-label"><HeadingText>{group.title}</HeadingText></div>
                  {group.items.map(gap => {
                    const badgeLabel =
                      gap.category === 'additional-dbq-consideration'
                        ? 'Additional DBQ Suggestion'
                        : gap.category === 'contradiction'
                          ? 'Clinical Contradiction'
                          : gap.category === 'abnormal-lab'
                            ? 'Abnormal Lab Finding'
                            : gap.category === 'unverified-rule'
                              ? 'Unverified Rule'
                              : group.key === 'blocking-issue'
                                ? 'Blocking Issue'
                                : group.key === 'collect-during-exam'
                                  ? 'Collect During Exam'
                                  : 'Clinical Review';
                    return (
                      <div key={gap.id} className={`triage-gap-row severity-${gap.severity}`}>
                        <AlertTriangle size={15} />
                        <div className="triage-gap-content">
                          <div className="triage-gap-headline">
                            <span className="triage-gap-badge"><HeadingText>{badgeLabel}</HeadingText></span>
                            <strong>{gap.message}</strong>
                          </div>
                          <span>{gap.recommendedAction}</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ))}
          </div>
        );
      })()}

      {/* VA-Specified Pathway Banner ONLY if VA explicitly mandated one */}
      {!isApproved && vaSpecifiedPathway && (
        <div className="triage-pathway-proposal">
          <div className="triage-pathway-main">
            <div>
              <span className="triage-pathway-kicker"><HeadingText>VA-Specified Pathway</HeadingText></span>
              <h4><HeadingText>{pathwayLabels[vaSpecifiedPathway]}</HeadingText></h4>
              <p>Specified in the VA Examination Request instructions.</p>
            </div>
          </div>
        </div>
      )}

      {/* Approved Summary State OR Editable Plan Controls */}
      {isApproved && approvedReq ? (
        <div className="triage-approved-banner">
          <div className="triage-approved-info">
            <CheckCircle2 size={18} />
            <div>
              <strong>
                Approved Plan: {approvedReq.examinationType} · {pathwayLabels[approvedReq.selectedModality || 'in-person']}
              </strong>
              <p>
                Specialty: {approvedReq.requiredSpecialties.join(', ')} ({approvedReq.requiredCredentials.join(', ')}) ·
                DBQs: {approvedReq.requestedDbqTypes.join(', ')}
              </p>
              {approvedReq.requestedDiagnostics.length > 0 && (
                <p>Diagnostics: {approvedReq.requestedDiagnostics.join(', ')}</p>
              )}
              {approvedReq.reviewerRationale && <small>Justification: {approvedReq.reviewerRationale}</small>}
            </div>
          </div>
          <button
            type="button"
            className="intake-secondary"
            onClick={() => {
              if (onScheduling) {
                onScheduling(item.esrId, approvedReq.id);
              } else {
                onApproved(item.esrId, item.title, approvedReq.id);
              }
            }}
          >
            Open in Scheduling <ArrowRight size={15} />
          </button>
        </div>
      ) : (
        <div className="triage-plan-section">
          {row?.awaitingResponse ? (
            <div className="triage-clarification-box">
              <h4><MessageSquare size={16} /> <HeadingText>Clarification requested via Prime EMS</HeadingText></h4>
              <p>{item.clarificationReason}</p>
              <label className="intake-field">
                <span>Response received</span>
                <textarea rows={2} maxLength={3000} value={note} onChange={e => setNote(e.target.value)} placeholder="Record VA / Prime EMS clarification response..." />
              </label>
              <div className="triage-action-buttons">
                <button type="button" className="intake-primary" disabled={busy || !note.trim()} onClick={() => void runAction('resume')}>
                  Record response and reopen review
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="triage-plan-toggle-row">
                <button type="button" className="intake-text-action" onClick={() => setEditOpen(v => !v)}>
                  <ClipboardList size={15} /> {editOpen ? 'Collapse examination plan editor' : 'Edit & approve examination plan'}
                </button>
              </div>

              {editOpen && row && (
                <fieldset className="triage-fields" disabled={busy}>
                  <div className="triage-field-grid">
                    <label className="intake-field">
                      <span>Examination type</span>
                      <select
                        value={plan.examCode}
                        onChange={e => {
                          const selected = choices.find(exam => exam.code === e.target.value);
                          setPlan({
                            examCode: e.target.value,
                            modality: '',
                            dbqs: selected ? [selected.primaryDbq] : [],
                            diagnostics: [],
                            rationale: plan.rationale,
                          });
                        }}
                      >
                        <option value="">Select an examination</option>
                        {choices.map(exam => (
                          <option key={exam.code} value={exam.code}>{exam.title}</option>
                        ))}
                      </select>
                    </label>

                    <label className="intake-field">
                      <span>
                        Examination pathway {vaSpecifiedPathway ? '(VA-specified · Locked)' : '(Permitted pathways only)'}
                      </span>
                      <select
                        disabled={!choice}
                        value={plan.modality}
                        onChange={e => setPlan({ ...plan, modality: e.target.value as TriagePlan['modality'] })}
                      >
                        <option value="">Choose pathway</option>
                        {permittedForExam.map(value => (
                          <option key={value} value={value}>{pathwayLabels[value]}</option>
                        ))}
                      </select>
                      {(selectedModalityReason || rulesEval?.aceEligibilityReason) && (
                        <small className="triage-pathway-reason">
                          {selectedModalityReason || rulesEval?.aceEligibilityReason}
                        </small>
                      )}
                    </label>
                  </div>

                  {/* Clinician ACE Eligibility Authorization (RFP 5.1.1.7) */}
                  {choice && choiceAllowsAceInRules && !canSelectAce && (
                    <div className="triage-ace-auth-box">
                      {!aceAuthOpen ? (
                        <div className="triage-ace-prompt">
                          <span>ACE records review is eligible for {choice.title} under RFP 5.1.1.7, but requires explicit clinician authorization.</span>
                          <button type="button" className="intake-secondary" onClick={() => setAceAuthOpen(true)}>
                            Authorize ACE eligibility
                          </button>
                        </div>
                      ) : (
                        <div className="triage-ace-form">
                          <label className="intake-field">
                            <span>Clinician ACE eligibility justification (RFP 5.1.1.7)</span>
                            <textarea
                              rows={2}
                              value={aceNote}
                              onChange={e => setAceNote(e.target.value)}
                              placeholder="Document why existing objective clinical records suffice for ACE..."
                            />
                          </label>
                          <div className="triage-action-buttons">
                            <button type="button" className="intake-secondary" onClick={() => setAceAuthOpen(false)}>Cancel</button>
                            <button type="button" className="intake-primary" disabled={!aceNote.trim()} onClick={() => void runAction('authorizeAce')}>
                              Record ACE authorization
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {choice && (
                    <>
                      <p className="triage-specialty">
                        <b>{choice.specialty}</b> · Required credentials: {choice.credentials.join(' · ')}
                      </p>
                      <fieldset className="triage-options">
                        <legend><HeadingText>Required & Secondary DBQs</HeadingText> {isBvaRemand && <small>(BVA Remand scope protected)</small>}</legend>
                        {choice.dbqs.map(dbq => (
                          <label key={dbq}>
                            <input
                              type="checkbox"
                              checked={plan.dbqs.includes(dbq)}
                              disabled={dbq === choice.primaryDbq}
                              onChange={e => changeValues('dbqs', dbq, e.target.checked)}
                            />
                            {dbq}
                            {dbq === choice.primaryDbq && <small>Primary Required</small>}
                          </label>
                        ))}
                      </fieldset>

                      {choice.diagnostics.length > 0 && (
                        <fieldset className="triage-options">
                          <legend><HeadingText>Optional diagnostic orders</HeadingText> <small>(Only if needed for this exam — RFP 5.2.2)</small></legend>
                          {choice.diagnostics.map(name => (
                            <label key={name}>
                              <input
                                type="checkbox"
                                checked={plan.diagnostics.includes(name)}
                                onChange={e => changeValues('diagnostics', name, e.target.checked)}
                              />
                              {name}
                            </label>
                          ))}
                        </fieldset>
                      )}
                    </>
                  )}

                  {isRuleUnverified && (
                    <div className="triage-pathway-reason">
                      Verified VA rules are unavailable for this condition. Use <strong>Request VA clarification</strong> before approving.
                    </div>
                  )}

                  <label className="intake-field">
                    <span>Clinical Review Justification (Required for Audit)</span>
                    <textarea
                      value={plan.rationale}
                      rows={2}
                      maxLength={3000}
                      placeholder="Enter brief clinical reason for your selected pathway or DBQ choice (e.g., 'In-person ROM exam required')..."
                      onChange={e => setPlan({ ...plan, rationale: e.target.value })}
                    />
                  </label>

                  <div className="triage-card-footer">
                    <div className="triage-action-buttons">
                      <button
                        type="button"
                        className="intake-text-action"
                        onClick={() => setClarificationOpen(v => !v)}
                      >
                        <MessageSquare size={15} /> Request VA clarification
                      </button>
                    </div>
                    <div className="triage-action-buttons">
                      <button type="button" className="intake-secondary" onClick={() => void runAction('draft')}>
                        Save draft
                      </button>
                      <button
                        type="button"
                        className="intake-primary"
                        disabled={!canApprove}
                        onClick={() => void runAction('approve')}
                      >
                        Approve Plan <ArrowRight size={15} />
                      </button>
                    </div>
                  </div>

                  {clarificationOpen && (
                    <div className="triage-clarification-box">
                      <label className="intake-field">
                        <span>Question for VA / Prime EMS</span>
                        <textarea rows={2} value={note} onChange={e => setNote(e.target.value)} placeholder="Describe ambiguity, remand scope conflict, or missing request detail..." />
                      </label>
                      <div className="triage-action-buttons">
                        <button type="button" className="intake-secondary" onClick={() => setClarificationOpen(false)}>Cancel</button>
                        <button type="button" className="intake-primary" disabled={!note.trim()} onClick={() => void runAction('clarification')}>
                          Send clarification request
                        </button>
                      </div>
                    </div>
                  )}
                </fieldset>
              )}
            </>
          )}
        </div>
      )}

      {error && <p className="triage-error" role="alert">{error}</p>}
      {feedback && !error && <p className="triage-feedback" role="status">{feedback}</p>}
    </article>
  );
}

export function TriagePage({
  query,
  onQueryChange,
  filters,
  activeFilter,
  onFilterChange,
  selectedId,
  onSelect,
  onScheduling,
  openSource,
}: TriageProps) {
  const { state, prepareTriageDossier } = useWorkspace();
  const [queueOpen, setQueueOpen] = useState(false);
  const [showCaseEvidence, setShowCaseEvidence] = useState(false);
  const [sourceDrawer, setSourceDrawer] = useState<SourceTarget>();
  const [approvedBanner, setApprovedBanner] = useState<{ esrId: string; title: string; entityId?: string }>();
  const [preparing, setPreparing] = useState(false);
  const caseHeading = useRef<HTMLHeadingElement>(null);

  const { esrsById = {}, contentionsById = {}, evidenceDocumentsById = {}, triage } = state ?? {};
  const caseIds = triage?.queueIds ?? [...new Set((triage?.rows ?? []).map(r => r.esrId))];
  const casesByEsr = triage?.casesByEsrId ?? {};

  const search = query.trim().toLowerCase();
  const matchedCases = caseIds
    .map(id => casesByEsr[id])
    .filter((c): c is TriageCaseView => Boolean(c))
    .filter(c => {
      if (!search) return true;
      return [
        c.esrId,
        c.claimantName,
        c.claimIdentifier,
        c.serviceBranch,
        ...c.contentions.map(ct => ct.title),
      ].some(val => val?.toLowerCase().includes(search));
    });

  const groups: Record<QueueFilter, TriageCaseView[]> = {
    all: matchedCases,
    exception: matchedCases.filter(
      c => c.clarificationCount > 0 || c.distressAlerts.length > 0 || c.contentions.some(ct => ct.row && !ct.row.readableEvidence),
    ),
    review: matchedCases.filter(c => c.pendingCount > 0 && c.clarificationCount === 0),
    ready: matchedCases.filter(c => c.pendingCount === 0 && c.approvedCount > 0),
  };

  const visibleCases = groups[activeFilter];
  const [deselected, setDeselected] = useState(false);
  const activeCase = deselected ? undefined : (visibleCases.find(c => c.esrId === selectedId) ?? visibleCases[0]);
  const queueList = useQueueRows(activeCase?.esrId, queueOpen, `${activeFilter}:${query}`);
  const activeEsr = activeCase ? esrsById[activeCase.esrId] : undefined;
  const activeDocs = activeEsr
    ? activeEsr.evidenceDocumentIds.map(id => evidenceDocumentsById[id]).filter((d): d is EvidenceDocument => Boolean(d))
    : [];

  useEffect(() => {
    setShowCaseEvidence(false);
  }, [activeCase?.esrId]);

  useEffect(() => {
    if (activeCase?.esrId && activeCase.processing?.status === 'queued' && !preparing) {
      void handlePrepareNow(activeCase.esrId);
    }
  }, [activeCase?.esrId, activeCase?.processing?.status]);

  const isBackgroundProcessing = activeCase?.processing?.status === 'queued' || activeCase?.processing?.status === 'processing';

  const handlePrepareNow = async (esrId: string) => {
    setPreparing(true);
    try {
      await prepareTriageDossier(esrId, true);
    } finally {
      setPreparing(false);
    }
  };

  return (
    <div className="intake-page triage-page queue-workbench">
      <StageHeading
        stage="Medical Triage"
        description="Review extracted medical history, validate DBQ requirements, and approve individual examination plans before scheduling."
      >
        <button type="button" className="intake-secondary" onClick={openSource}>Reference library</button>
      </StageHeading>

      {approvedBanner && (
        <div className="triage-success" role="status">
          <CheckCircle2 size={21} />
          <div>
            <strong><HeadingText>Examination plan approved & queued for Scheduling</HeadingText></strong>
            <p>{approvedBanner.title} · {approvedBanner.esrId}</p>
          </div>
          <button
            type="button"
            className="intake-secondary"
            onClick={() => onScheduling(approvedBanner.esrId, approvedBanner.entityId)}
          >
            Open Scheduling <ArrowRight size={16} />
          </button>
          <button
            type="button"
            className="intake-icon-button"
            aria-label="Dismiss approval notification"
            onClick={() => setApprovedBanner(undefined)}
          >
            <X size={17} />
          </button>
        </div>
      )}

      <div className="intake-board">
        {/* Left Queue Sidebar (Grouped by Patient / ESR) */}
        <aside className="intake-queue-card" aria-labelledby="triage-queue-title">
          <div className="intake-queue-heading">
            <h2 id="triage-queue-title"><HeadingText>Triage queue</HeadingText></h2>
            <span>{visibleCases.length} {visibleCases.length === 1 ? 'case' : 'cases'}</span>
          </div>

          <button
            type="button"
            className="intake-queue-toggle"
            aria-expanded={queueOpen}
            aria-controls="triage-queue-content"
            onClick={() => setQueueOpen(!queueOpen)}
          >
            <Inbox size={17} />
            {queueOpen ? 'Hide triage queue' : 'Choose a patient case'}
            <small>{visibleCases.length} open</small>
            <ChevronDown size={16} />
          </button>

          <div className={`intake-queue-controls${queueOpen ? ' is-open' : ''}`} id="triage-queue-content">
            <div className="intake-queue-toolbar">
              <label className="intake-search">
                <Search size={17} aria-hidden="true" />
                <input
                  placeholder="Search claimant, ESR, or condition"
                  aria-label="Search Medical Triage queue"
                  value={query}
                  onChange={e => {
                    setDeselected(false);
                    onQueryChange(e.target.value);
                  }}
                />
                {query && (
                  <button
                    type="button"
                    className="intake-icon-button"
                    aria-label="Clear Medical Triage search"
                    onClick={() => onQueryChange('')}
                  >
                    <X size={16} />
                  </button>
                )}
              </label>
              <div className="intake-queue-filters" role="group" aria-label="Triage queue filters">
                {filters.map(f => (
                  <button
                    type="button"
                    key={f.key}
                    aria-pressed={activeFilter === f.key}
                    onClick={() => {
                      setDeselected(false);
                      onFilterChange(f.key);
                    }}
                  >
                    {f.key === 'all' ? 'All cases' : f.label} <span>{groups[f.key].length}</span>
                  </button>
                ))}
              </div>
            </div>

            {visibleCases.length ? (
              <ul className="intake-queue-list" ref={queueList}>
                {visibleCases.map(c => (
                  <li key={c.esrId}>
                    <button
                      type="button"
                      className="intake-queue-row"
                      aria-pressed={activeCase?.esrId === c.esrId}
                      onClick={() => {
                        if (activeCase?.esrId === c.esrId) {
                          setDeselected(true);
                          onSelect('');
                        } else {
                          setDeselected(false);
                          onSelect(c.esrId);
                          if (queueOpen) {
                            setQueueOpen(false);
                            requestAnimationFrame(() => caseHeading.current?.focus());
                          }
                        }
                      }}
                    >
                      <span className="intake-queue-row-main">
                        <strong>{c.claimantName}</strong>
                        <small>{c.claimIdentifier} · {c.requestedCount} examination{c.requestedCount === 1 ? '' : 's'}</small>
                      </span>
                      {c.distressAlerts.length > 0 ? (
                        <Status text="Urgent Alert" tone="red" />
                      ) : c.processing.status === 'processing' || c.processing.status === 'queued' ? (
                        <Status text="Analyzing" tone="amber" />
                      ) : c.clarificationCount > 0 ? (
                        <Status text="Clarify" tone="amber" />
                      ) : (
                        <Status text={`${c.approvedCount}/${c.requestedCount} Approved`} tone={c.pendingCount === 0 ? 'green' : 'slate'} />
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="intake-queue-empty">
                <p>No cases in this view.</p>
                {(query || activeFilter !== 'all') && (
                  <button type="button" className="intake-text-action" onClick={() => { onQueryChange(''); onFilterChange('all'); }}>
                    Reset filters
                  </button>
                )}
              </div>
            )}
          </div>
        </aside>

        {/* Right Workspace: Clean 6-Section Clinical Planning Canvas */}
        {activeCase && activeEsr ? (
          <section className="intake-case-card triage-case-workspace queue-detail" key={activeCase.esrId} aria-labelledby="triage-case-title" tabIndex={0}>
            {(isBackgroundProcessing || preparing) ? (
              <div className="triage-preparing-canvas" role="status" aria-live="polite">
                <div className="triage-processing-card">
                  <Loader2 size={36} className="triage-spinner" />
                  <h3><HeadingText>Preparing clinical evidence & examination plan</HeadingText>…</h3>
                  <p>
                    Running Medical NLP, ICD-10/CPT code crosswalk, transparent record search, and VA Rules checks for <strong>{activeCase.claimantName}</strong>.
                  </p>
                  <div className="triage-processing-steps">
                    <span className="is-active">1. Medical NLP & Negation</span>
                    <span className="is-active">2. Evidence Matching</span>
                    <span className="is-active">3. AI Chronological Summary</span>
                  </div>
                  <div style={{ marginTop: '14px' }}>
                    <button
                      type="button"
                      className="intake-primary"
                      disabled={preparing}
                      onClick={() => void handlePrepareNow(activeCase.esrId)}
                    >
                      {preparing ? 'Analyzing now…' : 'Complete analysis immediately'}
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              <>

            {activeCase.processing.status === 'failed' && (
              <div className="triage-stale-banner" role="alert">
                <AlertTriangle size={16} />
                <div>
                  <strong><HeadingText>Stage 2 background preparation failed</HeadingText></strong>
                  <span>{activeCase.processing.error || 'An error occurred while preparing this dossier.'}</span>
                </div>
                <button
                  type="button"
                  className="intake-secondary"
                  disabled={preparing}
                  onClick={() => void handlePrepareNow(activeCase.esrId)}
                >
                  Retry analysis
                </button>
              </div>
            )}

            {activeCase.distressAlerts.length > 0 && (
              <div className="triage-distress-banner" role="alert">
                <ShieldAlert size={20} />
                <div>
                  <strong><HeadingText>Urgent clinical safety alert</HeadingText> — Staff review required immediately.</strong>
                  {activeCase.distressAlerts.map((alert, i) => (
                    <p key={i}>
                      {alert.documentTitle} (p. {alert.pageNumber}): {alert.reason}
                    </p>
                  ))}
                </div>
              </div>
            )}

            {/* Section 1: Patient & Case Overview */}
            <header className="triage-overview-header">
              <div className="triage-overview-identity">
                <span className="triage-avatar-initials" aria-hidden="true">{activeCase.initials}</span>
                <div>
                  <div className="triage-meta-line">
                    <span>{practiceCaseLabel(activeEsr) ?? activeCase.claimIdentifier}</span>
                    {activeCase.serviceBranch && <span>· {activeCase.serviceBranch}</span>}
                    {activeCase.isSimulated && <span className="triage-sim-pill">Simulated</span>}
                  </div>
                  <h2 id="triage-case-title" tabIndex={-1} ref={caseHeading}>{activeCase.claimantName}</h2>
                  <small>{activeCase.benefitProgram} · Priority: {activeCase.priority}</small>
                </div>
              </div>
              <div className="triage-overview-actions">
                <Status
                  text={activeCase.pendingCount === 0 ? 'All Plans Approved' : 'Pending Clinical Review'}
                  tone={activeCase.pendingCount === 0 ? 'green' : 'amber'}
                />
                <button
                  type="button"
                  className="intake-secondary"
                  disabled={preparing}
                  onClick={() => void handlePrepareNow(activeCase.esrId)}
                  title="Re-run Medical NLP, code crosswalk, and AI chronological summary"
                >
                  <RefreshCw size={14} /> Refresh analysis
                </button>
                <button
                  type="button"
                  className="intake-icon-button"
                  aria-label="Close case details"
                  title="Close case"
                  onClick={() => {
                    setDeselected(true);
                    onSelect('');
                  }}
                >
                  <X size={18} />
                </button>
              </div>
            </header>

            <div className="triage-kpi-strip">
              <div className="triage-kpi-box">
                <span>Requested examinations</span>
                <strong>{activeCase.requestedCount}</strong>
              </div>
              <div className="triage-kpi-box">
                <span>Approved plans</span>
                <strong>{activeCase.approvedCount} / {activeCase.requestedCount}</strong>
              </div>
              <div className="triage-kpi-box">
                <span>Pending review</span>
                <strong>{activeCase.pendingCount}</strong>
              </div>
            </div>

            {!!activeCase.programFlags?.programNotices?.length && (
              <div className="triage-program-notices">
                {activeCase.programFlags.programNotices.map((notice, idx) => (
                  <div key={idx} className={`triage-program-notice severity-${notice.severity}`}>
                    <strong>{notice.rule}</strong>
                    <p>{notice.text}</p>
                  </div>
                ))}
              </div>
            )}

            {activeCase.specialInstructions && (
              <div className="triage-va-instructions" role="region" aria-label="VA Instructions from Intake">
                <div className="triage-va-header">
                  <span className="triage-va-title"><strong><HeadingText>VA Request Special Instructions</HeadingText></strong></span>
                </div>
                <p className="triage-va-text">{activeCase.specialInstructions}</p>
              </div>
            )}

            {/* Section 2: Clean Medical Summary + Single "Evidence" Toggle Button */}
            <section className="triage-summary-section" aria-label="Medical Summary">
              <div className="triage-section-head">
                <h3><Sparkles size={16} /> <HeadingText>Overall Medical History Summary</HeadingText></h3>
                <div className="triage-overview-actions">
                  <span className="triage-provider-tag">
                    {activeCase.aiProvider && activeCase.aiProvider !== 'local'
                      ? `AI Summary (${activeCase.aiProvider})`
                      : 'Verified NLP Chronology'}
                  </span>
                  <button
                    type="button"
                    className="intake-secondary"
                    aria-expanded={showCaseEvidence}
                    onClick={() => setShowCaseEvidence(v => !v)}
                  >
                    <FileText size={14} /> {showCaseEvidence ? 'Hide Evidence' : `Evidence (${activeDocs.length})`}
                  </button>
                </div>
              </div>
              <p className="triage-summary-prose">{activeCase.caseOverview}</p>

              {showCaseEvidence && (
                <div className="triage-evidence-panel">
                  <div className="triage-citation-row">
                    {activeDocs.map(doc => (
                      <button
                        key={doc.id}
                        type="button"
                        className="triage-citation-chip"
                        onClick={() =>
                          setSourceDrawer({
                            documentId: doc.id,
                            documentTitle: doc.title,
                            pageNumber: 1,
                            citationLabel: `${doc.title} · p. 1`,
                          })
                        }
                      >
                        <FileText size={13} /> {documentName(doc.title)} ({doc.pageCount} p.)
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <small className="triage-disclaimer">
                All examination plans require authorized clinical approval · Rules version {activeCase.programFlags?.ruleVersion || 'MDE-NG-RFP-2026.2'}
              </small>
            </section>

            {/* Section 3, 4 & 5: Requested Examinations (Contention Cards) */}
            <section className="triage-cards-section" aria-label="Requested examinations">
              <div className="triage-section-head">
                <h3><ClipboardList size={16} /> <HeadingText>Requested examinations</HeadingText> ({activeCase.contentions.length})</h3>
              </div>
              <div className="triage-cards-stack">
                {activeCase.contentions.map(entry => {
                  const item = contentionsById[entry.contentionId];
                  if (!item) return null;
                  return (
                    <ContentionPlanningCard
                      key={entry.contentionId}
                      entry={entry}
                      item={item}
                      choices={triage?.examChoices ?? []}
                      documents={activeDocs}
                      isBvaRemand={Boolean(activeCase.programFlags?.isBvaRemand)}
                      onOpenCitation={setSourceDrawer}
                      onApproved={(esrId, title, entityId) => setApprovedBanner({ esrId, title, entityId })}
                      onScheduling={onScheduling}
                    />
                  );
                })}
              </div>
            </section>

            {/* Section 6: Footer Status & Scheduling Handoff */}
            <footer className="triage-case-footer">
              <div>
                <strong>
                  {activeCase.approvedCount === activeCase.requestedCount
                    ? 'All requested examination plans approved and sent to Scheduling'
                    : `${activeCase.approvedCount} of ${activeCase.requestedCount} examination plan(s) approved`}
                </strong>
                <small>Each approved plan creates an authorized Stage 3 Scheduling requirement.</small>
              </div>
              {activeCase.approvedCount > 0 && (
                <button
                  type="button"
                  className="intake-primary"
                  onClick={() => onScheduling(activeCase.esrId)}
                >
                  Open Stage 3 Scheduling <ArrowRight size={16} />
                </button>
              )}
            </footer>
          </>
        )}
      </section>
    ) : (
          <section className="intake-case-empty">
            <Inbox size={24} />
            <h2><HeadingText>{visibleCases.length ? 'No case selected' : 'No cases to triage'}</HeadingText></h2>
            <p>{visibleCases.length ? 'Select a patient case from the triage queue to review clinical evidence and approve examination plans.' : 'Validated Intake cases appear here once Stage 1 completes and sends them to Medical Triage.'}</p>
          </section>
        )}
      </div>

      {/* Slide-Over Original Evidence Drawer */}
      {sourceDrawer && (
        <SourceDrawer
          target={sourceDrawer}
          documents={activeDocs}
          onClose={() => setSourceDrawer(undefined)}
        />
      )}
    </div>
  );
}
