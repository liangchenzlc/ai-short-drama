import { useEffect, useState } from 'react';

export function useBatchSelectionState(scope: string) {
  const [ids, setIds] = useState<string[]>([]);
  useEffect(() => { setIds([]); }, [scope]);
  const toggle = (id: string, checked: boolean) =>
    setIds((current) => checked ? [...new Set([...current, id])] : current.filter((value) => value !== id));
  return { ids, setIds, toggle };
}