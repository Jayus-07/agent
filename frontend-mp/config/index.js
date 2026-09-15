// Taro 构建配置（用 .js 避免 CLI 版本差异的类型问题）
const path = require('path')

const config = {
  projectName: 'agent-mp',
  date: '2026-9-16',
  // @/ 别名（与 tsconfig paths 对齐；tsconfig 只管类型检查，webpack 走这里）
  alias: {
    '@': path.resolve(__dirname, '..', 'src'),
  },
  designWidth: 750,
  deviceRatio: {
    640: 2.34 / 2,
    750: 1,
    828: 1.81 / 2,
    375: 2,
  },
  sourceRoot: 'src',
  outputRoot: 'dist',
  plugins: [],
  defineConstants: {},
  copy: {
    patterns: [],
    options: {},
  },
  framework: 'react',
  compiler: 'webpack5',
  mini: {
    // 开发联调时后端在 http://127.0.0.1:9080（APISIX），
    // 微信开发者工具需勾选「不校验合法域名」
  },
  h5: {},
}

module.exports = function (merge) {
  if (process.env.NODE_ENV === 'development') {
    return merge({}, config, {})
  }
  return merge({}, config, {})
}
