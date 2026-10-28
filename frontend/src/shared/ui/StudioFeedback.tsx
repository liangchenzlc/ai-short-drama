import { createFeedbackStore } from '@host/shared/ui/studio-feedback-store';
import type { Detail, FeedbackContextValue, StudioFeedbackProps } from '@host/types/shared/ui/StudioFeedback';
import { Alert as AntAlert, Button, Popover, message as antdMessage } from 'antd';
import { AlertCircle, X } from 'lucide-react';
import {
  Children,
  createContext,
  isValidElement,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from 'react';
import './studio-feedback.css';
export type { StudioFeedbackType } from '@host/types/shared/ui/studio-feedback-store';
export { feedbackType } from './studio-feedback-store';
const FeedbackContext = createContext<FeedbackContextValue | null>(null);
const empty: ReturnType<typeof createFeedbackStore<Detail>> extends {
  getSnapshot: () => infer T;
}
  ? T
  : never = [];
const noopSubscribe = () => () => {};
const textOf = (node: ReactNode): string =>
  Children.toArray(node)
    .map((child) => {
      if (typeof child === 'string' || typeof child === 'number') return String(child);
      return isValidElement<{ children?: ReactNode }>(child) ? textOf(child.props.children) : '';
    })
    .join(' ')
    .trim();

export function StudioMessageProvider({
  children,
  getContainer,
  local = false,
}: {
  children: ReactNode;
  getContainer?: () => HTMLElement;
  local?: boolean;
}) {
  const [store] = useState(() => createFeedbackStore<Detail>());
  const [api, holder] = antdMessage.useMessage({
    maxCount: 2,
    top: local ? 12 : 72,
    getContainer,
  });
  const review = useRef<(() => void) | null>(null);
  const value = useMemo<FeedbackContextValue>(
    () => ({
      store,
      review: () => review.current?.(),
      setReview: (callback) => {
        review.current = callback;
      },
      publish: ({ type, message, key, persistent, quiet }) => {
        const summary = textOf(message);
        if (quiet || !summary) return;
        const noticeKey = key ?? `${type}:${summary}`;
        if (!store.shouldNotify(noticeKey, `${type}:${summary}`)) return;
        void api.open({
          key: noticeKey,
          type,
          duration: type === 'error' ? 6 : type === 'warning' ? 5 : 2,
          className: `studio-message${local ? ' studio-message-local' : ''}`,
          content: (
            <span className="studio-message-content" role={type === 'error' ? 'alert' : 'status'}>
              <span className="studio-message-summary">{summary}</span>
              {persistent && review.current && (
                <Button
                  type="link"
                  size="small"
                  onClick={() => {
                    api.destroy(noticeKey);
                    review.current?.();
                  }}
                >
                  查看详情
                </Button>
              )}
              <Button
                type="text"
                size="small"
                className="studio-message-close"
                aria-label="关闭提示"
                icon={<X size={14} />}
                onClick={() => api.destroy(noticeKey)}
              />
            </span>
          ),
        });
      },
    }),
    [api, store, local],
  );
  return (
    <FeedbackContext.Provider value={value}>
      {children}
      {holder}
    </FeedbackContext.Provider>
  );
}

export function useStudioMessage() {
  const context = useContext(FeedbackContext);
  if (!context) throw new Error('useStudioMessage requires StudioMessageProvider');
  return useMemo(
    () => ({
      error: (message: ReactNode, key?: string) => context.publish({ type: 'error', message, key }),
      warning: (message: ReactNode, key?: string) => context.publish({ type: 'warning', message, key }),
      success: (message: ReactNode, key?: string) => context.publish({ type: 'success', message, key }),
    }),
    [context],
  );
}

/** Renders no layout node. Recovery actions remain in the existing save/details entry. */
export function StudioFeedback({
  type = 'info',
  title,
  message,
  description,
  children,
  action,
  actions,
  persistent,
  noticeKey,
  quiet = false,
  scope,
  ...fallback
}: StudioFeedbackProps) {
  const context = useContext(FeedbackContext);
  const id = useId();
  const heading = message ?? title;
  const body = (
    <>
      {title && message && <p>{title}</p>}
      {description}
      {children}
    </>
  );
  const controls = actions ?? action;
  const summary = textOf(heading) || textOf(description) || textOf(children);
  const retain = persistent ?? (type === 'error' || type === 'warning' || !!controls);
  // chat 作用域的问题只留在对话里，不进文档保存状态面板。
  const chat = scope === 'chat';
  useEffect(() => {
    if (chat || !context || !retain || !summary) return;
    context.store.upsert({
      id,
      type,
      summary,
      content: { body, actions: controls },
    });
  });
  useEffect(
    () => () => {
      context?.store.remove(id);
    },
    [context, id],
  );
  useEffect(() => {
    if (!retain || !summary) context?.store.remove(id);
  }, [context, id, retain, summary]);
  useEffect(() => {
    context?.publish({
      type,
      message: summary,
      key: noticeKey,
      persistent: retain && !chat,
      quiet,
    });
  }, [context, type, summary, noticeKey, retain, quiet, chat]);
  if (!context || chat)
    return (
      <AntAlert
        {...fallback}
        type={type}
        message={heading}
        description={
          <>
            {description}
            {children}
          </>
        }
        action={controls}
      />
    );
  return null;
}

export function useStudioFeedbackIssues() {
  const context = useContext(FeedbackContext);
  return useSyncExternalStore(
    context?.store.subscribe ?? noopSubscribe,
    context?.store.getSnapshot ?? (() => empty),
    () => empty,
  );
}

export function useStudioFeedbackReview(open: () => void) {
  const context = useContext(FeedbackContext);
  const latest = useRef(open);
  latest.current = open;
  useEffect(() => {
    context?.setReview(() => latest.current());
    return () => context?.setReview(null);
  }, [context]);
}

export function StudioFeedbackDetails() {
  const issues = useStudioFeedbackIssues();
  return (
    <div className="studio-feedback-details">
      {issues.map((issue) => (
        <section key={issue.id} className={`studio-feedback-detail studio-feedback-${issue.type}`}>
          <strong>
            <AlertCircle size={14} />
            {issue.summary}
          </strong>
          <div className="studio-feedback-body">{issue.content.body}</div>
          {issue.content.actions && <div className="studio-feedback-actions">{issue.content.actions}</div>}
        </section>
      ))}
    </div>
  );
}

function feedbackScope(element: Element | null): HTMLElement | null {
  return (
    element?.closest<HTMLElement>('dialog[open]') ??
    element?.closest<HTMLElement>('.studio-feedback-host') ??
    element?.closest<HTMLElement>('.ant-modal, .ant-drawer-content') ??
    null
  );
}

/** Operation layers need their own host and keyboard-accessible persistent details. */
export function StudioFeedbackIndicator() {
  const issues = useStudioFeedbackIssues();
  const problems = issues.filter((issue) => issue.type === 'error' || issue.type === 'warning');
  const [open, setOpen] = useState(false);
  const entry = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      const owner = feedbackScope(entry.current);
      const active = feedbackScope(event.target instanceof Element ? event.target : null);
      if (active !== owner || (!owner?.matches('dialog[open]') && document.querySelector('dialog[open]'))) return;
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      requestAnimationFrame(() => entry.current?.focus({ preventScroll: true }));
    };
    document.addEventListener('keydown', close, true);
    return () => document.removeEventListener('keydown', close, true);
  }, [open]);
  useStudioFeedbackReview(() => setOpen(true));
  if (!issues.length) return null;
  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      trigger="click"
      placement="bottomRight"
      getPopupContainer={(trigger) => feedbackScope(trigger) ?? document.body}
      rootClassName="studio-feedback-popup"
      title="提示详情"
      content={<StudioFeedbackDetails />}
    >
      <Button
        ref={entry}
        type="text"
        size="small"
        className={`studio-feedback-indicator${problems.length ? ' has-issues' : ''}`}
        aria-label={problems.length ? `提示详情：${problems.length} 项需处理` : '任务详情'}
        aria-expanded={open}
        icon={<AlertCircle size={15} />}
      />
    </Popover>
  );
}
