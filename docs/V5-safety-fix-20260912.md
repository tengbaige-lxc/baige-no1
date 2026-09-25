# 白鸽五号执行安全修复（2026-09-12）

## 已确认根因

- 五号成交时原生止损为价格反向约 2%。三号的 `analyzer_service.sync_live_positions` 随后将同账户五号仓位补录为孤儿仓，并用 `ORPHAN_BACKSTOP_FALLBACK_PCT=0.03` 撤旧重挂。已用两个服务的同标的日志和交易所 algoId 对照确认。
- 五号退出配置使用 `divergence_exit`、`closed_candle_only`，引擎实际读取 `top_divergence`、`require_closed_candle`。
- 缓存复核仅排除部分失败原因，漏掉 `missing_structure_confirmation`；组合排序沿用触发时历史分，掩盖当前分下降。
- 同向相关性只检查当轮候选，不检查账户已有仓位。

## 修复内容

1. 三号增加 `RECONCILE_EXTERNAL_ACCOUNT_IDS=1`，已转交五号的 openclaw 账户只保留后台读取，不再自动补账或覆盖保护单。三号其他账户不受此开关影响；三号所有策略继续暂停。没有删除或重写历史账本。
2. 五号交易所止损覆盖校验检查标的、方向、状态、数量、触发价、市价执行及 reduceOnly。每次新开仓前和运行期间检查五号账本归属持仓。校验失败时禁止新增；保护检查异常不阻断软件退出检查。
3. 重挂保护单采用“读取旧单、挂新单、交易所确认、新旧 ID 比对、撤旧单”的顺序。查询、挂单或确认失败时保留旧保护。减仓后读取仓位失败也不撤旧单。
4. 配置正确接入：单级 5m 反向背离按现有动量确认规则减 15%，5m+30m 共振减 25%，已确认三卖强退出保持；趋势线失效须 30m 收盘确认。软件 1.5% 和交易所约 2% 价格止损保持，不放宽。
5. 缓存仍保留触发窗口，但当前评分必须达到开仓门槛 6.5，且不能出现缺结构等硬性失败。排序和下单使用当前与历史分的较低值，另保留 trigger_score/revalidation_score 供审计。频率可能下降，这是清除失效信号，并非提高首次触发门槛。
6. 新候选对照账户已有的同资产类别、同经济方向持仓计算已收盘 30m 收益相关性；达到 0.85 或数据不足时拒绝叠仓。已有仓位不会仅因为相关性高就被强平。
7. 五号退出管理要求明确的五号账本归属，不再默认接管没有归属的人工仓。

## 验证

- 隔离目录：`/root/baige-no5/staging/v5-safety-20260912`。
- 项目实际 venv，隔离测试数据库，77 项测试通过；11 项 Pydantic 弃用警告。
- 三号及五号 Python 语法检查通过；部署文件 SHA256 与本机一致。
- 部署启动时间：2026-09-12 10:11:47 UTC。两个服务 active、health healthy；五号 execution_enabled/new_entries_enabled=true。
- 部署后六笔现有仓位各有一个 live 市价 reduceOnly 保护单，覆盖校验全部通过：

| 标的 | 方向 | 数量 | 原生止损价 |
| --- | --- | ---: | ---: |
| DELL | 多 | 0.11 | 554.70 |
| IBM | 多 | 0.25 | 237.99 |
| UNH | 空 | 0.16 | 383.79 |
| META | 多 | 0.09 | 637.92 |
| MINIMAX | 空 | 1.8 | 33.89 |
| NOK | 多 | 5.6 | 10.839 |

## 备份与哈希

备份：`/root/baige-no5/backups/v5-safety-20260912T100710Z`。三号/五号数据库分别通过 SQLite backup 获取一致快照、quick_check=ok、gzip 压缩并 gzip -t 校验，保留源码和原五号配置。四号未修改。

| 文件 | SHA256 |
| --- | --- |
| v5_execution.py | 5957d4b0693c72002d207e453c1d336da95b0f13eb3735496639e222c2faab13 |
| v5_main.py | f2fa925f20bee014a6179991be2313381872ebd509f36a6e8728d5936090a3dd |
| v5_portfolio.py | f02b6647e89cea24dc350e132aa2d665d85b3b062e09f8d22feb601a86da888f |
| v4_candidate_window.py（五号独立副本） | 5f7f046f44e0f6802146eea4dc0208040ece40c47c695b91d8c1fecc3af34fe6 |
| 五号 strategy_engine.py | ffdb23023f8a740a2c36d34173cffb6171dd77018d1509cbf0ad1ed0a9c5fa38 |
| native_stop_validation.py | 93fd2d5b7001b9c80e927c75d786310b47b760544a7b364950610d66f43e9576 |
| 三号 analyzer_service.py | 5055c0a196cd12fc7727af9edc2eaa0c19d1a8af0509e6c95904eac32d621834 |

## 限制

这次是执行安全与配置接线修复，不是收益验证。3 笔已平亏损不足以判定策略长期失效，也不足以证明放宽止损会更好。30m 样本相关性不是完整组合 Beta/VaR；3% 单腿风险预算不含跳空、滑点、手续费等实际偏差。三号被错误补录的历史记录未擅自删除，未来统计须以五号独立账本与交易所成交为准。
