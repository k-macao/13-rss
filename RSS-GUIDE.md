# RSS usage guide

[简体中文](RSS-GUIDE.zh-CN.md) · [Home](README.md) · [Download OPML](README.md#downloads)

RSS brings updates from different websites into one reader, in chronological order and without a recommendation feed deciding what you see. This repository packages the feed URLs into OPML files, so you do not need to add hundreds of subscriptions by hand.

## Start in three steps

1. **Choose a bundle.** Use the Top 200 for a first import: every primary category is represented without requiring you to prune hundreds of feeds. Pick a topic bundle when you already know your focus, or the complete collection when you want an archive to organize yourself.
2. **Download the OPML file.** Click a file in the [download table](README.md#downloads). The downloaded file should end in `.opml`. If the browser displays XML text, save the page as a file instead of subscribing to the page URL.
3. **Import it into your reader.** If you do not have one yet, choose from the [readers and tutorials](#readers-and-official-tutorials) below. Otherwise, look for “Import OPML,” select the downloaded file, and wait for the first refresh. The OPML groups become folders in readers that preserve them.

### Import into Tidings

Open Tidings and choose **Import OPML** from Add Subscription or Feed Management. Tidings preserves the bundle groups, skips duplicate feed URLs, and reports how many subscriptions were added, skipped, or failed.

The Top 200 is the default everyday bundle. The 725-feed complete collection creates a larger unread queue and many more network requests, so it is better suited to readers who plan to curate it themselves.

## Which bundle should I choose?

| You want to read | Recommended bundle |
| --- | --- |
| A balanced first library across every category | ⭐ Top 200 |
| Chinese personal writing | ✍️ Chinese independent blogs |
| V2EX, LINUX DO, Reddit, Hacker News, and other discussions | 👥 Technical communities |
| Vulnerabilities, security research, and advisories | 🔐 Security |
| Technology news and long-form reporting | 📰 Technology media |
| Curated technical reading delivered weekly | 📮 Technical newsletters |
| First-party writing from company engineering teams | 🏢 Company technology |
| Models, AI research, and tools | 🤖 Artificial intelligence |
| Every Chinese-language source | 🀄 Chinese-language sources |
| The entire directory to prune yourself | 📚 Complete collection |

Topic bundles may overlap. Most readers identify existing subscriptions by feed URL, but exporting your current OPML before a large import is a sensible backup.

## Subscribe to a single feed

Open the [complete source directory](README.md#complete-source-directory), click `RSS` beside the source you want, copy the final feed URL, and paste it into your reader’s Add Subscription field.

V2EX has a main feed plus technology and creative section feeds. Subscribe to the main feed for broad coverage, or choose a section to reduce noise. The section feeds overlap with the main feed, so subscribing to all three is usually unnecessary.

## Troubleshooting

### The download opens as XML text

Your browser is previewing the OPML file. Save it with the `.opml` extension, or download the attachment from [GitHub Releases](https://github.com/fuxiaoai/tidings-rss/releases/latest).

### No articles appear immediately after import

Allow the reader to finish its first refresh. Large bundles trigger many network requests, and a few publishers may respond slowly. Refresh an individual feed if it alone remains empty; [report it](CONTRIBUTING.md) if the failure repeats.

### The unread count is overwhelming

Remove folders you do not want, or import a smaller topic bundle into a fresh account. The catalog is a starting point, not a required reading list.

### How do I back up my subscriptions?

Most readers can export OPML. Keep a recent export before changing readers or reorganizing a large library.

## Readers and official tutorials

- [Tidings](https://tidings.info/): an AI-native RSS reader with OPML groups, AI summaries and article Q&A, AI-powered translation, AI Radar, and Obsidian export.
- [NetNewsWire: Importing Subscriptions with an OPML File](https://netnewswire.com/help/mac/6.0/en/import-opml.html)
- [Feedly: How to import an OPML file](https://docs.feedly.com/article/51-how-to-import-opml-into-feedly)
- [FreshRSS: Subscription management, import, and export](https://freshrss.github.io/FreshRSS/en/users/04_Subscriptions.html)
- [RSSHub documentation](https://docs.rsshub.app/): find routes or create feeds for sites without a native endpoint.
- [OPML 2.0 specification](https://opml.org/spec2.opml)

Feed URLs move and publishers stop maintaining them. When [suggesting a feed or reporting a broken one](CONTRIBUTING.md), include the Feed URL and the reader you tested.
