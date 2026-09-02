# 参与贡献

[English](CONTRIBUTING.md)

感谢你帮助更多人发现值得长期订阅的内容。好的贡献应当范围清楚、可以验证，并尊重内容发布者。

## 推荐订阅源

你可以提交 **Feed suggestion** Issue，或直接修改 `data/feeds.json` 并发起 Pull Request。请提供：

- 公开可访问的 RSS、Atom 或 JSON Feed 地址；
- 发布者的网站地址；
- 最合适的分类和下载包；
- 一段具体的推荐理由；
- 当前能够解析且至少含一篇内容的验证证据。

我们优先收录原创报道、一手科研内容、从业者博客、官方项目博客，以及具有明确编辑定位的视频和播客。抓取镜像、需要凭据的私有源、垃圾内容、SEO 内容农场、搬运站和以推广返利为主的来源不会被收录。

中文独立博客需要在最近 180 天内有更新，至少返回两篇带可靠日期的文章，并通过多轮 Tidings 解析。`tidings-blogs.opml` 最多保留 400 个，全集最多 730 个；达到上限后，新源需要比现有源更活跃或更有阅读价值。

社区源应使用官方订阅地址，或有公开说明的备用地址，近期仍有讨论，并连续通过三轮 Tidings 解析。Feed 能正常导入，不代表 Tidings 一定能抓取完整回帖；推荐时请把这两项能力分开说明。

安全、科技媒体和技术周刊同样需要连续通过三轮 Tidings 解析，并返回带可靠日期的近期内容。只换栏目名、但内容高度重复的 Feed 不会同时收录；板块源需要有明确的独立用途。

热榜、聚合页和行情类源必须有真实可订阅的 Feed 地址：优先官方 RSS；没有官方 Feed 时，使用有公开说明的桥接路由（例如 RSSHub），并写清这条 Feed 承载的是哪个榜单。只渲染 HTML 的聚合站——NewsNow、REBANG，以及需要登录的投研平台（如萝卜投研）——不写入目录，只在本轮报告里记为未收录候选并说明原因。桥接路由若只能给出榜单快照时间、拿不到单条发布日期，也要在报告中写明。

微信公众号需要能快速响应、提供近期文章并通过 Tidings 解析。大厂技术号请注明机构和技术方向；同一机构、同一方向只保留一个源，有官方独立网站 RSS 时优先提交官网源。

精选 200 是全集中的严格子集，由 `tools/select_top200.py` 生成。它必须覆盖全部主分类，默认对同一发布者去重；每个入选源还要连续通过三轮 Tidings 解析，三轮都能读到文章和真实发布日期。

复现当前精选包：

```bash
python tools/select_top200.py --date 2026-08-13 \
  --candidate-snapshot reports/top200-candidates.json \
  --output reports/top200-curation.json \
  --validation reports/top200-validation-round-1.json \
  --validation reports/top200-validation-round-2.json \
  --validation reports/top200-validation-round-3.json \
  --video-validation reports/top200-video-validation-round-1.json \
  --video-validation reports/top200-video-validation-round-2.json \
  --video-validation reports/top200-video-validation-round-3.json \
  --apply
```

复现 2026-09-03 热榜与行情这一轮：

```bash
python tools/merge_hotlist_sources.py --date 2026-09-03 \
  --curated sources/hotlist-curated.json \
  --output data/feeds.json \
  --report reports/hotlist-curation.json \
  --summary reports/validation-summary.json
python scripts/catalog.py generate
python tools/generate_source_appendix.py
```

## 更新生成文件

只需要 Python 3.10 或更高版本，无需安装第三方依赖：

```bash
python scripts/catalog.py generate
python scripts/catalog.py check
python -m unittest discover -s tests -v
```

请勿手工编辑 `opml/` 或 `reports/catalog-summary.md`，它们都由 `data/feeds.json` 确定性生成。

## 删除或纠正订阅源

如果订阅源已经永久失效、被劫持、Feed 端点本身被付费墙阻断、内容为空，或已不再代表所列发布者，欢迎提交删除 PR。单次超时不足以证明永久失效，请附上可复核证据和检查日期。

## 权利与隐私

本项目只整理公开订阅端点，不转载文章正文。提交原创目录元数据即表示同意以 CC0-1.0 贡献；Feed 内容和发布者名称仍归各自权利人所有。
