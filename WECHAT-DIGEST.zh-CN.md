# 微信自动推送（pushplus）

每天北京时间 **09:00** 和 **18:00**，GitHub Actions 会读取目录里的订阅源，抓取时间窗内的新文章，渲染成国际杂志风格的竖屏 HTML，并通过 [pushplus](https://www.pushplus.plus/) 推送到微信。

| | |
| --- | --- |
| 工作流 | [`.github/workflows/wechat-digest.yml`](.github/workflows/wechat-digest.yml) |
| 脚本 | [`scripts/wechat_digest.py`](scripts/wechat_digest.py)（仅用标准库，无需安装依赖） |
| 测试 | [`tests/test_wechat_digest.py`](tests/test_wechat_digest.py) |

## 一、准备工作

1. 打开 [pushplus](https://www.pushplus.plus/) 用微信扫码登录，在「一对一推送」页面复制 `token`。
2. 在仓库 **Settings → Secrets and variables → Actions → New repository secret** 添加：

   | 名称 | 必填 | 说明 |
   | --- | --- | --- |
   | `PUSHPLUS_TOKEN` | 是 | pushplus 个人 token |
   | `PUSHPLUS_TOPIC` | 否 | 群组编码，填了就一对多推送给群组成员 |

3. 在 **Actions** 页面确认工作流已启用。首次可以用 **Run workflow** 手动跑一次验证。

> Fork 仓库默认关闭定时任务，需要在 Actions 页面手动启用一次。

## 二、推送时间

工作流用 UTC 计时，对应北京时间（UTC+8）如下：

| 期号 | 北京时间 | cron (UTC) | 回溯窗口 | 覆盖范围 |
| --- | --- | --- | --- | --- |
| 晨间简报 | 09:00 | `0 1 * * *` | 15 小时 | 昨晚 18:00 之后的更新 |
| 晚间简报 | 18:00 | `0 10 * * *` | 9 小时 | 今早 09:00 之后的更新 |

两个窗口首尾相接，正好覆盖 24 小时，既不漏也基本不重。GitHub 的定时任务在高峰期可能延迟几分钟，属于平台正常表现。

## 三、内容规则

- **来源**：默认读取 `data/feeds.json` 里的 `top200` 集合，可换成 `ai`、`news`、`security` 等任意 OPML 集合。
- **筛选**：只保留时间窗内、带有真实发布时间的文章；每个源最多 3 条，单期最多 120 条。
- **去重**：按链接（忽略末尾斜杠与锚点）和标题双重去重。
- **排序**：按目录分类分栏，栏内按发布时间从新到旧。
- **容错**：抓取失败的源直接跳过，不影响本期推送；本时段无更新时不发送（避免打扰），可用 `--allow-empty` 改变该行为。

## 四、视觉风格

参考归藏 skills 的电子杂志排版，全部使用内联样式，保证微信 webview 正常渲染：

| 元素 | 取值 |
| --- | --- |
| 版式 | 竖屏单栏，最大宽度 640px |
| 底色 | 浅灰 `#EFEFEA`，卡片 `#F7F7F4` |
| 正文 | 黑色 `#0B0B0B`，小字 12px |
| 次要信息 | 深灰 `#3A3A38`，等宽字体 10px |
| 强调色 | 萤光绿 `#CCFF00`（刊头、栏目色块、来源标签、页脚色条） |
| 结构 | 刊头 + 发丝线分栏 + 两位数序号 + 页脚续页提示 |

## 五、10 万字限制与分页

pushplus 微信渠道单条内容上限为 10 万字（会员），标题 100 字。脚本按 **每页 92,000 字符** 的预算打包（预留刊头、页脚与包裹层空间），超出即自动分页：

- 页码显示为 `01 / 03` 这样的形式，页脚会标注「续下页」或「本期结束」。
- 文章序号跨页连续编号，栏目标题在续页顶部重复出现。
- 标题自动追加 `2/3` 页码后缀，单页时不追加。
- 每页之间间隔 2 秒发送，避免触发频率限制。

## 六、本地调试

```bash
# 只渲染不推送，把 HTML 写到 build/digest 目录，用浏览器打开即可预览
python scripts/wechat_digest.py --dry-run --out build/digest --window-hours 48

# 指定集合与期号
python scripts/wechat_digest.py --dry-run --pack ai --edition morning --out build/digest

# 真实推送（本地）
PUSHPLUS_TOKEN=你的token python scripts/wechat_digest.py --pack top200
```

常用参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--pack` | `top200` | 要读取的 OPML 集合 |
| `--edition` | `auto` | `auto` 按北京时间判断上午/下午 |
| `--window-hours` | `0` | 0 表示使用该期号的默认窗口 |
| `--max-per-feed` | `3` | 每个源最多取几条 |
| `--max-items` | `120` | 单期文章总数上限 |
| `--page-budget` | `92000` | 单页字符预算 |
| `--topic` | 环境变量 | pushplus 群组编码 |
| `--channel` | `wechat` | 也支持 `mail`、`webhook` 等 pushplus 渠道 |
| `--dry-run` | 关 | 只渲染不推送 |
| `--allow-empty` | 关 | 没有新文章时也推送 |

## 七、常见问题

**没收到推送？** 打开工作流运行日志：`items=0` 说明窗口内确实没有新文章；`pushplus code=xxx` 说明 token 或额度有问题。渲染结果也会作为 artifact 上传，保留 7 天，可下载查看。

**想改推送时间？** 修改 workflow 里的两个 cron（UTC 时间 = 北京时间 − 8 小时），同时更新 `EDITIONS` 里的 `window_hours` 让窗口继续覆盖 24 小时。

**想换风格配色？** 改 `scripts/wechat_digest.py` 顶部的调色板常量即可，测试会校验主要颜色仍然存在。
