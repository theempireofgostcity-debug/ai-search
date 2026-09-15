# AI 情报日报

一个纯本地、零依赖的 AI 资讯聚合工具。定时抓取 12 个中英文 AI 资讯源，去重、打分、分组，生成一份可搜索可筛选的简报网页。

## 快速开始

```bash
python daily.py                 # 抓取最近 48 小时，生成 index.html
python daily.py --hours 72      # 放宽时间窗口
python daily.py --rebuild       # 用缓存 JSON 重新渲染，不联网
python daily.py --open          # 生成后用默认浏览器打开
```

只依赖 Python 标准库，无需 pip install。

## 数据源

| 分组 | 来源 | 抓取窗口 |
|---|---|---|
| 官方发布 | OpenAI、Google DeepMind、Google AI | 14 天 |
| 行业动态 | 量子位、InfoQ 中国、TechCrunch AI、The Verge AI、VentureBeat AI、MIT Technology Review | 2–4 天 |
| 研究前沿 | arXiv cs.AI | 2 天 |
| 社区热议 | Simon Willison、Hacker News | 2–4 天 |

官方博客更新频率低，因此给了更长的抓取窗口；新闻源窗口较短，保证时效性。

## 输出

- `index.html` — 当日简报（每次覆盖）
- `archive/daily-YYYY-MM-DD.html` — 按日期归档
- `data/items-YYYY-MM-DD.json` — 原始条目，可用于回溯或重新渲染

## 排序逻辑

条目得分 = 关键词命中 × 1.0 + 信源权重 × 4 + 分组加成 + 时效加成 + HN 热度加成

分组加成：`官方发布 +7 / 行业动态 +5 / 社区热议 +3 / 研究前沿 0`。
这一项是为防止 arXiv 论文靠关键词密度霸占"今日要点"——日报的头条应该是新闻，不是论文。

每个信源有条目上限（arXiv 25、新闻源 15–20），避免单源刷屏。

## 网页功能

- 实时搜索（标题、摘要、来源）
- 按分组筛选
- 今日要点按热度与时效排序，且同一信源最多 2 条，保证多样性
- 单文件、无外链脚本、无追踪

## 自定义

编辑 `daily.py` 顶部的三处配置：

- `SOURCES` — 增删信源，或调整 `weight` / `window_h` / `max`
- `TERMS` — 关键词与权重，决定相关性打分
- `GROUP_BONUS` — 各分组在排序中的加成

## 定时运行

已在 WorkBuddy 中配置每日 08:30 自动执行。如需手动改时间，调整对应的自动化任务即可。

## 用 GitHub Actions 托管（零成本，不依赖本地电脑）

本地方案要求电脑一直开着。搬到 GitHub 之后，抓取和发布都在云端完成，电脑关机也不影响。

### 结构

- `.github/workflows/daily.yml` — 定时任务，cron `30 0 * * *`（00:30 UTC = 08:30 北京时间）
- 流程：检出代码 → 装 Python → 跑 `daily.py --hours 48 --out _site` → 上传 Pages 产物 → 部署
- `_site/`、`data/`、`archive/` 均已 gitignore，仓库只存源码

### 首次部署

1. 在 GitHub 新建仓库（如 `ai-daily-brief`），选 **Public**，不要勾选 README / .gitignore
2. 在本目录执行：

```bash
git init
git add .
git commit -m "AI 日报：初始提交"
git branch -M main
git remote add origin https://github.com/<你的用户名>/ai-daily-brief.git
git push -u origin main
```

3. 仓库 **Settings → Pages → Source** 选择 **GitHub Actions**
4. 首次 push 会自动触发一次构建；之后每天 08:30 自动更新（GitHub 调度高峰期可能延迟几分钟）
5. 手机上把旧的桌面图标删掉，用新的 Pages 域名重新添加到主屏幕

想改时间就编辑 `daily.yml` 里的 cron。注意 GitHub 用的是 UTC，北京时间减 8 小时。

想手动跑一次：仓库 **Actions** 标签页 → 选 AI Daily Brief → **Run workflow**。

## 已知限制

- VentureBeat 偶发 429 限流，失败会在页脚标出，不影响其他源
- 少数站点需登录或有反爬，无法接入
- 抓取窗口内若无更新，对应信源会出现 0 条，属正常现象
