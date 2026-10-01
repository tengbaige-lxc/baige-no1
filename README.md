# Baige V5

白鸽五号是 Crypto 与 TradFi 分池运行的横截面多空组合策略。

当前正式版本：`5.8.1`，版本源文件为 `VERSION`。

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

## 文档

- [最新策略说明](docs/V5-STRATEGY-SPEC.md)：当前实盘入口、组合、仓位、退出、轮换、已知差异与因子研究流程。
- [版本改动记录](CHANGELOG.md)：建库后的代码和策略变化。
- [历史策略记录](docs/V5-CHANGELOG.md)：建库前的策略演进与上线记录。
- [数据库操作](docs/DATABASE-OPERATIONS.md)：备份、迁移和数据保留流程。
- [Binance 适配边界](docs/BINANCE-USDM-ADAPTER.md)：USDⓈ-M 账户接入、验签、只读能力与实盘启用前置条件。

所有新变更统一写入 `CHANGELOG.md`。改变交易行为时，还必须同步更新最新策略说明和相应回归测试。
