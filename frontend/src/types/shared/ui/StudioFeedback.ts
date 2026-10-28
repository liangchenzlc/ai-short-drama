import type { FeedbackStore, StudioFeedbackType } from '@host/types/shared/ui/studio-feedback-store';
import type { AlertProps } from 'antd';
import type { ReactNode } from 'react';

export type Detail = { body: ReactNode; actions: ReactNode };

export type Notice = {
  type: StudioFeedbackType;
  message: ReactNode;
  key?: string;
  persistent?: boolean;
  quiet?: boolean;
};

export type FeedbackContextValue = {
  store: FeedbackStore<Detail>;
  publish: (notice: Notice) => void;
  review: () => void;
  setReview: (callback: (() => void) | null) => void;
};

export type StudioFeedbackProps = Omit<AlertProps, 'type' | 'title' | 'message'> & {
  type?: StudioFeedbackType;
  title?: ReactNode;
  message?: ReactNode;
  actions?: ReactNode;
  persistent?: boolean;
  noticeKey?: string;
  quiet?: boolean;
  children?: ReactNode;
  /** chat 作用域只在对话内展示，不进入文档保存状态面板。 */
  scope?: 'chat' | 'document';
};
