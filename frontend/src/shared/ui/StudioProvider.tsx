import { StudioMessageProvider } from '@host/shared/ui/StudioFeedback';
import { studioTheme } from '@host/shared/ui/theme';
import { useReducedMotion } from '@host/shared/ui/useReducedMotion';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { useMemo, type ReactNode } from 'react';
/** Also used by confirmations rendered outside the application React root. */
export function StudioProvider({ children }: { children: ReactNode }) {
  const reducedMotion = useReducedMotion();
  const theme = useMemo(
    () => ({
      ...studioTheme,
      token: { ...studioTheme.token, motion: !reducedMotion },
    }),
    [reducedMotion],
  );
  return (
    <ConfigProvider button={{ autoInsertSpace: false }} locale={zhCN} theme={theme}>
      <StudioMessageProvider>{children}</StudioMessageProvider>
    </ConfigProvider>
  );
}
