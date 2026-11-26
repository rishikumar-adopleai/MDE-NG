import { useLayoutEffect, useRef } from 'react';

export function useQueueRows(selectedId: string | undefined, open: boolean, filterKey: string) {
  const listRef = useRef<HTMLUListElement>(null);
  useLayoutEffect(() => {
    const list = listRef.current;
    if (!list?.clientHeight || list.scrollHeight <= list.clientHeight) return;
    const selected = list.querySelector('button[aria-pressed="true"]');
    if (!selected) return;
    const viewport = list.getBoundingClientRect();
    const row = selected.getBoundingClientRect();
    if (row.top < viewport.top) list.scrollTop += row.top - viewport.top;
    else if (row.bottom > viewport.bottom) list.scrollTop += row.bottom - viewport.bottom;
  }, [selectedId, open, filterKey]);
  return listRef;
}
