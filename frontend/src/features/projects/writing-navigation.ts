import type { Navigator } from 'react-router-dom';
import type { WritingSession, WritingSnapshot } from './writing-session';

export interface NavigationBarrier { hasUnsettled(): boolean; flush(): Promise<boolean>; hasMemoryOnlyChanges?(): boolean }

// An uncertain save can have committed even when the current draft equals our stale
// acknowledged snapshot. Errors therefore remain a discard risk until reconciled.
export function hasUnsettledWriting(state: WritingSnapshot) {
  return state.loaded && (state.dirty || state.busy || state.recoverable || state.recoveryBlocked || state.status === 'conflict' || state.status === 'error');
}

/** Queues are retained outside routes. Only a genuine document exit can lose memory-only input. */
export function installWritingNavigationGuard(_navigator: Navigator, session: WritingSession, browser: Window = window, extraBarrier?: () => NavigationBarrier | null, _confirmLeave?: (message: string) => Promise<boolean>) {
  const onUnload = (event: BeforeUnloadEvent) => {
    const barrier = extraBarrier?.();
    const memoryOnly = session.hasMemoryOnlyChanges?.() ?? hasUnsettledWriting(session.getSnapshot());
    if (memoryOnly || barrier?.hasMemoryOnlyChanges?.()) { event.preventDefault(); event.returnValue = ''; }
  };
  browser.addEventListener('beforeunload', onUnload);
  return () => browser.removeEventListener('beforeunload', onUnload);
}
