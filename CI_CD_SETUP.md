# 安全CI/CD配置指南

## 🔐 GitHub Secrets 配置

### 1. 在GitHub仓库中添加Secrets

访问: `Settings` → `Secrets and variables` → `Actions` → `New repository secret`

添加以下secrets:

| Secret名称 | 值 | 说明 |
|-----------|-----|------|
| `OKX_API_KEY` | 你的API Key | OKX API密钥 |
| `OKX_SECRET_KEY` | 你的Secret Key | OKX API密钥 |
| `OKX_PASSPHRASE` | 你的Passphrase | OKX API密码 |
| `OKX_BASE_URL` | https://www.okx.com | API基础URL(可选) |

### 2. 本地环境变量配置

开发时在本地设置环境变量:

**Linux/Mac:**
```bash
export OKX_API_KEY='你的API Key'
export OKX_SECRET_KEY='你的Secret Key'
export OKX_PASSPHRASE='你的Passphrase'
```

**Windows:**
```cmd
set OKX_API_KEY=你的API Key
set OKX_SECRET_KEY=你的Secret Key
set OKX_PASSPHRASE=你的Passphrase
```

**Python加载:**
```python
import os
from config_secure import API_KEY, SECRET_KEY, PASSPHRASE
# 自动从环境变量读取
```

## 🚀 CI/CD工作流

工作流文件: `.github/workflows/test_secure.yml`

### 安全特性:

1. **Secrets加密** - API密钥使用GitHub Secrets存储，不会在日志中暴露
2. **环境变量注入** - CI运行时通过`${{ secrets.XXX }}`注入
3. **硬编码检测** - 自动检查代码中是否硬编码了敏感信息
4. **模拟测试** - 默认不调用真实API，只运行单元测试

## 🧪 测试运行方式

### 本地测试:
```bash
# 设置环境变量后运行
export OKX_API_KEY='xxx'
pytest test_trader_pytest.py -v
```

### GitHub Actions:
自动在push时运行，使用Secrets中的密钥

## ⚠️ 安全提醒

1. **永远不要**在代码中硬编码API密钥
2. **永远不要**将`.env`文件提交到git
3. **定期轮换**API密钥
4. **使用IP白名单**限制API访问
5. **启用2FA**保护GitHub账户

## 🔧 修改real_trader_v2.py以支持环境变量

修改导入部分:
```python
# 从config_secure导入配置
from config_secure import API_KEY, SECRET_KEY, PASSPHRASE, BASE_URL
```

这样代码会自动优先使用环境变量中的配置。
