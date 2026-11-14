# STock

股票与 ETF 持仓管理、市场监测、事件分析和研究报告工作台，提供本地桌面端及托管部署方式。

## 直接下载安装

**[下载 Windows 安装包（Windows 10/11，64 位）](https://github.com/ToddQiu/STock/releases/latest)**

在发布页 Assets 中选择 `STock-Setup-1.0.4-x64.exe`，下载后双击安装，无需另装 Python 或 Node.js。`Source code` 是供开发者使用的源码。本版本未作商业代码签名，Windows 可能显示未知发布者；下载页提供 SHA-256 校验文件。

## 此版本的数据

这是清空持仓的公开发布版：不包含任何个人持仓、组合账户、本金、库存现金、应收应付、成交记录、数据库、API 密钥、导出报告或本机缓存。默认配置持仓为空、本金为零，监测列表为空。测试中的数字仅为测试夹具。

## Windows 本地启动

安装 Python 3.10 或更新版本、Node.js/npm 后，在项目目录执行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
Copy-Item config.example.json config.json
npm --prefix desktop ci
powershell -NoProfile -ExecutionPolicy Bypass -File .\start_desktop_app.ps1
```

已有本地配置时不要覆盖 `config.json`。运行数据保存在本地 `data/`，两者均已加入 Git 忽略规则。AI 分析所需凭据请在本地设置，不要提交到仓库。

## 其他运行方式

- [托管部署说明](deploy/README.md)
- [网页研究工具说明](pages/README.md)
- 桌面打包脚本：`scripts/build_desktop_package.py`；源码目录不包含第三方运行时，安装包在 GitHub Releases 下载。

`PUBLICATION-MANIFEST.json` 记录本次发布文件的 SHA-256，可用于校验源码副本。
