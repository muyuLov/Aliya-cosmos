/**
 * 工具函数集合
 */

/** Token 数量自动换算为可读格式 */
export function formatTokenCount(n) {
  if (typeof n !== 'number' || n < 0) return '—';
  if (n >= 1000000) return (n / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
  return String(n);
}

/** 模型名格式化：deepseek-v4-flash → Deepseek V4 Flash */
export function formatModelName(raw) {
  if (!raw || raw === '未知') return '未选择模型';
  return raw
    .split(/[-_@]/)
    .map((seg) => seg.charAt(0).toUpperCase() + seg.slice(1))
    .join(' ');
}
