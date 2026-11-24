import { stageKeys, type StageKey } from './domain/model.ts';

export function stageFromSearch(search: string): StageKey {
  const name = new URLSearchParams(search).get('stage');
  return stageKeys.find(stage => stage === name) ?? 'Intake';
}

export function stageHref(stage: StageKey, search: string) {
  const params = new URLSearchParams(search);
  params.set('stage', stage);
  return `/?${params}`;
}

export function canonicalEntryHref({ pathname, search, hash }: Location) {
  if (pathname === '/dev' || pathname === '/dev/' || (pathname === '/' && !new URLSearchParams(search).has('stage'))) {
    return stageHref(stageFromSearch(search), search) + hash;
  }
}
