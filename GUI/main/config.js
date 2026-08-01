// ========== 模型配置读取 ==========
const fs = require('fs');
const path = require('path');

const ALIYA_ROOT = path.resolve(__dirname, '..', '..');
const PROVIDERS_FILE = path.join(ALIYA_ROOT, 'data/config/LLMProviders.json');
const MAIN_YML = path.join(ALIYA_ROOT, 'data/config/main.yml');

function getCurrentProviderName() {
  try {
    const yaml = fs.readFileSync(MAIN_YML, 'utf-8');
    // 使用 llm: 锚定避免误匹配 tts 等其它 provider 块的 name 字段
    const match = yaml.match(/llm:[\s\S]*?providers:[\s\S]*?^\s+name:\s*(\S+)/m);
    return match ? match[1] : 'deepseek';
  } catch {
    return 'deepseek';
  }
}

function getModelConfig() {
  try {
    const providers = JSON.parse(fs.readFileSync(PROVIDERS_FILE, 'utf-8'));
    const providerName = getCurrentProviderName();
    const config = providers[providerName];
    return {
      provider: providerName,
      model: config?.model || 'unknown',
      url: config?.url || '',
    };
  } catch {
    return { provider: '', model: '未选择模型', url: '' };
  }
}

function getIdentity() {
  try {
    const yaml = fs.readFileSync(MAIN_YML, 'utf-8');
    const aiName = yaml.match(/^\s{4}ai_name:\s*(\S+)/m)?.[1] || 'Aliya';
    const userName = yaml.match(/^\s{4}user_name:\s*(\S+)/m)?.[1] || '';
    return { aiName, userName };
  } catch {
    return { aiName: 'Aliya', userName: '' };
  }
}

/** 解析形如 ${VAR} / ${VAR:default} 的环境变量占位值 */
function resolveEnvValue(raw) {
  const m = typeof raw === 'string' ? raw.match(/^\$\{(\w+)(?::(.*?))?\}$/) : null;
  if (!m) return raw;
  const [, varName, defaultVal] = m;
  return process.env[varName] || defaultVal || '';
}

module.exports = {
  PROVIDERS_FILE,
  MAIN_YML,
  getCurrentProviderName,
  getModelConfig,
  getIdentity,
  resolveEnvValue,
};
