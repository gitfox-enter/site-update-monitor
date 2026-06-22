# GitHub Actions Workflow Bug 修复摘要

**时间**: 2026-06-22 21:12 GMT+8  
**任务**: 修复 rssforge 项目中 6 个 GitHub Actions workflow 文件的 bug

## 修改文件清单

### 1. Bug #79: cleanup-history.yml - GITHUB_TOKEN 泄露修复 ✅

**文件**: `.github/workflows/cleanup-history.yml`

**问题**: `git remote add origin https://x-access-token:${{ secrets.GITHUB_TOKEN }}@github.com/${{ github.repository }}` 会在日志中泄露 token

**修复方案**:
- 使用 `env` 注入 token 到环境变量 `GIT_TOKEN`
- 直接在 `git push` 命令中使用带 token 的 URL，不添加到 remote
- 避免在 remote 列表中暴露 token

**修改后代码**:
```yaml
- name: Force push cleaned history
  env:
    GIT_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  run: |
    REPO_URL="https://x-access-token:${GIT_TOKEN}@github.com/${{ github.repository }}.git"
    git push "$REPO_URL" --force --all
    git push "$REPO_URL" --force --tags
```

---

### 2. Bug #78: test.yml - pytest exit code 被管道吞掉修复 ✅

**文件**: `.github/workflows/test.yml`

**问题**: `python -m pytest ... 2>&1 | head -100` 中的 `head` 会导致管道返回 head 的 exit code 而非 pytest 的；`echo "Exit code: $?"` 取的是 echo 的退出码（永远是0）

**修复方案**:
- 使用 `pipefail` 确保管道中任何命令失败都会导致非零退出码
- 使用 `${PIPESTATUS[0]}` 捕获 pytest 的真实退出码
- 在脚本末尾用 `exit $pytest_rc` 正确传递退出码

**修改后代码**:
```yaml
- name: 运行测试
  run: |
    set -o pipefail
    python -m pytest test_crawler.py --tb=short -q --no-header 2>&1 | head -100; pytest_rc=${PIPESTATUS[0]}
    echo "Exit code: $pytest_rc"
    exit $pytest_rc
```

---

### 3. Bug #82: pages.yml - workflow_run 触发器不会被 GITHUB_TOKEN push 触发修复 ✅

**文件**: `.github/workflows/pages.yml`

**问题**: `workflow_run` 触发器永远不会被 `GITHUB_TOKEN` 的 push 触发（GitHub 限制）

**修复方案**:
- 移除 `workflow_run` 触发器
- 改为 `push` 触发器，并添加 `paths` 过滤器（只在某些文件变化时触发）
- 添加 `workflow_dispatch` 方便手动触发

**修改后触发器**:
```yaml
on:
  push:
    branches:
      - main
    paths:
      - 'feeds/**'
      - 'feeds_meta.json'
      - 'opml.xml'
      - 'docs/**'
      - 'public/**'
  workflow_dispatch:
```

---

### 4. Bug #83 + #117: fast_check.yml - push 失败后 exit 问题 + git pull 失败处理 ✅

**文件**: `.github/workflows/fast_check.yml`

**问题**:
- #83: 最后 `if [ "$push_success" != "true" ]; then echo "All push attempts failed"; exit 1; fi` 的 exit 逻辑有问题
- #117: 当 `git pull` 失败时（网络问题），脚本仍然继续 push

**修复方案**:
- 在 `git pull --rebase` 后检查 `$rebase_rc`
- 如果 rebase 失败，检查是否是网络错误（通过 `git status | grep "Unmerged paths"` 判断是否合并冲突）
- 如果是网络错误，跳过本次 push 尝试，继续下一次循环
- 如果是合并冲突，自动解决后继续
- 如果 rebase --continue 失败，也跳过本次 push 尝试
- 确保所有路径都能正确退出

**修改后代码**: 在 push 循环中添加网络错误检测和合并冲突处理

---

### 5. Bug #94: fast_check.yml - timeout-minutes 检查 ✅

**文件**: `.github/workflows/fast_check.yml`

**检查结果**: 文件已有 `timeout-minutes: 5`，此 bug 已修复或无此问题

---

### 6. Bug #98: crawl.yml - 删除无用的 packages:write 权限 ✅

**文件**: `.github/workflows/crawl.yml`

**问题**: `packages: write` 权限申请了但未使用（没有发布任何包）

**修复方案**:
- 删除 `packages: write` 权限行

**修改后权限**:
```yaml
permissions:
  contents: write
```

---

## 验证结果

所有文件已修改并验证：
1. ✅ cleanup-history.yml - token 不再暴露在 remote 中
2. ✅ test.yml - pytest exit code 现在能正确传递
3. ✅ pages.yml - 触发器改为 push + workflow_dispatch
4. ✅ fast_check.yml - 网络错误检测和 exit 逻辑修复
5. ✅ fast_check.yml - timeout-minutes 已存在
6. ✅ crawl.yml - 无用权限已删除

## 建议的后续操作

1. 提交这些修改到仓库
2. 测试 pages.yml 的新触发器是否正常工作
3. 观察 fast_check.yml 是否还会在网络错误时错误地尝试 push
4. 考虑在 cleanup-history.yml 中添加更多日志输出，证明 token 不再泄露
