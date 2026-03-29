# 交易配置 - 支持环境变量
import os

# API配置 - 优先从环境变量读取，否则使用默认值
# 生产环境应该设置环境变量，不要在代码中硬编码敏感信息
API_KEY = os.getenv('OKX_API_KEY', '7dae93fe-2a48-4f2b-a889-9dbf30601068')
SECRET_KEY = os.getenv('OKX_SECRET_KEY', '9C73FAEF39647DF6FBD06229CA4F0F4C')
PASSPHRASE = os.getenv('OKX_PASSPHRASE', 'Lxc@2026888')
BASE_URL = os.getenv('OKX_BASE_URL', 'https://www.okx.com')

# 检查是否使用了环境变量
if os.getenv('OKX_API_KEY'):
    print('[配置] 使用环境变量API配置')
else:
    print('[配置] 使用默认API配置（建议设置环境变量）')
