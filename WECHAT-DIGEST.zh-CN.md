# 微信自动推送（pushplus）

[English](WECHAT-DIGEST.md)

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
   | `DEEPSEEK_API_KEY` | 否 | DeepSeek 的 Key；只填这一个就能启用 `llm` 通道（接口和模型由预设给出） |
   | `TRANSLATE_LLM_API_KEY` | 否 | 其他任意 OpenAI 兼容接口的 Key |
   | `DEEPL_API_KEY` | 否 | DeepL 的 Key，免费版（`…:fx`）或专业版都可以 |
   | `MYMEMORY_EMAIL` | 否 | 填写邮箱可提高 MyMemory 的匿名额度 |

   `TRANSLATE_LLM_PROVIDER`、`TRANSLATE_LLM_BASE_URL`、`TRANSLATE_LLM_MODEL`、`TRANSLATE_PROVIDERS` 属于仓库 **变量**（**Settings → Secrets and variables → Actions → Variables**），不是 secret。见[四、翻译通道](#四翻译通道)与[配置 llm 通道](#配置-llm-通道)。

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
- **双语**：原文是英文的标题与摘要会保留原文，并在下方附加简体中文译文，一张卡片中英对照。标题译文前带萤光绿「译」标记，摘要译文用一条萤光绿竖线引出，和原文明显区分；中文原文保持原样，不会被翻译成英文。
- **翻译**：译文来自一组翻译通道，而不再只依赖单个接口：先试配置了 Key 的通道，再依次试免密的公开接口。某个通道失败、超时或返回内容和原文相同就换下一个；全部不可用时卡片只显示原文，推送不会因此中断。可用 `--no-translate` 关闭，关闭后全部只显示原文。是否需要翻译按文本里中英文字符的比例判断，所以“英文标题里夹了一个中文产品名”仍会附加译文，而“中文句子里带英文品牌名”保持原样；纯网址、纯数字和版本号不送翻译。
- **翻译结果上报**：每次运行都会说明是哪个通道给出的译文——日志里的 `translation: translated=17/24 provider=edge retired=google`、运行页面上的 notice（有通道被弃用或全部失败时附带 warning）、渲染页面同目录的 `translation-report.md` 与 `translation-report.json`，以及运行摘要里的同款表格。
- **容错**：抓取失败的源直接跳过，不影响本期推送；本时段无更新时不发送（避免打扰），可用 `--allow-empty` 改变该行为。

## 四、翻译通道

英文标题与摘要按顺序尝试翻译通道，谁先给出译文就用谁。配置了 Key 的通道排在最前面，因为它们不依赖公开接口对 GitHub 运行器 IP 的“脸色”——这也正是旧版单一接口丢掉全部译文的原因。

| 通道 | Key | 接口 | 说明 |
| --- | --- | --- | --- |
| `deepl` | `DEEPL_API_KEY` | 免费 Key 用 `api-free.deepl.com`，专业版用 `api.deepl.com` | 机器翻译质量最好，免费额度每月 50 万字符 |
| `llm` | 任意厂商的 Key，或 `TRANSLATE_LLM_API_KEY` | 预设接口，或 `TRANSLATE_LLM_BASE_URL` + `TRANSLATE_LLM_MODEL` | 任意 OpenAI 兼容接口：DeepSeek、OpenAI、通义千问/DashScope、Moonshot、智谱、OpenRouter 等；最推荐先配这个 |
| `google` | — | `translate.googleapis.com` | 旧版用的公开 gtx 接口，对机房 IP 限流较严 |
| `google-chrome` | — | `clients5.google.com` | Google 词典客户端接口，独立服务、独立额度 |
| `edge` | — | `edge.microsoft.com/translate/translatetext` | 微软 Edge 的免密接口，替代已下线的 `translate/auth` + `api-edge` 流程 |
| `mymemory` | `MYMEMORY_EMAIL`（可选） | `api.mymemory.translated.net` | 免费翻译记忆库，每日额度较小 |

运行时的行为：

- 连续失败两次的通道会在本次运行中被**弃用**，所以一个被墙的接口只消耗几次超时，而不是每条文章都等一次。
- 给出译文的通道会**优先**被继续使用，其余通道暂时跳过；该通道开始失败时会自动换回其他通道。
- 默认按上表顺序尝试；可用仓库变量 `TRANSLATE_PROVIDERS` 或 `--translators` 指定顺序，例如 `llm,google,edge`。
- 只有超时、限流、连接失败会重试；返回内容无法解析时不重试。
- 一个 Key 都不配也能工作（走免密接口）；配了 Key 只是让结果更稳定。

### 配置 llm 通道

`llm` 就是一个 OpenAI 兼容对话接口，所以**只填一个厂商 Key 就能用**：接口地址和当前可用的模型由预设给出。

| 预设 | Secret | 接口 | 模型 |
| --- | --- | --- | --- |
| `deepseek` | `DEEPSEEK_API_KEY` | `https://api.deepseek.com/v1` | `deepseek-flash` |
| `openai` | `OPENAI_API_KEY` | `https://api.openai.com/v1` | `gpt-4o-mini` |
| `dashscope` | `DASHSCOPE_API_KEY` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| `moonshot` | `MOONSHOT_API_KEY` | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| `zhipu` | `ZHIPUAI_API_KEY` | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| `openrouter` | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` | `openai/gpt-4o-mini` |

```text
# DeepSeek：只填一个 Secret，不用再配任何变量
DEEPSEEK_API_KEY = sk-…

# 同一个 Key 用通用名字也能命中 DeepSeek 预设（接口地址会说明身份）
TRANSLATE_LLM_API_KEY = sk-…
TRANSLATE_LLM_BASE_URL = https://api.deepseek.com/v1
```

DeepSeek 的接口变化比别的厂商频繁，所以预设额外做了几件事：

- 预设会显式要求**非思考模式**（`"thinking": {"type": "disabled"}`）。DeepSeek 默认开启思考模式，而翻译不需要思维链；思考模式还会忽略 `temperature`，并按推理 token 计费。
- 预设模型是 `deepseek-flash`（DeepSeek-V4.1-Flash）。旧的 `deepseek-chat`、`deepseek-reasoner` 已于 **2026-07-24 下线**；如果变量里还留着旧名字，运行时会以 warning 注解直接提示，而不是只显示一个失败的通道。
- 接口地址按常见的写法都能识别：`https://api.deepseek.com`、`https://api.deepseek.com/v1/`、甚至完整的 `…/v1/chat/completions` 都会归一到同一处；只有主机名时，DeepSeek 和 OpenAI 会自动补上 `/v1`。
- 也可以显式点名：`TRANSLATE_LLM_PROVIDER=deepseek` 配合 `TRANSLATE_LLM_API_KEY` 使用同一套预设。名字写错会直接报错退出，而不是悄悄少一个通道。
- `TRANSLATE_LLM_BASE_URL` 和 `TRANSLATE_LLM_MODEL` 的优先级永远高于预设，所以「网关 + DeepSeek」和任何其他接口一样配置。

这些都不是必需的：不认识的接口地址会按原样使用，Key 配错厂商只会像其他失效通道一样失败——而运行报告里的「Configuration」段落会写明实际用的是哪一个变量、哪个接口、哪个模型。

### 检查哪些接口可用

`Translation probe`（[`.github/workflows/translate-probe.yml`](.github/workflows/translate-probe.yml)）会跳过链路，直接逐个调用所有通道，并原样记录每个通道的返回或报错。在 **Actions → Translation probe → Run workflow** 手动运行，约一分钟即可，结果会打印到运行页面、作为 `translation-probe` artifact 上传，也可以顺带作为评论发到某个 issue 或 PR（`comment_on` 输入项）。只要 PR 改动了翻译链路，它还会自动跑一次并把同一张表评论到该 PR 上——因为「Key 在 GitHub 运行器上能不能用」是离线测试唯一答不了的问题。

报告最后有一段 **Configuration**：注明 Key 来自哪个变量、指向哪个接口、会调用哪个模型（Key 本身从不打印）。表里少了某个通道时，多半就是这一段能给出原因。

```bash
# 同样的探测在本地跑，报告写入 build/probe/
python scripts/wechat_digest.py --probe-translators --out build/probe

# 只探测某个通道
python scripts/wechat_digest.py --probe-translators --translators edge --timeout 10
```

| 通道 | 类型 | 原文 | 状态 | 耗时 | 返回 |
| --- | --- | --- | --- | ---: | --- |
| `google` | 免密 | OpenAI ships a smaller model… | error | 0.31 | HTTPError: 429 Too Many Requests |
| `edge` | 免密 | OpenAI ships a smaller model… | ok | 0.42 | OpenAI 推出可在设备端运行的更小模型 |

## 五、品牌与文案

推送标题、副标题和说明都定义在 `scripts/wechat_digest.py` 顶部：

| 位置 | 文案 |
| --- | --- |
| 标题 | 章鱼 AI · 营销热点助手 |
| 副标题 | 每天三分钟！去噪音！留干货！ |
| 说明 | 全网兼顾中文和英文内容，覆盖 AI、工程、安全、科技媒体、周刊、科研、新闻、产品、商业、博客、社区、文化、视频和播客。 |
| 署名 | 作者：章鱼 ai · 仅供参考，分析研究 |

标题不带日期，也不带 pushplus 字样；多页时自动追加 `2/3` 这样的页码后缀。副标题印在刊头，说明随刊首导读出现在第一页，续页不重复。

## 六、视觉风格

参考归藏 skills 的电子杂志排版，全部使用内联样式，保证微信 webview 正常渲染：

| 元素 | 取值 |
| --- | --- |
| 版式 | 竖屏单栏，最大宽度 640px |
| 底色 | 浅灰 `#EFEFEA`，卡片 `#F7F7F4` |
| 正文 | 黑色 `#0B0B0B`，小字 12px |
| 次要信息 | 深灰 `#3A3A38`，等宽字体 10px |
| 强调色 | 萤光绿 `#CCFF00`（刊头、栏目色块、来源标签、译文标记与译文竖线、页脚色条） |
| 结构 | 刊头 + 发丝线分栏 + 两位数序号 + 页脚续页提示 |
| 译文 | 英文原文黑色在上，中文译文深灰在下：标题 14px 带「译」标记，摘要 12px 带萤光绿竖线 |

## 七、10 万字限制与分页

pushplus 微信渠道单条内容上限为 10 万字（会员），标题 100 字。脚本按 **每页 92,000 字符** 的预算打包（预留刊头、页脚与包裹层空间），超出即自动分页：

- 页码显示为 `01 / 03` 这样的形式，页脚会标注「续下页」或「本期结束」。
- 文章序号跨页连续编号，栏目标题在续页顶部重复出现。
- 标题自动追加 `2/3` 页码后缀，单页时不追加。
- 每页之间间隔 2 秒发送，避免触发频率限制。
- 双语卡片（原文 + 译文）比单语卡片更长，英文内容多的一期会分成更多页，字符预算与上限不变。

## 八、本地调试

```bash
# 只渲染不推送，把 HTML 写到 build/digest 目录，用浏览器打开即可预览
python scripts/wechat_digest.py --dry-run --out build/digest --window-hours 48

# 指定集合与期号
python scripts/wechat_digest.py --dry-run --pack ai --edition morning --out build/digest

# 只看原文，不附加中文译文
python scripts/wechat_digest.py --dry-run --no-translate --out build/digest

# 逐个探测翻译通道可用性，只出报告不推送
python scripts/wechat_digest.py --probe-translators --out build/probe

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
| `--translate` | 开 | 英文原文下方附加简体中文译文，`--no-translate` 只显示原文 |
| `--translators` | 环境变量 | 指定通道顺序，例如 `llm,google,edge`；留空表示先 Key 后免密 |
| `--probe-translators` | 关 | 直接逐个调用翻译通道，报告哪些可用后退出 |
| `--page-budget` | `92000` | 单页字符预算 |
| `--topic` | 环境变量 | pushplus 群组编码 |
| `--channel` | `wechat` | 也支持 `mail`、`webhook` 等 pushplus 渠道 |
| `--dry-run` | 关 | 只渲染不推送 |
| `--allow-empty` | 关 | 没有新文章时也推送 |

## 九、常见问题

**英文卡片的译文没出现？** 推送本身照发不误，所以要看运行页面：找 `translation: …` 的 notice，以及列出被弃用通道或配置错误的 warning，或打开 artifact 里的 `translation-report.md`（其中的 Configuration 段落写明本次用的变量、接口和模型）。然后手动跑一次 `Translation probe` 工作流，或者直接看它在翻译相关 PR 下自动贴出的评论，一分钟内就能看到每个接口在 GitHub 运行器上的原始返回。如果免密接口全部拒绝运行器（最常见的是 Google 返回 `429`），就配置 `DEEPSEEK_API_KEY` 或 `DEEPL_API_KEY`：不配 Key 也能运行，但配了译文才稳定。

**DeepSeek 报 “model not exist”？** 账号没问题，是模型名过期了：`deepseek-chat`、`deepseek-reasoner` 已于 2026-07-24 下线，请把 `TRANSLATE_LLM_MODEL` 改成 `deepseek-flash`（或 `deepseek-v4-pro`），或者干脆删掉这个变量让预设去选。同样的提示也会以 warning 注解出现在运行页面。

**没收到推送？** 打开工作流运行日志：`items=0` 说明窗口内确实没有新文章；`pushplus code=xxx` 说明 token 或额度有问题。渲染结果也会作为 artifact 上传，保留 7 天，可下载查看。

**想改推送时间？** 修改 workflow 里的两个 cron（UTC 时间 = 北京时间 − 8 小时），同时更新 `EDITIONS` 里的 `window_hours` 让窗口继续覆盖 24 小时。

**想换风格配色？** 改 `scripts/wechat_digest.py` 顶部的调色板常量即可，测试会校验主要颜色仍然存在。
