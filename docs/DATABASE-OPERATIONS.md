# 数据库迁移与保留策略

## 迁移

数据库结构变更统一使用 Alembic，禁止继续在业务启动代码中追加临时 `ALTER TABLE`。

```bash
cd /root/baige-no5/backend
set -a
. /root/baige-no5/service.env
set +a
/root/baige-no4/venv/bin/python -m alembic -c alembic.ini current
/root/baige-no4/venv/bin/python -m alembic -c alembic.ini upgrade head
```

`20260925_0001` 是现有生产结构的空迁移基线。部署时先对生产库创建并校验 gzip 备份，再执行迁移。

## 数据保留

默认命令只预演，不删除数据：

```bash
/root/baige-no4/venv/bin/python manage_data_retention.py \
  /root/baige-no5/backend/state/baige_no5.db
```

当前保留规则：

- `strategy_logs` 的 `HOLD` 日志保留 7 天。
- 衍生品市场快照保留 35 天。
- 后台操作日志保留 90 天。
- 交易订单、成交台账、持仓、策略版本、用户和账户配置永久保留。
- 工具不会自动执行 `VACUUM`，避免长时间锁库。

实际清理必须显式传入 `--apply --backup-dir DIR`；工具会先生成 gzip 备份并完整解压校验，备份失败则不删除。
