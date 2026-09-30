import { theme, type ThemeConfig } from 'antd';

// Keep these values aligned with tokens.css for native and Ant Design controls.
export const studioTheme: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: '#4c5cc7', colorLink: '#a5b2ff', colorLinkHover: '#c0c8ff',
    colorLinkActive: '#8e9eff', colorBgLayout: '#0b0d12', colorBgContainer: '#14171f',
    colorBgElevated: '#1c202b', colorBorder: '#333b4c', colorBorderSecondary: '#292f3d',
    colorText: '#edf0f7', colorTextSecondary: '#a6afc2', colorTextTertiary: '#959fb4',
    colorTextPlaceholder: '#959fb4', colorTextDisabled: '#6c7588',
    colorSuccess: '#69cfac', colorWarning: '#e9b96d', colorError: '#f38995',
    colorInfo: '#91a5ff', borderRadius: 7, controlHeight: 34, controlHeightSM: 28,
    controlHeightLG: 38, fontSize: 13,
    fontFamily: '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
    motionDurationMid: '0.18s', motionDurationSlow: '0.24s',
    boxShadow: '0 12px 36px rgba(0, 0, 0, 0.35)',
    boxShadowSecondary: '0 16px 48px rgba(0, 0, 0, 0.45)',
  },
  components: {
    Button: { primaryShadow: 'none', defaultShadow: 'none', fontWeight: 500,
      primaryColor: '#ffffff', paddingInline: 13 },
    Input: { activeShadow: '0 0 0 2px rgba(101, 116, 239, 0.18)' },
    Select: { optionSelectedBg: '#2a304d', optionSelectedColor: '#d5dcff' },
    Table: { headerBg: '#1a1e28', headerColor: '#a6afc2', rowHoverBg: '#1e2330',
      cellPaddingBlock: 13, cellPaddingInline: 16, borderColor: '#292f3d' },
    Tabs: { titleFontSize: 13, horizontalItemGutter: 24, inkBarColor: '#8e9eff' },
    Segmented: { trackBg: '#0e1118', itemSelectedBg: '#2a3045', itemSelectedColor: '#d5dcff' },
    Drawer: { colorBgElevated: '#14171f', paddingLG: 24 },
    Modal: { contentBg: '#1c202b', headerBg: '#1c202b', titleFontSize: 16 },
    Tooltip: { colorBgSpotlight: '#2b3344', colorTextLightSolid: '#edf0f7' },
  },
};
