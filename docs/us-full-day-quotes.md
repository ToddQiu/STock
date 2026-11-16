# 美股持仓全天行情

持仓后台全天运行，完成一轮请求后等待 20 秒继续刷新，不按常规盘收盘时间停用。前台可见时每 10 秒读取持仓快照。

Yahoo 分钟行情与分时段报价共同提供价格；分时段接口开启 `overnightPrice=true`，夜盘使用带成交时间的 `overnightMarketPrice` / `overnightMarketTime`，来源为 Blue Ocean ATS。按成交时间选取最新有效价格，显示常规、盘前、盘后或夜盘标识。报价时段按美东时间及交易日历处理，自动适配夏冬令时和休市日。无时间戳的 `fulldayPrice` 不用于估值；来源缺失、过期或延迟时保留真实时间与状态，不把请求时间冒充成交时间。

最新有效报价用于持仓市值、浮动盈亏和总资产。美股当日盈亏继续按常规盘实际收盘周期计算。夜盘报价超过三分钟未有新成交时保留最后成交，不作为实时异动证据。

来源：[Yahoo 24/5 与 Blue Ocean 行情说明](https://www.yahooinc.com/press/yahoo-finance-expands-global-coverage-with-new-uk-based-programming-and-24-5-market-data)。全天刷新不表示所有标的每时每刻都有成交，也不表示周末和假期连续交易。
