/**
 * Naive UI 主题定制
 * 将 Naive UI 组件色板对齐现有 --rb-* 设计令牌（粉紫渐变玻璃质感），
 * 与 tokens.css 的深紫基底保持一致。
 */
import { darkTheme } from 'naive-ui';

export const themeOverrides = {
  common: {
    primaryColor: '#ec4899',
    primaryColorHover: '#f472b6',
    primaryColorPressed: '#db2777',
    primaryColorSuppl: '#ff6ec7',
    infoColor: '#9f7aea',
    infoColorHover: '#b794f4',
    infoColorPressed: '#7c5fcc',
    successColor: '#22c55e',
    successColorHover: '#4ade80',
    warningColor: '#f59e0b',
    errorColor: '#ef4444',
    errorColorHover: '#f87171',
    bodyColor: 'rgba(15, 13, 31, 0.96)',
    cardColor: 'rgba(255, 255, 255, 0.05)',
    modalColor: 'rgba(24, 20, 50, 0.98)',
    popoverColor: 'rgba(24, 20, 50, 0.98)',
    textColorBase: '#ebe5f5',
    textColor1: '#fef7ff',
    textColor2: '#ebe5f5',
    textColor3: '#a094c1',
    borderColor: 'rgba(236, 72, 153, 0.18)',
    dividerColor: 'rgba(236, 72, 153, 0.14)',
    borderRadius: '8px',
    fontSize: '13px',
    fontFamily: '"Microsoft YaHei", "PingFang SC", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  },
  Card: {
    color: 'rgba(255, 255, 255, 0.05)',
    borderColor: 'rgba(236, 72, 153, 0.18)',
    borderRadius: '16px',
  },
  Input: {
    color: 'rgba(255, 255, 255, 0.06)',
    colorFocus: 'rgba(255, 255, 255, 0.08)',
    border: '1px solid rgba(236, 72, 153, 0.18)',
    borderHover: '1px solid rgba(236, 72, 153, 0.34)',
    borderFocus: '1px solid #ec4899',
    boxShadowFocus: '0 0 0 2px rgba(236, 72, 153, 0.18)',
    textColor: '#ebe5f5',
    placeholderColor: '#6b6388',
  },
  Button: {
    colorPrimary: 'linear-gradient(135deg, #ff6ec7 0%, #ec4899 50%, #9f7aea 100%)',
    colorHoverPrimary: 'linear-gradient(135deg, #ff8ed2 0%, #f472b6 50%, #b794f4 100%)',
    colorPressedPrimary: 'linear-gradient(135deg, #ec4899 0%, #db2777 50%, #7c5fcc 100%)',
    textColorPrimary: '#ffffff',
    fontWeight: '500',
    borderRadiusMedium: '8px',
  },
  Tag: {
    borderRadius: '9999px',
  },
  Tabs: {
    tabTextColorActiveLine: '#f472b6',
    tabTextColorHoverLine: '#f472b6',
    tabColorSegment: 'rgba(255, 255, 255, 0.04)',
    tabTextColorActiveSegment: '#fef7ff',
    tabColorActiveSegment: 'rgba(236, 72, 153, 0.16)',
    tabBorderColorSegment: 'rgba(236, 72, 153, 0.18)',
  },
  Select: {
    peers: {
      InternalSelection: {
        color: 'rgba(255, 255, 255, 0.06)',
        colorActive: 'rgba(255, 255, 255, 0.08)',
        border: '1px solid rgba(236, 72, 153, 0.18)',
        borderHover: '1px solid rgba(236, 72, 153, 0.34)',
        borderFocus: '1px solid #ec4899',
        boxShadowFocus: '0 0 0 2px rgba(236, 72, 153, 0.18)',
        textColor: '#ebe5f5',
        placeholderColor: '#6b6388',
      },
    },
  },
  Alert: {
    borderRadius: '12px',
  },
  Divider: {
    color: 'rgba(236, 72, 153, 0.14)',
  },
};

export { darkTheme };
