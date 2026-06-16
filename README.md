# Site Update Monitor

自动化多站点内容监控，基于 GitHub Actions 定时爬取、检测更新并聚合展示。

## 功能

- **47 个站点监控** — 线报、优惠券、软件资源等，支持 WordPress / Discuz / RSS / 自定义 CMS
- **智能爬取** — 防检测 UA 轮换、域名级限速、指数退避重试、断路器保护
- **数据去重** — MD5 指纹比对，最多保留 1500 条记录，自动清理
- **RSS 输出** — 标准 RSS 2.0 feed，支持订阅
- **SPA 前端** — 搜索、分类筛选、分页，数据通过 Gist 动态加载
- **CI/CD** — 自动化测试、定时爬取、GitHub Pages 部署

## 爬取频率

| 任务 | 频率 | 站点数 |
|------|------|--------|
| 全量爬取 | 每天 3 次（8:00 / 13:00 / 18:00 北京时间） | 47 |
| 快速检测 | 每 30 分钟（9:00–21:00 北京时间） | 12 |

## 项目结构

```
crawl.py / fast_check.py    爬取入口
common.py                   公共工具
blacklist.json              黑名单配置
sites.yaml                  站点配置
crawler/                    爬虫核心模块
  ├── parsers/              站点解析器（插件式注册）
  ├── config.py             配置加载
  ├── engine.py             爬取引擎
  ├── network.py            网络请求
  └── storage.py            数据持久化 + Gist 同步
public/                     前端静态文件
  ├── css/app.css
  ├── js/app.js
  └── ..
.gist/                      Gist 配置
tests/                      测试用例
```

## 添加新站点

1. 在 `sites.yaml` 中添加站点 URL 和类型
2. 在 `crawler/parsers/` 下对应模块中注册解析器
3. 运行测试确认通过

## 在线演示

👉 [线报聚合](https://gitfox-enter.github.io/site-update-monitor/)

## License

MIT
