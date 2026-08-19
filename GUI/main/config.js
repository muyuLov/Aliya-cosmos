// ========== 后端配置读取 / 安全写入 ==========
// 主配置 data/config/main.yml 是唯一配置源头（后端 core/config 持有单例）。
// 为避免 YAML 整体重写破坏注释与格式，写入一律采用"定点行替换"：
//   只替换目标键的值部分，保留该行尾注与其余所有内容。
const fs = require('fs');
const path = require('path');

const ALIYA_ROOT = path.resolve(__dirname, '..', '..');
const PROVIDERS_FILE = path.join(ALIYA_ROOT, 'data/config/LLMProviders.json');
const MAIN_YML = path.join(ALIYA_ROOT, 'data/config/main.yml');

// ========== YAML 标量读取（按 key 行匹配，兼容尾注） ==========

/**
 * 读取 main.yml 中指定缩进层级的标量值。
 * @param {string} yaml YAML 文本
 * @param {string} key  键名
 * @param {number} indent 该键所在行前导空格数
 * @returns {string|null}
 */
function readYamlScalar(yaml, key, indent) {
  const re = new RegExp(`^[ \\t]{${indent}}${key}:\\s*(\\S+)(?:\\s+#.*)?$`, 'm');
  const m = yaml.match(re);
  return m ? m[1] : null;
}

/**
 * 读取嵌套键（如 llm 块下的 providers.name），
 * 通过上级锚点字符串定位，避免同缩进同名键误匹配（如 tts.providers.name）。
 * @param {string} yaml YAML 文本
 * @param {string} anchor 上级锚点（正则片段，如 'llm:'）
 * @param {string} key 目标键名
 * @returns {string|null}
 */
function readYamlNestedScalar(yaml, anchor, key) {
  const re = new RegExp(`${anchor}[\\s\\S]*?^([ \\t]+)${key}:\\s*(\\S+)(?:\\s+#.*)?$`, 'm');
  const m = yaml.match(re);
  return m ? m[2] : null;
}

// ========== YAML 标量安全写入（定点替换，保留注释与格式） ==========

/**
 * 定点替换指定缩进层级键的值，保留行尾注释。
 * @param {string} yaml YAML 文本
 * @param {string} key 键名
 * @param {number} indent 该键所在行前导空格数
 * @param {string|number|boolean} value 新值
 * @returns {string|null} 替换后的文本；键不存在返回 null
 */
function setYamlScalar(yaml, key, indent, value) {
  const re = new RegExp(`^([ \\t]{${indent}})${key}:\\s*\\S+([ \\t]*)(#.*)?$`, 'm');
  let hit = false;
  const updated = yaml.replace(re, (_m, lead, _spaces, comment) => {
    hit = true;
    const tail = comment ? ` ${comment}` : '';
    return `${lead}${key}: ${value}${tail}`;
  });
  return hit ? updated : null;
}

/**
 * 定点替换嵌套块（如 llm.providers.name）中某键的值。
 * @param {string} yaml YAML 文本
 * @param {string} anchor 上级锚点（正则片段）
 * @param {string} key 目标键名
 * @param {string|number|boolean} value 新值
 * @returns {string|null}
 */
function setYamlNestedScalar(yaml, anchor, key, value) {
  const re = new RegExp(`(${anchor}[\\s\\S]*?^([ \\t]+)${key}:\\s*)\\S+([ \\t]*)(#.*)?$`, 'm');
  let hit = false;
  const updated = yaml.replace(re, (_m, prefix, _lead, spaces, comment) => {
    hit = true;
    const tail = comment ? ` ${comment}` : '';
    return `${prefix}${value}${spaces}${tail}`;
  });
  return hit ? updated : null;
}

// ========== 各配置项读取 ==========

function getCurrentProviderName() {
  try {
    const yaml = fs.readFileSync(MAIN_YML, 'utf-8');
    // 使用 llm: 锚定避免误匹配 tts 等其它 provider 块的 name 字段
    return readYamlNestedScalar(yaml, 'llm:', 'name') || 'deepseek';
  } catch {
    return 'deepseek';
  }
}

function getTTSProviderName() {
  try {
    const yaml = fs.readFileSync(MAIN_YML, 'utf-8');
    return readYamlNestedScalar(yaml, 'tts:', 'name') || 'edge';
  } catch {
    return 'edge';
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
    const aiName = readYamlScalar(yaml, 'ai_name', 4) || 'Aliya';
    const userName = readYamlScalar(yaml, 'user_name', 4) || '';
    return { aiName, userName };
  } catch {
    return { aiName: 'Aliya', userName: '' };
  }
}

/** 读取 Agent WebSocket 服务地址（支持 ${ENV_VAR:default} 占位符） */
function getWsEndpoint() {
  try {
    const yaml = fs.readFileSync(MAIN_YML, 'utf-8');
    const hostRaw = readYamlScalar(yaml, 'host', 8) || '127.0.0.1';
    const portRaw = readYamlScalar(yaml, 'port', 8) || '8765';
    return {
      host: resolveEnvValue(hostRaw),
      port: resolveEnvValue(portRaw),
    };
  } catch {
    return { host: '127.0.0.1', port: '8765' };
  }
}

// ========== 安全写入 ==========

/**
 * 保存身份信息（ai_name / user_name），定点写入 main.yml。
 * @param {{aiName?: string, userName?: string}} identity
 * @returns {{success: boolean, error?: string}}
 */
function saveIdentity(identity) {
  try {
    let yaml = fs.readFileSync(MAIN_YML, 'utf-8');
    const aiName = String(identity?.aiName ?? '').trim();
    const userName = String(identity?.userName ?? '').trim();
    if (aiName) {
      const updated = setYamlScalar(yaml, 'ai_name', 4, aiName);
      if (updated === null) return { success: false, error: 'main.yml 中未找到 ai_name 字段' };
      yaml = updated;
    }
    if (userName) {
      const updated = setYamlScalar(yaml, 'user_name', 4, userName);
      if (updated === null) return { success: false, error: 'main.yml 中未找到 user_name 字段' };
      yaml = updated;
    }
    fs.writeFileSync(MAIN_YML, yaml, 'utf-8');
    return { success: true, identity: getIdentity() };
  } catch (err) {
    return { success: false, error: err.message };
  }
}

/**
 * 切换当前 LLM Provider（llm.providers.name 定点写入）。
 * @param {string} providerName
 * @returns {{success: boolean, model?: object, error?: string}}
 */
function switchProvider(providerName) {
  try {
    let yaml = fs.readFileSync(MAIN_YML, 'utf-8');
    const updated = setYamlNestedScalar(yaml, 'llm:', 'name', providerName);
    if (updated === null) {
      return { success: false, error: 'main.yml 中未找到可替换的 provider name' };
    }
    fs.writeFileSync(MAIN_YML, updated, 'utf-8');
    return { success: true, model: getModelConfig() };
  } catch (err) {
    return { success: false, error: err.message };
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
  getTTSProviderName,
  getModelConfig,
  getIdentity,
  getWsEndpoint,
  saveIdentity,
  switchProvider,
  resolveEnvValue,
};
