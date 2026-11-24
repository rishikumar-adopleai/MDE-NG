import type { ActivityAction, ESR, StageKey, WorkspaceState } from '../domain/model';
import { getContentionsByESR, getDBQsByESR, getESRQueueStatus, getESRProgress, getExaminationsByESR, getRequirementsByESR } from '../domain/selectors';
import type { CaseItem, Tone } from './model';

export function formatStatus(value: string): string {
  return value.replace(/-locally\b/g, '').replaceAll('-', ' ').trim();
}

export function formatCategory(category: string): string {
  switch (category) {
    case 'service-treatment': return 'Service Treatment Records';
    case 'va-medical-records': return 'VA Medical Records';
    case 'private-medical-records': return 'Private Medical Records';
    case 'hospital': return 'VA Medical Records';
    case 'laboratory': return 'Lab Reports';
    case 'imaging': return 'Imaging';
    case 'prior-examination': return 'Previous DBQs';
    case 'va-request': return 'VA Request Metadata';
    case 'other': return 'Other Documents';
    default: return formatStatus(category);
  }
}

export function practiceCaseLabel(esr?: ESR): string | undefined {
  if (!esr?.practiceScenario) return;
  return [esr.sourceReference, 'Practice case', esr.practiceScenario.exercise].filter(Boolean).join(' · ');
}

const timeZone = 'America/New_York';
const dateFormat = new Intl.DateTimeFormat('en-US', { timeZone, month: 'short', day: 'numeric', year: 'numeric' });
const timeFormat = new Intl.DateTimeFormat('en-US', {
  timeZone, hour: 'numeric', minute: '2-digit', hour12: true, timeZoneName: 'short',
});
const dueDateFormat = new Intl.DateTimeFormat('en-US', {
  timeZone, weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
});

export function dateLabel(value: string): string {
  const instant = Date.parse(value);
  if (!Number.isFinite(instant)) return 'Date and time unavailable';
  return `${dateFormat.format(instant)} · ${timeFormat.format(instant)}`;
}

const activityLabels: Partial<Record<ActivityAction, string>> = {
  'intake-aep-recorded': 'Receipt acknowledged',
  'intake-document-reviewed': 'Document reviewed',
  'intake-exception-reviewed': 'Exception reviewed',
  'intake-exception-resolved': 'Exception resolved',
  'intake-exception-escalated': 'Exception escalated',
};

export function formatActivityLabel(action: ActivityAction): string {
  return activityLabels[action] ?? formatStatus(action)
    .replace(/\b(esr|dbq|qa)\b/g, acronym => acronym.toUpperCase())
    .replace(/^./, first => first.toUpperCase());
}

export function formatWorkspaceCopy(value: string): string {
  return value
    .replace(/\b(?:local|development) workspace\b/gi, 'workspace')
    .replace(/\bin (?:this|the) local (?:simulation|workflow)\b/gi, 'in this workspace')
    .replace(/\ba (?:local )?simulated\b/gi, 'an internal')
    .replace(/\b(?:local )?simulated\b/gi, match => match[0] === match[0].toUpperCase() ? 'Internal' : 'internal')
    .replace(/\b(?:local|synthetic|seeded) (?=[a-z])/gi, '')
    .replace(/\s+locally\b/gi, '')
    .replace(/^./, first => first.toUpperCase());
}

export const documentName = (name: string) => name.replace(/^synthetic-service-note\.txt(?=$| · )/, 'service-treatment-note.txt');
export const formVersionLabel = (version: string) => version === 'synthetic-v2' ? 'Form version 2' : version;

export function toCaseItem(state: WorkspaceState, esr: ESR): CaseItem {
  const veteran = state.veteransById[esr.veteranId];
  const contentions = getContentionsByESR(state, esr.id);
  const requirements = getRequirementsByESR(state, esr.id);
  const dbqs = getDBQsByESR(state, esr.id);
  const examinations = getExaminationsByESR(state, esr.id);
  const priority = esr.priority === 'urgent' ? 'Urgent' : esr.priority === 'elevated' ? 'Elevated' : 'Routine';
  const day = 86_400_000;
  const dueTime = Date.parse(esr.dueAt);
  const now = Date.now();
  const days = Math.ceil((dueTime - now) / day);
  const elapsed = Math.max(0, Math.floor((now - Date.parse(esr.receivedAt)) / day));
  const progress = getESRProgress(state, esr.id);
  const types = [...new Set(requirements.map((item) => item.examinationType))];
  const exam = types.length ? `${types.slice(0, 2).join(' & ')} · ${dbqs.length} DBQ${dbqs.length === 1 ? '' : 's'}` : `${contentions.length} contention${contentions.length === 1 ? '' : 's'} · awaiting plan`;
  const owner = examinations.map((item) => item.examinerId ? state.examinersById[item.examinerId]?.displayName : undefined).find(Boolean) ?? esr.assignedTeamId ?? 'Unassigned';
  const status = getESRQueueStatus(state, esr.id);
  return {
    id: esr.id,
    claim: veteran?.claimIdentifier ?? 'Claim identifier unavailable',
    name: veteran?.displayName ?? 'Veteran record unavailable',
    initials: veteran?.initials ?? '--',
    location: [veteran?.city, veteran?.stateCode].filter(Boolean).join(', ') || 'Location unavailable',
    priority,
    exam,
    dueFull: dueDateFormat.format(dueTime),
    days,
    elapsed,
    total: Math.max(1, Math.ceil((dueTime - Date.parse(esr.receivedAt)) / day)),
    state: status,
    owner,
    contentions: contentions.map((item) => item.title),
  };
}

export function caseStateTone(status: string): Tone {
  if (/exception|required|blocked|clarification/i.test(status)) return 'red';
  if (/review|triage|clinical/i.test(status)) return 'amber';
  if (/ready|approved|confirmed|prepared/i.test(status)) return 'green';
  if (/scheduling|processing|new/i.test(status)) return 'blue';
  return 'slate';
}

export function stageQueueStatus(state: WorkspaceState, stage: StageKey, esrId: string) {
  const matching = Object.values(state.workItemsById).filter((item) => item.esrId === esrId && item.stage === stage);
  return matching.length ? `${matching.length} open item${matching.length === 1 ? '' : 's'}` : 'No open work items';
}
