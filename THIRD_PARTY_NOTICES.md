# 第三方依赖与来源

## ReportLab

CLI PDF 导出使用 [ReportLab](https://www.reportlab.com/) Python 包，通过 `requirements.txt` 安装。本仓库没有打包该库的源代码。核心校验、计分、HTML 生成和浏览器打印不依赖 ReportLab。

## 可选的 BOSS 直聘抓取器

上游项目：[eatmoreduck/boss-zhipin-scraper](https://github.com/eatmoreduck/boss-zhipin-scraper)。本项目所参考的上游版本为 `2bc40f56a3ca3249ce3b98cdda0187e0bd612aa5`；该版本的 `LICENSE` 为 MIT，版权行为 `Copyright (c) 2026 eatmoreduck`。

本仓库没有复制该抓取器源码、Git 历史、浏览器配置或本地修改补丁。第三方实现的版权与许可证归其上游，不属于本仓库的原创实现。

`scripts/crawl_once.py` 通过子进程调用配置的 `crawl.scraper`，依赖以下 CLI 接口：

- 列表：`--keyword`、`--city`、`--pages`、`--no-detail`、`--output`，以及配置的筛选参数。
- 详情：`--input`、`--detail`、`--detail-output`。
- 输出提供 `job_id`、`job_link` 等岗位字段；列表 JSON 可以是数组，或含 `jobs` 数组的对象；详情按上游接口落盘。

安装上游时请检查版本的命令行接口、环境与登录要求，使用个人工作目录保存日志和数据。此次公开发行验证仅运行离线虚构夹具，没有验证上游当前站点连通性，也没有携带本地的断点续抓修改。

抓取控制规则属于本项目的调用边界：单轮即停、补抓须本人批准、风控与登录失效立即停止、绝不代投。
