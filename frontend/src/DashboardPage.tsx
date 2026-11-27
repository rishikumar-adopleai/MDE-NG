import { ArrowRight, CalendarDays, CircleAlert, Clock3, ClipboardList, Layers3 } from 'lucide-react';
import { useState, type MouseEvent } from 'react';
import type { StageKey, WorkspaceState } from './domain/model';
import { dateLabel, formatActivityLabel, formatWorkspaceCopy } from './stages/presentation';
import { navItems } from './stages/model';
import { StageHeading } from './stages/StageHeading';
import { workspaceHref } from './workspaceRoutes';

const stageColors: Record<StageKey, string> = {
  Intake: '#17695d',
  'Medical Triage': '#467dc2',
  Scheduling: '#c37d32',
  'Clinical Evaluation': '#7962ad',
  'Internal QA': '#329395',
  'VA Delivery & Review': '#ad557c',
};

const deadlineBuckets = [
  { label: 'Overdue', color: '#b75646' },
  { label: 'Next 7 days', color: '#b67832' },
  { label: 'Days 8–14', color: '#407cb0' },
  { label: 'After 14 days', color: '#248476' },
  { label: 'No due date', color: '#71828e' },
];
const day = 86_400_000;

export function DashboardPage({ state, onOpen }: { state: WorkspaceState; onOpen: (stage: StageKey, esrId: string) => void }) {
  const [selectedDeadline, setSelectedDeadline] = useState<number>();
  const now = Date.now();
  const soon = now + 7 * day;
  const openWork = Object.values(state.workItemsById).filter(item =>
    item.status !== 'completed' && item.status !== 'cancelled');
  const queues = navItems.map(({ name }) => {
    const items = openWork.filter(item => item.stage === name);
    return {
      name,
      items,
      blocked: items.filter(item => item.status === 'blocked' || item.blockingReasons.length > 0).length,
    };
  });
  let angle = 0;
  const occupiedQueues = queues.filter(queue => queue.items.length > 0);
  const slices = occupiedQueues.map(({ name, items }) => {
    const start = angle;
    angle += (items.length / openWork.length) * 100;
    return `${stageColors[name]} ${start}% ${Math.min(angle, 100)}%`;
  });
  const donutBackground = occupiedQueues.length === 1 ? stageColors[occupiedQueues[0].name]
    : slices.length > 1 ? `conic-gradient(${slices.join(', ')})` : undefined;
  const stageByEsr = new Map<string, StageKey>();
  for (const queue of queues) {
    for (const item of queue.items) {
      if (!stageByEsr.has(item.esrId)) stageByEsr.set(item.esrId, queue.name);
    }
  }
  const deadlines = deadlineBuckets.map(bucket => ({
    ...bucket, requests: [] as { esrId: string; stage: StageKey; dueAt?: string; due: number }[],
  }));
  for (const [esrId, stage] of stageByEsr) {
    const dueAt = state.esrsById[esrId]?.dueAt;
    const due = Date.parse(dueAt ?? '');
    const bucket = !Number.isFinite(due) ? 4 : due < now ? 0 : due <= soon ? 1 : due <= now + 14 * day ? 2 : 3;
    deadlines[bucket].requests.push({ esrId, stage, dueAt: Number.isFinite(due) ? dueAt : undefined, due });
  }
  const maxDeadline = Math.max(1, ...deadlines.map(bucket => bucket.requests.length));
  const activeDeadline = selectedDeadline === undefined ? undefined : deadlines[selectedDeadline];
  const attention = openWork.map(item => {
    const esr = state.esrsById[item.esrId];
    const due = esr ? Date.parse(esr.dueAt) : Number.NaN;
    const overdue = Number.isFinite(due) && due < now;
    const dueSoon = Number.isFinite(due) && due >= now && due <= soon;
    const blocked = item.status === 'blocked' || item.blockingReasons.length > 0;
    const urgent = esr?.priority === 'urgent';
    return { item, due, overdue, dueSoon, blocked, urgent };
  }).filter(row => row.blocked || row.overdue || row.dueSoon || row.urgent)
    .sort((a, b) => Number(b.blocked) - Number(a.blocked) || Number(b.overdue) - Number(a.overdue) || a.due - b.due);
  const upcoming = Object.values(state.appointmentsById).filter(item =>
    ['held', 'confirmed-locally', 'reschedule-requested'].includes(item.status) &&
    Date.parse(item.startsAt) >= now && Date.parse(item.startsAt) <= soon)
    .sort((a, b) => a.startsAt.localeCompare(b.startsAt));
  const activities = Object.values(state.activitiesById).sort((a, b) => b.at.localeCompare(a.at)).slice(0, 6);

  const go = (event: MouseEvent<HTMLAnchorElement>, stage: StageKey, id = '') => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault(); onOpen(stage, id);
  };
  const getItemStage = (id: string): StageKey => state.workItemsById[id]?.stage ?? 'Intake';

  return <div className="dashboard-page">
    <StageHeading stage="Dashboard" />

    <section className="dashboard-metrics" aria-label="Workspace summary">
      <article><span className="dashboard-metric-icon"><Layers3 size={18} /></span><small>Active ESRs</small><b>{stageByEsr.size}</b><span>Requests with open work</span></article>
      <article className="attention-metric"><span className="dashboard-metric-icon"><CircleAlert size={18} /></span><small>Needs attention</small><b>{attention.length}</b><span>Open work to review</span></article>
      <article><span className="dashboard-metric-icon"><Clock3 size={18} /></span><small>Due in 7 days</small><b>{deadlines[1].requests.length}</b><span>Active ESRs</span></article>
      <article><span className="dashboard-metric-icon"><CalendarDays size={18} /></span><small>Upcoming appointments</small><b>{upcoming.length}</b><span>Next 7 days</span></article>
    </section>

    <div className="dashboard-grid dashboard-charts">
      <section className="dashboard-panel" aria-labelledby="dashboard-workload">
        <div className="dashboard-panel-heading"><div><span className="dashboard-panel-icon"><ClipboardList size={17} /></span><div><h2 id="dashboard-workload">Workload by stage</h2><p>Open work items · select a stage to view its queue</p></div></div></div>
        <div className="dashboard-workload-body">
          <div className="dashboard-donut" aria-hidden="true" style={{ background: donutBackground }}>
            <span><b>{openWork.length}</b>Open items</span>
          </div>
          <ul className="dashboard-chart-legend">
            {queues.map(({ name, items, blocked }) => <li key={name}>
              <a href={workspaceHref(name)} onClick={event => go(event, name)}>
                <span className="dashboard-chart-swatch" style={{ backgroundColor: stageColors[name] }} aria-hidden="true" />
                <span>{name}{blocked > 0 && <small>{blocked} blocked</small>}</span>
                <b aria-label={`${items.length} open items`}>{items.length}</b>
                <ArrowRight size={14} aria-hidden="true" />
              </a>
            </li>)}
          </ul>
        </div>
      </section>

      <section className="dashboard-panel" aria-labelledby="dashboard-deadlines">
        <div className="dashboard-panel-heading"><div><span className="dashboard-panel-icon"><Clock3 size={17} /></span><div><h2 id="dashboard-deadlines">Request deadlines</h2><p>Active ESRs · select a range to view its requests</p></div></div></div>
        <ul className="dashboard-deadline-bars">
          {deadlines.map(({ label, color, requests }, index) => <li key={label}>
            <button type="button" aria-pressed={selectedDeadline === index} aria-controls="dashboard-deadline-results"
              onClick={() => setSelectedDeadline(current => current === index ? undefined : index)}>
              <span>{label}</span>
              <span className="dashboard-bar-track" aria-hidden="true"><span style={{ width: `${requests.length / maxDeadline * 100}%`, backgroundColor: color }} /></span>
              <b aria-label={`${requests.length} requests`}>{requests.length}</b>
            </button>
          </li>)}
        </ul>
      </section>
      <section id="dashboard-deadline-results" className="dashboard-panel dashboard-deadline-results"
        hidden={!activeDeadline} aria-labelledby="dashboard-deadline-selection">
          {activeDeadline && <>
            <h3 id="dashboard-deadline-selection">{activeDeadline.label} · {activeDeadline.requests.length} {activeDeadline.requests.length === 1 ? 'request' : 'requests'}</h3>
            {activeDeadline.requests.length === 0 ? <p>No requests in this range.</p> :
              <div className="dashboard-simple-list">{[...activeDeadline.requests].sort((a, b) => a.due - b.due || a.esrId.localeCompare(b.esrId)).map(({ esrId, stage, dueAt }) =>
                <a key={esrId} href={workspaceHref(stage, esrId)} onClick={event => go(event, stage, esrId)}>
                  <span><b>{esrId}</b><small>{stage}</small></span>
                  {dueAt ? <time dateTime={dueAt}>{dateLabel(dueAt)}</time> : <small>No due date</small>}
                </a>)}</div>}
          </>}
      </section>
    </div>

    <div className="dashboard-grid">
      <section className="dashboard-panel dashboard-list-panel" aria-labelledby="dashboard-attention-title">
        <div className="dashboard-panel-heading"><div><span className="dashboard-panel-icon"><CircleAlert size={17} /></span><div><h2 id="dashboard-attention-title">Needs attention</h2><p>Blocked, urgent, overdue, or due soon</p></div></div><span className="dashboard-count">{attention.length}</span></div>
        {attention.length ? <div className="dashboard-attention-list dashboard-list-scroll" role="group" aria-labelledby="dashboard-attention-title" tabIndex={0}>{attention.map(({ item, blocked, overdue, urgent }) => {
          const esr = state.esrsById[item.esrId];
          const stage = item.stage;
          const reason = blocked ? item.blockingReasons[0] || 'Waiting for a required review' : overdue ? 'Past the recorded due date' : urgent ? 'Urgent priority' : 'Due within 7 days';
          return <a className="dashboard-attention-row" key={item.id} href={workspaceHref(stage, item.esrId)} onClick={event => go(event, stage, item.esrId)}>
            <span className={`dashboard-priority-dot ${blocked || overdue ? 'is-alert' : urgent ? 'is-urgent' : ''}`} />
            <span className="dashboard-attention-copy"><b>{formatWorkspaceCopy(item.title)}</b><span>{item.esrId} · {stage}</span><small>{formatWorkspaceCopy(reason)}</small></span>
            <span className="dashboard-due">{esr ? dateLabel(esr.dueAt).split(' · ')[0] : 'Due date unavailable'}<ArrowRight size={16} /></span>
          </a>;
        })}</div> : <div className="dashboard-empty">No open work currently matches these attention rules.</div>}
      </section>

      <section className="dashboard-panel dashboard-list-panel" aria-labelledby="dashboard-appointments-title">
        <div className="dashboard-panel-heading"><div><span className="dashboard-panel-icon"><CalendarDays size={17} /></span><div><h2 id="dashboard-appointments-title">Upcoming appointments</h2><p>Next seven days</p></div></div><span className="dashboard-count">{upcoming.length}</span></div>
        {upcoming.length ? <div className="dashboard-simple-list dashboard-list-scroll" role="group" aria-labelledby="dashboard-appointments-title" tabIndex={0}>{upcoming.map(item => {
          const veteran = state.veteransById[state.esrsById[item.esrId]?.veteranId ?? ''];
          const examiner = state.examinersById[item.examinerId];
          return <a href={workspaceHref('Scheduling', item.esrId)} key={item.id} onClick={event => go(event, 'Scheduling', item.esrId)}>
            <span><b>{veteran?.displayName ?? item.esrId}</b><small>{examiner?.displayName ?? 'Examiner unassigned'} · {item.status === 'held' ? 'Held slot' : item.status === 'confirmed-locally' ? 'Locally confirmed' : 'Reschedule requested'}</small></span>
            <time dateTime={item.startsAt}>{dateLabel(item.startsAt)}</time>
          </a>;
        })}</div> : <div className="dashboard-empty">No appointments in the next seven days.</div>}
      </section>

      <section className="dashboard-panel dashboard-activity">
        <div className="dashboard-panel-heading"><div><span className="dashboard-panel-icon"><Clock3 size={17} /></span><div><h2>Recent activity</h2><p>Latest recorded workflow events</p></div></div></div>
        {activities.length ? <div className="dashboard-simple-list">{activities.map(item => {
          const stage = getItemStage(item.relatedWorkItemId ?? '');
          return <a href={workspaceHref(stage, item.esrId)} key={item.id} onClick={event => go(event, stage, item.esrId)}>
            <span><b>{formatActivityLabel(item.action)}</b><small>{item.esrId} · {item.entityType}</small></span><time dateTime={item.at}>{dateLabel(item.at)}</time>
          </a>;
        })}</div> : <div className="dashboard-empty">No workflow activity has been recorded.</div>}
      </section>
    </div>
  </div>;
}
