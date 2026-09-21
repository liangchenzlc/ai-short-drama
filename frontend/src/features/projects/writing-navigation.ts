import type { Navigator } from 'react-router-dom';
import type { WritingSession, WritingSnapshot } from './writing-session';

export interface NavigationBarrier { hasUnsettled(): boolean; flush(): Promise<boolean> }

// An uncertain save can have committed even when the current draft equals our stale
// acknowledged snapshot. Errors therefore remain a discard risk until reconciled.
export function hasUnsettledWriting(state: WritingSnapshot) {
  return state.loaded && (state.dirty || state.busy || state.status === 'conflict' || state.status === 'error');
}

/** Guard BrowserRouter's navigator and capture POP before its history listener.
 * Keep the current entry mounted while saving, then replay an accepted transition. */
export function installWritingNavigationGuard(navigator: Navigator, session: WritingSession, browser: Window = window, extraBarrier?: () => NavigationBarrier | null) {
  let active = true;
  let index: number = browser.history.state?.idx ?? 0;
  let reverting = false;
  let replaying = false;
  let pending: { action: () => void; allowed?: boolean } | null = null;
  const requiresGuard = () => hasUnsettledWriting(session.getSnapshot()) || !!extraBarrier?.()?.hasUnsettled();
  const finish = () => {
    if (!active || reverting || !pending || pending.allowed === undefined) return;
    const transition = pending; pending = null;
    if (transition.allowed) { transition.action(); index = browser.history.state?.idx ?? index; }
  };
  const request = (action: () => void) => {
    if (pending || replaying) return;
    pending = { action };
    void session.flush().then(async writingSaved => {
      const barrier = extraBarrier?.();
      const saved = writingSaved && (!barrier?.hasUnsettled() || await barrier.flush());
      if (!active || !pending) return;
      pending.allowed = saved || browser.confirm('内容尚未全部保存。离开会丢失当前页面的未保存草稿。确定离开？');
      finish();
    });
  };
  const push = navigator.push;
  const replace = navigator.replace;
  const guard = (action: () => void) => {
    if (pending || replaying) return;
    if (!requiresGuard()) { action(); index = browser.history.state?.idx ?? index; }
    else request(action);
  };
  navigator.push = (...args) => guard(() => push.apply(navigator, args));
  navigator.replace = (...args) => guard(() => replace.apply(navigator, args));
  const onPop = (event: PopStateEvent) => {
    const next: number | undefined = event.state?.idx;
    if (replaying) { replaying = false; index = next ?? index; return; }
    if (reverting) {
      event.stopImmediatePropagation();
      if (next !== index) { if (next !== undefined) browser.history.go(index - next); return; }
      reverting = false; finish(); return;
    }
    if (!requiresGuard() && !pending) { index = next ?? index; return; }
    // BrowserRouter owns indexed same-document entries; document exits use beforeunload.
    if (next === undefined || next === index) return;
    event.stopImmediatePropagation();
    reverting = true;
    browser.history.go(index - next);
    request(() => { replaying = true; browser.history.go(next - index); });
  };
  const onUnload = (event: BeforeUnloadEvent) => {
    if (requiresGuard()) { event.preventDefault(); event.returnValue = ''; }
  };
  browser.addEventListener('popstate', onPop, true);
  browser.addEventListener('beforeunload', onUnload);
  return () => {
    active = false;
    navigator.push = push; navigator.replace = replace;
    browser.removeEventListener('popstate', onPop, true);
    browser.removeEventListener('beforeunload', onUnload);
  };
}
