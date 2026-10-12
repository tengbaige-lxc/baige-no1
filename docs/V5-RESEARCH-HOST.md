# 白鸽五号研究机分工

## 机器边界

- 实盘机 `43.159.130.219`：保留账户配置、交易密钥、风控、下单和生产数据库。
- 研究机 `187.77.129.235`：仅抓取 OKX 公共已收盘 K 线，运行候选因子离线检验，生成只读报告。该机器已有网站和大隐业务，研究任务必须限资源运行。
- 研究机不得持有交易密钥，不得连接生产数据库，不得调用下单接口。研究报告不自动晋级正式因子。

## 研究产物

- 代码：`/opt/baige-v5-research/`，root 拥有，只供服务读取。
- 数据：`/var/lib/baige-v5-research/candles.sqlite`，与实盘状态库隔离。
- 报告：`/var/lib/baige-v5-research/reports/latest.json`。
- 采集范围：BTC/ETH USDT 永续的 4H、30m、5m 已收盘 K 线，滚动读取最近 90 天。
- 报告明确包含数据来源、覆盖率、训练/留出段事件数、压力成本结果、局限和 `auto_apply=false`。当前不是可交易信号。

## 调度与资源

- `baige-v5-research-host-candles.timer` 每日 00:40 UTC 左右采集。
- `baige-v5-research-host-report.timer` 每日 01:10 UTC 左右生成报告。
- 两个 oneshot 服务使用独立的 `baige-research` 用户，单次最多 1 GiB 内存、50% 单核 CPU；报告服务无网络权限。
- 研究机只有 2 核且没有 swap，大规模全币种回测不得与现有网站/股票服务并发运行。

## 跨服务器交接

现阶段仅通过 SSH 人工读取研究报告，不配置研究机向实盘机推送，也不配置实盘机自动加载研究结果。后续若需要自动交接，应由实盘机使用受限只读身份拉取版本化报告，核验生成时间、文件哈希、数据覆盖率与模式字段，再交人工审查；绝不直接改策略参数或订单。

研究报告未通过留出段检验，或缺少资金费率、冲击成本、协整与真实成交验证时，只能留在研究层。

## 运维核对

```bash
systemctl status baige-v5-research-host-candles.timer baige-v5-research-host-report.timer
systemctl show baige-v5-research-host-candles.service -p Result -p ExecMainStatus
systemctl show baige-v5-research-host-report.service -p Result -p ExecMainStatus
cat /var/lib/baige-v5-research/reports/latest.json
```

暂停研究调度只需停止这两个 timer，不应停止或重启网站、大隐、股票或白鸽实盘服务。
