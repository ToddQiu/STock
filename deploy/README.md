# STock 完整网站部署

这套部署运行现有桌面版的全部网页、Python API、采集器和 SQLite，不是 GitHub Pages 的 25 公司研究页。

## 当前边界

- 全部浏览器模块沿用原实现：总览、持仓/本金/盈亏、基金、事件中心、行情/K 线、四类资产、四大指数、股票池、社交与 Trump、策略卡、ETF、财报、研究记录与设置。
- 模块存在不代表来源总能返回数据。免费源可能限流、延迟或不可达；缺少授权的社交源和模型 API 保留明确状态，不制造数据。
- 英译中运行时及模型在镜像构建时从公开来源下载并核对 SHA256，不上传本机翻译历史。
- Windows 系统弹窗、窗口最小化和桌面快捷方式不属于网页能力。网站内保留事件中心；不会让访客触发服务器的桌面弹窗。
- **这是受邀共享工作区，不是多用户 SaaS。拿到同一访问密码的朋友可以查看、修改、导出同一个工作区。不要往共享实例填私人持仓或私人研究。** 每个人需要私人资产时，部署独立域名、独立 Compose project 和数据卷，不能共用密码冒充账号隔离。
- 新实例从空持仓、零本金开始，不读取原桌面 config.json、数据库、密钥或研究笔记。

## 部署条件

需要一台能运行 Docker Compose 的 Linux 服务器、一个解析到服务器的域名、开放 80/443。无需购买行情 API。GitHub Pages 不能运行这个 Python 后台。

### 已确认的阿里云准备方向

用户已同意按阿里云中国香港节点准备，但**未授权购买或扣费**。先评估轻量应用服务器；配置目标为 x86-64、4 核/8 GB、Ubuntu 24.04 LTS、至少约 70 GB 系统盘，先按 1 个月核价，不开启自动续费。此为初始资源评估，不是性能承诺；最终以套餐可选项、源站连通性和负载验证为准。

采购确认必须包含：具体规格族与线路、首期实付、续费、流量或超额费用、快照/备份费用、总额。套餐不合适时再比较同配置 ECS，不自动购买 GPU、RDS、付费证书、负载均衡或模型套餐。

2026-10-16 实际打开官方购买入口后被要求登录，未取得账号内报价，未创建订单。下一步是所有者登录后核价，不使用第三方活动文章的价格代替订单金额。

官方依据：[产品页](https://www.aliyun.com/product/swas)、[计费项](https://help.aliyun.com/zh/simple-application-server/product-overview/billable-items)、[Docker 与 Compose 部署](https://www.alibabacloud.com/help/zh/simple-application-server/use-cases/manually-deploy-docker)。

先完成安全组、域名解析和适用的备案/内容授权检查。以下命令用于服务器，不能视为已执行成功。费用、实例购买仍由所有者确认。

## 启动

1. 拉取仓库，进入根目录。仓库中的 `config.example.json` 是脱敏模板；不要替换成本机个人配置。

```bash
git clone https://github.com/ToddQiu/STock.git
cd STock
export STOCK_DOMAIN=app.your-real-domain.com
docker compose -f deploy/compose.yaml build
```

2. 生成随机访问密码。目录仅所有者可访问；挂载的单个文件允许容器内非 root 用户读取。密码不会进入镜像或 Git。不要把输出粘到公开页面。

```bash
set -euC
install -d -m 700 deploy/secrets
test ! -e deploy/secrets/access-key.txt
docker run --rm --entrypoint python stock-full:local \
  -c "import secrets; print(secrets.token_urlsafe(32))" > deploy/secrets/access-key.txt
chmod 444 deploy/secrets/access-key.txt
```

若密码文件已存在，保留原文件，跳过生成步骤；不要重定向覆盖它。

3. 启动 HTTPS 和完整后台：

```bash
docker compose -f deploy/compose.yaml up -d
docker compose -f deploy/compose.yaml ps
```

访问 `https://` 加你的真实域名。浏览器要求用户名 `stock`，密码从服务器上的 `deploy/secrets/access-key.txt` 读取。分享网址时私下另发密码；不要把密码放在 URL 中。Basic 登录由浏览器保存，没有账号注册或忘记密码流程；共享设备请使用独立浏览器配置并关闭会话。

## 验收

- 未登录：网页、所有 API、导出文件都应返回 401，而非私人数据。
- 登录后：首页必须是完整 STock，总览、我的资产、市场监测、研究复盘、设置均可到达。
- 新增一条测试持仓，调整本金，查看盈亏，再删除；重启后确认配置和研究记录仍保留。
- 各数据页面检查来源及时间。等首次采集完成后验证新闻、行情、财报和图表，不以有菜单代替数据验证。
- 用另一个浏览器验证同一密码是共享工作区；不同私有实例之间应完全隔离。
- 核查域名 HTTPS 证书、手机实际网络和反向代理，不只检查本机端口。

## 数据与回退

SQLite、快照、配置和研究记录保存在 `workspace` 命名卷。升级先停止服务并备份整个卷，避免运行中直接复制 SQLite。密码和 HTTPS 证书另行安全备份。

`docker compose -f deploy/compose.yaml down` 停止服务但保留数据；**不要附加 `-v`，那会删除数据卷**。回退到先前 Git 提交、重建镜像并重新启动。未验证数据库向后兼容时，应恢复对应版本的完整备份。

Python 8080 端口仅在 Docker 网络内，不能添加公网端口映射。前端和 API 同源，Origin/Host 检查仍启用；桌面原来的本机访问限制没有改动。

## 当前验证状态

见 `VERIFICATION.md`。没有真实服务器、HTTPS 验收和外部网络验证前，不能把本部署包称为完整网站已经上线。
