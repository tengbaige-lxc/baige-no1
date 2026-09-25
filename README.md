# Baige V5

白鸽五号是 Crypto 与 TradFi 分池运行的横截面多空组合策略。

当前正式版本：`5.0.0`，版本源文件为 `VERSION`。

## 仓库边界

- `backend/`：应用、策略、测试与非敏感配置。
- `docs/`：策略版本记录和上线说明。
- `deploy/`：systemd 部署清单。
- `service.env.example`：不含凭据的环境变量模板。

数据库、扫描历史、运行状态、备份、暂存文件和真实密钥均不进入 Git。

## 验证

```bash
cd /root/baige-no5/backend
set -a
. /root/baige-no5/service.env
set +a
/root/baige-no4/venv/bin/python -m py_compile \
  v5_execution.py app/services/strategy_engine.py
/root/baige-no4/venv/bin/python -m pytest -q \
  test_v5_execution.py test_v5_cross_sectional.py \
  test_v5_portfolio.py test_v5_research_archive.py test_v5_shadow.py \
  tests
```

所有新变更统一写入 `CHANGELOG.md`；建库前的历史记录见 `docs/V5-CHANGELOG.md`。
