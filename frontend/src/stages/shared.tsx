import type { ReactNode } from 'react';
import { Inbox } from 'lucide-react';
import { type CaseItem, type StageKey, type Tone } from './model';
import type { WorkspaceState } from '../domain/model';
import { getContentionsByESR, getDBQsByESR, getESRProgress, getExaminationsByESR, getRequirementsByESR } from '../domain/selectors';
import { caseStateTone, formatStatus } from './presentation';
export function CasePanel({ item, stage, state }: { item: CaseItem; stage: StageKey; state: WorkspaceState }) {
  const progress = getESRProgress(state, item.id);
  const elapsedPercent = Math.round((item.elapsed / item.total) * 100);
  const elapsedTone = elapsedPercent >= 85 ? 'crit' : elapsedPercent >= 60 ? 'warn' : 'ok';
  const dueTone = item.days <= 2 ? 'crit' : item.days <= 4 ? 'warn' : 'ok';
  const contentions = getContentionsByESR(state, item.id);
  const requirements = getRequirementsByESR(state, item.id);
  const examinations = getExaminationsByESR(state, item.id);
  const dbqs = getDBQsByESR(state, item.id);
  const actionText: Record<StageKey, string> = {
    Intake: 'Review outstanding document exceptions and validate the intake record.',
    'Medical Triage': 'Review each contention and confirm its examination scope.',
    Scheduling: 'Resolve unscheduled requirements and review claimant contact history.',
    'Clinical Evaluation': 'Complete the selected DBQ and record the clinician review.',
    'Internal QA': 'Disposition findings for each report version before approval.',
    'VA Delivery & Review': 'Validate the results package and prepare it for staff review.',
  };
  const stateTone = caseStateTone(item.state);

  return <aside className="case-panel" aria-label="Selected case summary">
    <div className="case-head"><span className="case-avatar" aria-hidden="true">{item.initials}</span><div><b>{item.name}</b><small>{item.id} · {item.claim}</small></div></div>
    <div className="case-due"><span>Report due</span><strong>{item.dueFull}</strong><em className={`tone-${dueTone}`}>{item.days} calendar days left{dueTone === 'crit' ? ' · at risk' : ''}</em></div>
    <dl className="case-facts"><div><dt>Exam</dt><dd>{item.exam}</dd></div><div><dt>Location</dt><dd>{item.location}</dd></div><div><dt>Case state</dt><dd><Status text={item.state} tone={stateTone} /></dd></div><div><dt>Priority</dt><dd>{item.priority}</dd></div><div><dt>Owner</dt><dd>{item.owner}</dd></div></dl>
    <div className="sla-bars" aria-label="Case and record progress"><div className="sla-row"><div><span>Business days used</span><b>{item.elapsed} of {item.total}</b></div><span className="sla-track"><i className={`tone-${elapsedTone}`} style={{ width: `${elapsedPercent}%` }} /></span></div><div className="sla-row"><div><span>Approved records</span><b>{progress.completed} of {progress.total}</b></div><span className="sla-track"><i style={{ width: `${progress.percentage}%` }} /></span></div></div>
    <section className="case-record-summary"><b>Related work status</b>{[
      ...contentions.map((record) => ({ id: record.id, label: record.title, status: formatStatus(record.status) })),
      ...requirements.map((record) => ({ id: record.id, label: record.examinationType, status: formatStatus(record.status) })),
      ...examinations.map((record) => ({ id: record.id, label: record.id, status: formatStatus(record.status) })),
      ...dbqs.map((record) => ({ id: record.id, label: record.formType, status: formatStatus(record.status) })),
    ].slice(0, 6).map((record) => <div key={record.id}><span>{record.label}</span><Status text={record.status} tone={caseStateTone(record.status)} /></div>)}</section>
    <section className="next-action"><span>{stage} work cue</span><p>{actionText[stage]}</p></section>
  </aside>;
}

export function SearchStrip({ items, selectedId, onSelect, query, onClear }: { items: CaseItem[]; selectedId: string; onSelect: (id: string) => void; query: string; onClear: () => void }) {
  if (items.length === 0) return <section className="search-strip" role="status"><div className="empty-state"><b>No cases match “{query}”</b><span>Search by ESR number, claim ID, veteran last name, exam type, or case state.</span></div><button type="button" className="btn btn-outline" onClick={onClear}>Clear search</button></section>;
  return <section className="search-strip"><b>{items.length} matching {items.length === 1 ? 'case' : 'cases'}</b><div>{items.map((item) => <button type="button" className={selectedId === item.id ? 'selected' : ''} aria-pressed={selectedId === item.id} key={item.id} onClick={() => onSelect(item.id)}>{item.id}<span>{item.name}</span></button>)}</div></section>;
}

export function PanelTitle({ icon: Icon, title, meta, action }: { icon: typeof Inbox; title: string; meta?: string; action?: ReactNode }) { return <div className="panel-title"><span className="panel-title-icon"><Icon size={17} /></span><div><h2>{title}</h2>{meta && <p>{meta}</p>}</div>{action && <div className="panel-title-action">{action}</div>}</div>; }
export function QueuePagination({ page, total, pageSize = 12, onPageChange }: { page: number; total: number; pageSize?: number; onPageChange: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return <div className="queue-pagination"><span>{total ? `${page * pageSize + 1}–${Math.min(total, (page + 1) * pageSize)} of ${total}` : '0 items'}</span><div><button type="button" className="btn btn-outline" disabled={page <= 0} onClick={() => onPageChange(Math.max(0, page - 1))}>Previous</button><button type="button" className="btn btn-outline" disabled={page + 1 >= pages} onClick={() => onPageChange(Math.min(pages - 1, page + 1))}>Next</button></div></div>;
}
export function Status({ text, tone }: { text: string; tone: Tone }) { return <span className={`status tone-${tone}`}><i />{text}</span>; }
export function DefinitionList({ items }: { items: string[][] }) { return <dl className="definition-list">{items.map(([term, value]) => <div key={term}><dt>{term}</dt><dd>{value}</dd></div>)}</dl>; }
export function FormSection({ number, title, status, children }: { number: string; title: string; status: string; children: ReactNode }) { return <section className="form-section"><header><span>{number}</span><h3>{title}</h3><Status text={status} tone={status === 'Complete' ? 'green' : status === 'Required' ? 'amber' : 'blue'} /></header>{children}</section>; }

