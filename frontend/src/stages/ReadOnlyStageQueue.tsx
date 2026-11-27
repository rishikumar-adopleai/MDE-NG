import { HeadingText } from '../HeadingText';
import { useRef, useState } from 'react';
import { Activity, ChevronDown, Inbox, Search } from 'lucide-react';
import type { WorkspaceState } from '../domain/model';
import { stageCopy, type CaseItem, type StageKey } from './model';
import { StageHeading } from './StageHeading';
import type { StageProps } from './StageWorkbench';
import { CasePanel } from './shared';
import { useQueueRows } from './useQueueRows';

type Props = Pick<StageProps, 'activeFilter' | 'onFilterChange' | 'query' | 'onQueryChange' | 'onSelect' | 'onReset'> & {
  stage: StageKey;
  state: WorkspaceState;
  rows: CaseItem[];
  selectedId?: string;
  filters: (StageProps['filters'][number] & { count: number })[];
  onHistory: () => void;
};

export function ReadOnlyStageQueue({ stage, state, rows, selectedId, filters, activeFilter, onFilterChange, query, onQueryChange, onSelect, onReset, onHistory }: Props) {
  const [open, setOpen] = useState(false);
  const selected = rows.find(item => item.id === selectedId);
  const list = useQueueRows(selected?.id, open, `${activeFilter}:${query}`);
  const detail = useRef<HTMLElement>(null);

  return <div className="intake-page queue-workbench">
    <StageHeading stage={stage} description={stageCopy[stage][2]}>
      <button type="button" className="intake-secondary" disabled={!selected} onClick={onHistory}><Activity size={16} />Activity history</button>
    </StageHeading>
    <div className="intake-board">
      <aside className="intake-queue-card" aria-label="Production work queue">
        <div className="intake-queue-heading"><h2><HeadingText>Production work queue</HeadingText></h2><span>{rows.length} cases</span></div>
        <button type="button" className="intake-queue-toggle" aria-controls="readonly-queue" aria-expanded={open || !selected} disabled={!selected} onClick={() => setOpen(!open)}>
          <Inbox size={17} />Choose a case<small>{rows.length} cases</small><ChevronDown size={16} />
        </button>
        <div id="readonly-queue" className={`intake-queue-controls${open || !selected ? ' is-open' : ''}`}>
          <div className="intake-queue-toolbar">
            <label className="intake-search"><Search size={17} /><input type="search" aria-label="Search examination records" placeholder="Search examination records" value={query} onChange={event => onQueryChange(event.target.value)} /></label>
            <div className="intake-queue-filters" role="group" aria-label="Case queue filters">{filters.map(filter => <button type="button" key={filter.key} aria-pressed={activeFilter === filter.key} onClick={() => onFilterChange(filter.key)}>{filter.label}<span>{filter.count}</span></button>)}</div>
            {(query || activeFilter !== 'all') && <button type="button" className="intake-text-action" onClick={onReset}>Reset filters</button>}
          </div>
          <ul className="intake-queue-list" ref={list}>{rows.map(item => <li key={item.id}>
            <button type="button" className="intake-queue-row" aria-pressed={selected?.id === item.id} onClick={() => {
              onSelect(item.id);
              if (open) { setOpen(false); requestAnimationFrame(() => detail.current?.focus()); }
            }}>
              <span className="intake-queue-row-main"><strong>{item.name}</strong><small>{item.id}</small><small>{item.state}</small></span>
            </button>
          </li>)}</ul>
        </div>
      </aside>
      {selected ? <section key={selected.id} ref={detail} className="intake-case-card queue-detail readonly-case" tabIndex={0} aria-label={`Case details for ${selected.name}`}>
        <CasePanel item={selected} stage={stage} state={state} />
        <p className="stage-muted">Production workflow actions require configured operational services.</p>
      </section> : <section className="intake-case-empty" role="status"><Inbox size={24} /><h2><HeadingText>No work matches this stage and its filters</HeadingText></h2><p>Change the filters to see eligible work.</p></section>}
    </div>
  </div>;
}
