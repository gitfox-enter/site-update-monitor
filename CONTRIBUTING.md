# 贡献指南

感谢你对 RSSForge 的关注！本文档介绍如何参与项目贡献。

## 如何 Fork 和提 PR

1. **Fork 仓库** — 点击 GitHub 页面右上角的 `Fork` 按钮，将项目复制到你的账号下。
2. **创建分支** — 从 `main` 分支创建特性分支：
   ```bash
   git checkout -b feature/my-new-site
   ```
3. **开发和测试** — 在本地完成修改后，确保通过测试：
   ```bash
   pip install -r requirements-dev.txt
   pytest
   ```
4. **提交代码** — 使用清晰的 commit message：
   ```bash
   git commit -m "feat: 添加 XX 站点解析器"
   ```
5. **推送并创建 PR** — 推送到你的 Fork，然后在 GitHub 上创建 Pull Request，填写 PR 模板中的必要信息。

## 添加新站点

### sites.yaml 格式

在 `sites.yaml` 的 `sites` 列表中添加新站点：

```yaml
- url: "https://example.com/"    # 站点 URL（必填）
  name: 示例站点                  # 显示名称（必填）
  tier: medium                    # 抓取优先级: high / medium / low
  interval: 60                    # 最小抓取间隔（分钟）
  max_pages: 3                    # 最大分页数
  fast_check: false               # 是否参与快速检查
  js_render: false                # 是否需要 Playwright JS 渲染
  rss_feed: ""                    # RSS 源地址（可选，有 RSS 的站点优先使用 RSS）
  parser: ""                      # 解析器标识（可选，见下方说明）
```

**tier 说明：**
- `high` — 更新频繁的线报站（15~30 分钟间隔）
- `medium` — 中频更新站点（1~2 小时间隔）
- `low` — 低频更新站点（4~12 小时间隔）

### parser 函数命名

1. 在 `crawler/parsers/` 对应模块中创建解析函数：
   - 线报/羊毛/比价站 → `deal_sites.py`
   - 软件资源站 → `software_sites.py`
   - 社区论坛 → `forum_sites.py`
   - RSS/特殊 → `rss_parsers.py`

2. 函数签名统一为：
   ```python
   def parse_example_items(soup: BeautifulSoup, url: str) -> List[Dict[str, str]]:
       """解析 example.com 页面，返回条目列表。
       
       每个条目: {'url': '条目链接', 'text': '条目标题'}
       """
   ```

3. 在 `crawler/parsers/core.py` 的 `PARSER_REGISTRY` 中注册：
   ```python
   'example.com': (parse_example_items, None),
   ```

4. 在 `crawler/parsers/__init__.py` 中添加导出。

5. （可选）如果站点在 `sites.yaml` 中配置了 `parser` 字段，引擎会使用该字段指定的解析策略，详见 `crawler/config.py` 中的 `get_parser_strategy()` 函数。

## 代码风格

- **Python 版本**：3.11+
- **类型标注**：所有公开函数必须有类型标注
- **编码声明**：文件头添加 `# -*- coding: utf-8 -*-`
- **docstring**：使用三引号文档字符串，包含功能说明和参数/返回值描述
- **命名规范**：
  - 函数/变量：`snake_case`
  - 常量：`UPPER_SNAKE_CASE`
  - 私有函数：以 `_` 开头
- **import 顺序**：标准库 → 第三方库 → 本地模块，每组之间空一行
- **行宽**：不超过 120 字符
- **字符串**：优先使用单引号，docstring 使用三双引号

## Issue / PR 模板

### 提交 Issue

请包含以下信息：
- **问题描述**：清楚描述你遇到的问题或建议
- **复现步骤**（Bug）：如何复现该问题
- **预期行为**：你期望的正确行为
- **实际行为**：实际发生了什么
- **环境信息**：Python 版本、操作系统等

### 提交 PR

请包含以下信息：
- **关联 Issue**：`Fixes #xxx` 或 `Closes #xxx`
- **修改说明**：简述本次修改的内容和原因
- **测试说明**：如何验证修改的正确性
- **影响范围**：是否影响现有功能

---

再次感谢你的贡献！ 🎉
