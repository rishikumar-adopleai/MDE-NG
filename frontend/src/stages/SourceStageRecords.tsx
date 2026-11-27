import { HeadingText } from '../HeadingText';
import { useEffect, useRef, useState } from 'react';
import { FileText, Search } from 'lucide-react';
import { loadSourceRecords, type ExchangeEvent, type ReferenceForm } from '../data/repositories/workspaceApi';
import type { StageKey } from './model';
import { dateLabel } from './presentation';
import { StageHeading } from './StageHeading';
import { useQueueRows } from './useQueueRows';
import { StageBlock } from './StageWorkbench';

const descriptions: Partial<Record<StageKey, string>> = {
  'Medical Triage': 'Review examination requests and clarification events from the exchange documents.',
  Scheduling: 'Review appointment and scheduling events recorded in the exchange documents.',
  'Clinical Evaluation': 'Review recorded examination milestones and inspect questionnaire revisions.',
  'Internal QA': 'Inspect questionnaire revisions and their validation rules. Staff decisions appear in the work queue.',
  'VA Delivery & Review': 'Review result-package notices and their document references.',
};

function EventDetail({ event }: { event: ExchangeEvent }) {
  const facts = Object.entries(event.facts);
  const details = facts.filter(([label]) => !label.endsWith('reference') && label !== 'Request link');
  const references = facts.filter(([label]) => label.endsWith('reference'));
  return <StageBlock title="Event details">
    {event.facts['Request link'] && <p className="stage-description">{event.facts['Request link']}</p>}
    <dl className="source-record-facts">
      {event.at && <div><dt>Recorded</dt><dd><time dateTime={event.at}>{dateLabel(event.at)}</time></dd></div>}
      {event.appointmentAt && <div><dt>Appointment time</dt><dd><time dateTime={event.appointmentAt}>{dateLabel(event.appointmentAt)}</time></dd></div>}
      {details.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
    </dl>
    <details><summary><HeadingText>File and identifiers</HeadingText></summary><dl className="source-record-facts">
      <div><dt>Source file</dt><dd>{event.source}</dd></div>
      {references.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
    </dl></details>
  </StageBlock>;
}

function FormDetail({ form }: { form: ReferenceForm }) {
  return <StageBlock title="Questionnaire reference" description={form.bodySystem}>
    <dl className="source-record-facts">
      <div><dt>Form revision</dt><dd>{form.revision}</dd></div>
      <div><dt>Fields</dt><dd>{form.fieldCount}</dd></div>
      <div><dt>Rules</dt><dd>{form.ruleCount}</dd></div>
    </dl>
    <h3><HeadingText>Form sections</HeadingText></h3>
    <ol className="source-record-sections">{form.sections.map((text, i) => <li key={i}>{text}</li>)}</ol>
    <details><summary><HeadingText>File and identifiers</HeadingText></summary><dl className="source-record-facts">
      <div><dt>Document ID</dt><dd>{form.documentId}</dd></div><div><dt>Source file</dt><dd>{form.source}</dd></div>
    </dl></details>
  </StageBlock>;
}

export function SourceStageRecords({ stage, openQueue }: {stage: StageKey; openQueue: () => void}) {
  const [data, setData] = useState<{ events: ExchangeEvent[]; forms: ReferenceForm[] }>();
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [query, setQuery] = useState('');
  const [tab, setTab] = useState<'events' | 'forms'>(stage === 'Internal QA' ? 'forms' : 'events');
  const [selectedId, setSelectedId] = useState('');
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    loadSourceRecords(stage, controller.signal).then(setData).catch((failure: Error) => {
      if (!controller.signal.aborted) setError(failure.message);
    });
    return () => controller.abort();
  }, [stage, retry]);

  const needle = query.trim().toLowerCase();
  const activeTab = data && !data[tab].length ? (tab === 'events' ? 'forms' : 'events') : tab;
  const rows = (data?.[activeTab] ?? []).filter(row => {
    const searchable = 'facts' in row ? [row.title, row.claimant, row.scenario, row.source, row.facts['Request reference']]
      : [row.title, row.bodySystem, row.revision, row.documentId];
    return searchable.join(' ').toLowerCase().includes(needle);
  });
  const selected = rows.find(row => row.id === selectedId) ?? rows[0];
  const queueList = useQueueRows(selected?.id, open, `${activeTab}:${query}`);
  const heading = useRef<HTMLHeadingElement>(null);

  return <div className="intake-page operations-page queue-workbench">
    <StageHeading stage={stage} description={descriptions[stage] ?? 'Review the source records for this stage.'}>
      <button type="button" className="intake-secondary" onClick={openQueue}>Work queue</button>
    </StageHeading>
    <div className="intake-board">
      <aside className="intake-queue-card" aria-label="Source records">
        <div className="intake-queue-heading"><h2><HeadingText>Reference library</HeadingText></h2><span>{rows.length} records</span></div>
        <button type="button" className="intake-queue-toggle" aria-expanded={open || !selected} disabled={!selected} aria-controls="exchange-records" onClick={() => setOpen(!open)}><FileText size={17} /><HeadingText>Reference library</HeadingText><small>{rows.length} records</small></button>
        <div id="exchange-records" className={`intake-queue-controls${open || !selected ? ' is-open' : ''}`}>
          <div className="intake-queue-toolbar">
            <label className="intake-search"><Search size={17} /><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search source records" aria-label="Search source records" /></label>
            {Boolean(data?.events.length && data?.forms.length) && <div className="intake-queue-filters" role="group" aria-label="Source record type">
              <button type="button" aria-pressed={tab === 'events'} onClick={() => { setTab('events'); setQuery(''); }}>Events <span>{data?.events.length}</span></button>
              <button type="button" aria-pressed={tab === 'forms'} onClick={() => { setTab('forms'); setQuery(''); }}>DBQ forms <span>{data?.forms.length}</span></button>
            </div>}
          </div>
          <ul className="intake-queue-list" ref={queueList}>{rows.map(row => <li key={row.id}>
            <button type="button" className="intake-queue-row" aria-pressed={selected?.id === row.id} onClick={() => {
              setSelectedId(row.id);
              if (open) { setOpen(false); requestAnimationFrame(() => heading.current?.focus()); }
            }}>
              <span className="intake-queue-row-main"><strong>{row.title}</strong>
                {'facts' in row ? <><small>{row.claimant || (row.facts['Request link'] ? 'Request not linked' : 'Claimant not provided')}</small><small>{row.scenario}</small></>
                  : <small>{row.bodySystem} · Revision {row.revision}</small>}
              </span>
            </button>
          </li>)}</ul>
        </div>
      </aside>
      {selected ? <section className="intake-case-card queue-detail" key={`${activeTab}:${selected.id}`} aria-labelledby="source-record-title" tabIndex={0}>
        <header className="intake-case-header">
          <span className="intake-case-avatar"><FileText size={20} aria-hidden="true" /></span>
          <div className="intake-case-identity">
            <span>{'facts' in selected ? selected.scenario : `DBQ revision ${selected.revision}`}</span>
            <h2 id="source-record-title" ref={heading} tabIndex={-1}>{selected.title}</h2>
            {'facts' in selected && selected.claimant && <p>{selected.claimant}</p>}
          </div>
        </header>
        {'facts' in selected ? <EventDetail event={selected} /> : <FormDetail form={selected} />}
      </section> : <section className="intake-case-empty" role={error ? 'alert' : 'status'}>
        <FileText size={24} /><h2><HeadingText>{error ? 'Could not load source records' : data ? 'No matching source records' : 'Loading source records'}</HeadingText></h2>
        {error && <><p>{error}</p><button type="button" className="intake-secondary" onClick={() => { setError(''); setRetry(value => value + 1); }}>Retry</button></>}
      </section>}
    </div>
  </div>;
}
