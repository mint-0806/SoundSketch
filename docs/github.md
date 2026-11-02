# 上传到 GitHub

建议仓库名为 `SoundSketch`，描述为：

> Natural-language-driven controllable noise synthesis and auditory expression agent. LLM planning + local Python DSP + interactive listening feedback.

## 本地检查

仓库应包含源码、文档、测试和配置模板。`data/`、`artifacts/`、`.env`、录音、日志和构建产物已加入 `.gitignore`；现有文件不会因此被删除。若使用了自定义数据目录，应将其另外加入忽略规则。

```bash
git status --short
git diff --cached --stat
```

已被 Git 跟踪的文件不会因为新增忽略规则而自动排除。发布前检查待提交文件，不要提交原始录音、会话 JSON、API 密钥或本机凭据。

## 首次上传

在 GitHub 创建一个空仓库，选择公开或私有；因为项目已经包含 README、许可证和忽略规则，不要再让 GitHub 自动生成它们。然后在本地项目目录执行：

```bash
# 仅在目录尚未初始化时运行
git init -b main

git add .
git diff --cached --stat
git commit -m "Initial SoundSketch agent demo"

# 将下面的占位地址替换为实际仓库地址
git remote add origin https://github.com/YOUR_ACCOUNT/SoundSketch.git
git push -u origin main
```

如果已有初始提交，可跳过 `git add` 和 `git commit`。Git 作者信息由你在本机配置；本项目不设置或保存你的 GitHub 凭据。本文只是操作说明，整理本地仓库不会自动创建远程仓库或上传文件。

## 不使用 Git 命令时

可以解压项目的纯源码 ZIP，在 GitHub 网页上传解压后的文件。`.github/workflows/` 等点号目录也需要包含。不要直接上传整个工作目录，以免包含本地数据。网页上传单个 ZIP 只会得到一个压缩文件，不会展开为仓库目录。

## 上传后

GitHub Actions 会在 push / pull request 后运行测试，也可手动触发。当前配置在 Windows 与 Ubuntu、Python 3.12 上安装包并运行离线测试；不需要设置模型 API 密钥。首次运行结果才代表远程 CI 状态。

后续修改前运行 `python -m pytest -q`。更新模型配置时只修改 `.env.example` 的说明，实际密钥放环境变量。
