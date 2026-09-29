# Binance USDⓈ-M 适配边界

## 当前能力

白鸽五号 `5.8.0` 为 Binance USDⓈ-M Futures 增加了第一阶段适配：

- HMAC-SHA256 签名请求；
- 实盘与 Futures Testnet 地址隔离；
- 创建或更新账户前校验 API Key、Secret 和持仓模式；
- 后台统一展示 USDT 权益、可用资金、多空持仓数量、持仓名义金额、初始保证金和浮动盈亏；
- 识别单向持仓与双向持仓模式。

官方接口依据：

- [Binance USDⓈ-M Futures](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/Introduction)
- [Binance API 文档目录](https://developers.binance.com/en/docs/catalog)
- [USDⓈ-M Futures 更新日志](https://developers.binance.com/zh-CN/docs/products/derivatives-trading-usds-futures/change-log)

## 明确未启用

本阶段没有把 Binance 接到白鸽五号实盘执行层。以下能力仍只走 OKX：

- 市价开仓、减仓和平仓；
- 杠杆与保证金模式设置；
- 交易所原生止损；
- 成交确认、订单账本与重启恢复；
- WebSocket 账户增量同步。

因此，保存 Binance 账户后只会读取，不会自动交易。账户汇总会返回
`execution_supported=false`，供前端明确显示当前边界。

## 实盘启用前置条件

第二阶段接入执行层前，必须逐项通过：

1. API 只开放读取与 USDⓈ-M 合约交易，不开放提现；建议绑定服务器出口 IP。
2. 明确账户是单向还是双向持仓；五号启用前要求双向持仓映射测试通过。
3. 使用 `exchangeInfo` 校验每个合约的数量步长、最小数量、最小名义金额和价格精度。
4. 在测试网验证开仓、只减仓、全平、重复请求幂等和成交状态查询。
5. 验证杠杆、全仓/逐仓切换失败时系统失败关闭。
6. 验证交易所原生灾难止损，包含服务重启后的查询、恢复与撤单。
7. 为 Binance 单独建立订单归属、持仓归属和止损状态，不与 OKX 账本共用订单 ID。
8. 测试通过后仍须显式启用，不能因为账户 `is_active=true` 自动接管实盘。

## 配置

默认地址：

```text
BINANCE_FUTURES_BASE_URL=https://fapi.binance.com
BINANCE_FUTURES_TESTNET_URL=https://testnet.binancefuture.com
```

可通过环境变量覆盖测试网地址。生产环境不得把测试网密钥提交到实盘地址，反之亦然。
