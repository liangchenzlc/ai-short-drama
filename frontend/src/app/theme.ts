import type { ThemeConfig } from 'antd';

// Ant Design and the native controls in web.css share this palette.
export const studioTheme: ThemeConfig = {
  token: {
    colorPrimary: '#087f75', colorLink: '#087f75', colorLinkHover: '#06685f',
    colorLinkActive: '#05564f', colorBgLayout: '#f4f7f7', colorBgContainer: '#ffffff',
    colorBorder: '#d5dfe0', colorText: '#203235', colorTextSecondary: '#5d7073',
    colorTextPlaceholder: '#657678', colorSuccess: '#207451', colorWarning: '#9a641c',
    colorError: '#b53d45', borderRadius: 8, controlHeight: 40, fontSize: 14,
    fontFamily: '"Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
  },
  components: {
    Button: { primaryShadow: 'none', defaultShadow: 'none', fontWeight: 500 },
    Table: { headerBg: '#edf2f2', headerColor: '#5d7073', rowHoverBg: '#f4f8f8' },
    Tabs: { titleFontSize: 14 },
  },
};
