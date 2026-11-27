import { stageKeys, type StageKey } from './domain/model.ts';

export type WorkspacePage = 'Dashboard' | StageKey;

const stageSlugs: Record<StageKey, string> = {
  Intake: 'intake',
  'Medical Triage': 'medical-triage',
  Scheduling: 'scheduling',
  'Clinical Evaluation': 'clinical-evaluation',
  'Internal QA': 'internal-qa',
  'VA Delivery & Review': 'va-delivery-review',
};

const pageBySlug = new Map<string, WorkspacePage>([
  ['dashboard', 'Dashboard'],
  ...stageKeys.map(stage => [stageSlugs[stage], stage] as [string, StageKey]),
]);

export function workspaceHref(page: WorkspacePage, esrId?: string) {
  const base = page === 'Dashboard' ? '/dashboard' : `/${stageSlugs[page]}`;
  return esrId ? `${base}/esr/${encodeURIComponent(esrId)}` : base;
}

export function readWorkspaceLocation(location: Pick<Location, 'pathname' | 'search'>): { page: WorkspacePage; esrId?: string } {
  const parts = location.pathname.split('/').filter(Boolean);
  const slugPage = pageBySlug.get(parts[0] ?? '');
  if (slugPage) {
    const esrIndex = parts.indexOf('esr');
    const esrId = esrIndex >= 0 && parts[esrIndex + 1] ? decodeURIComponent(parts[esrIndex + 1]) : undefined;
    return { page: slugPage, esrId };
  }

  // Preserve bookmarked URLs from the original query-based prototype.
  const legacyName = new URLSearchParams(location.search).get('stage');
  const legacyStage = stageKeys.find(stage => stage === legacyName);
  if (legacyStage) return { page: legacyStage };
  return { page: 'Dashboard' };
}

export function canonicalEntryHref(location: Pick<Location, 'pathname' | 'search' | 'hash'>) {
  const current = `${location.pathname}${location.search}${location.hash}`;
  const target = workspaceHref(readWorkspaceLocation(location).page,
    location.pathname.split('/').includes('esr') ? readWorkspaceLocation(location).esrId : undefined) + location.hash;
  return current === target ? undefined : target;
}
