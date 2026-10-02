import { useMemo, type ReactNode } from 'react';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { studioTheme } from '../../app/theme';
import { useReducedMotion } from './useReducedMotion';

/** Also used by confirmations rendered outside the application React root. */
export function StudioProvider({ children }: { children: ReactNode }) {
  const reducedMotion = useReducedMotion();
  const theme = useMemo(() => ({ ...studioTheme, token: { ...studioTheme.token, motion: !reducedMotion } }), [reducedMotion]);
  return <ConfigProvider button={{ autoInsertSpace: false }} locale={zhCN} theme={theme}>{children}</ConfigProvider>;
}
