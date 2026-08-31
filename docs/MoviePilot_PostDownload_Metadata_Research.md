# MoviePilot 下载后本地元数据识别研究

更新日期：2026-08-30

## 结论

可以做，但准确说法不是“只读种子文件里的 NFO/海报”，而是“下载完成后读取真实下载目录里的 NFO、海报、字幕和文件名”。`.torrent` 本身主要是 metainfo：任务名、文件路径列表、文件大小、分片哈希、tracker 等。它可以提前告诉我们发布包里是否包含 `*.nfo`、`poster.jpg`、`fanart.jpg` 这类文件，但不能直接提供这些文件的正文或图片内容。

对 PigGo 儿童动画场景，最稳的插件路径是：

1. 在下载加入时保存下载 hash、站点条目、种子元信息、下载器和下载目录映射。
2. 在下载完成或整理失败时扫描真实下载内容包。
3. 优先解析随包 NFO、海报、背景图、字幕和媒体文件名。
4. 如果 TMDb 可准确识别，保留 TMDb 身份；若 TMDb 缺失，返回插件媒体来源 `piggokids` 的 `MediaInfo`。
5. 通过 MoviePilot 的识别补充、手动整理或整理重试链路继续入库。

## 官方文档依据

MoviePilot 官方基础说明把媒体识别定义为自动化订阅和整理的核心：程序需要从资源名称提取关键字，并在媒体管理平台找到对应元数据；识别失败时不会继续下载、整理等后续处理。这个行为说明插件必须解决“识别身份”问题，不能只改文件名。

配置参考中与本需求最相关的项目：

- `RMT_MEDIAEXT`、`RMT_SUBEXT`、`RMT_AUDIOEXT` 决定 MP 默认识别哪些媒体、字幕和音频扩展名。
- `MOVIE_RENAME_FORMAT`、`TV_RENAME_FORMAT` 决定整理后的电影/剧集命名结构。
- `SCRAP_FOLLOW_TMDB` 决定新增已入库媒体是否跟随 TMDb 信息变化。
- `RECOGNIZE_PLUGIN_FIRST` 可让插件识别结果优先于默认识别，稳定后可以开启。
- `SEARCH_SOURCE`、`RECOGNIZE_SOURCE`、`SCRAP_SOURCE` 决定搜索、识别和刮削来源。

文件整理文档说明，V2 支持下载器监控和目录监控；通过 MoviePilot 下载的内容优先使用内建下载文件自动整理。整理失败会产生失败记录，可在历史记录中重新整理或通过文件管理手动整理。这个机制适合插件在失败后补齐 `media_source + media_id` 再触发整理重试。

## MoviePilot 源码依据

本地源码缓存：`C:\Users\suerwei\Documents\ChatGPT\MoviePilot-source-cache`

关键文件：

- `app\chain\_recognition.py`
  - 原生识别后会进入插件补充识别。
  - 插件回写的 `mediainfo` 必须包含 `media_source`，并且必须有有效媒体身份。
  - `ChainEventType.MediaRecognize` 的 payload 包含标题、年份、季号、类型、当前来源和 ID。
- `app\runtime\extensions\module\contracts.py`
  - `recognize_media`、`search_medias`、`get_media_auxiliary_info`、`obtain_images` 属于媒体识别能力族。
  - `torrent_files` 可按下载器任务读取文件列表。
  - `download_added(context, torrent_content, download_dir)` 可在下载加入后保存种子元信息和下载目录。
  - `transfer_completed(hashs, downloader)` 可在下载器相关整理完成后做后处理。
- `app\schemas\event.py`
  - `DownloadAddedEventData` 暴露 hash、context、username、downloader、episodes、source 等字段。
  - `TransferResultEventData` 暴露 fileitem、meta、mediainfo、transferinfo、downloader、download_hash、transfer_history_id 等字段。
- `app\application\chain\events.py`
  - 下载后处理快照会保留 `context`、`download_dir` 和 `torrent_content`，其中种子字节会 base64 编码保存。
- `app\api\endpoints\media.py`
  - `/api/v1/media/recognize` 在 title 是媒体文件路径时，会合并父目录中的名称和年份等信息。
  - `/api/v1/media/recognize_file` 可按文件路径识别。
- `app\api\endpoints\download.py`
  - `/api/v1/download/add` 可在识别失败时返回 `requires_confirmation=true`。
  - 显式媒体身份需要成对传 `media_source + media_id`。
- `app\api\endpoints\transfer.py`
  - `/api/v1/transfer/manual` 可用 `media_source + media_id` 指定本次识别和刮削数据源。
  - 预览模式可先生成目标路径和失败原因，再决定是否真正整理。

这些源码点共同说明：V3 不需要硬改核心识别流程。插件可以注册 `piggokids` 来源，在下载后扫描实际文件，然后通过媒体识别补充或手动整理接口把身份交回 MP。

## `.torrent` 能做什么

BitTorrent BEP 3 定义的 metainfo 文件是 bencode 编码的字典，核心 `info` 区域包含单文件或多文件模式；多文件模式下有 `files` 列表，每项包含文件长度和路径片段。这个结构能回答：

- 这个任务的根目录名是什么。
- 包里声明了哪些相对路径。
- 每个文件大概多大。
- 是否声明了 NFO、海报、字幕等附件。
- infohash 与下载任务如何绑定。

它不能可靠回答：

- NFO 文件正文写了什么。
- 海报图片具体是哪张。
- 简介、导演、产地、演员是否准确。
- 字幕语言内容是否正确。
- 下载完成后文件是否被改名、跳过、部分下载或移动。

所以插件可以在 `DownloadAdded` 阶段解析 `.torrent`，但真正生成本地媒体条目要等下载目录可读。

## 推荐实现链路

1. `DownloadAdded` 阶段

   插件保存：

   - `download_hash`
   - `downloader`
   - `download_dir`
   - MP `context`
   - PigGo 站点条目 ID 或详情页 URL
   - `torrent_content` 解析出的根目录、文件路径、文件大小、NFO/图片存在标记

2. 下载完成或整理失败阶段

   插件通过 `download_hash` 找回任务映射，定位真实下载目录。若下载器还能提供文件列表，用 `torrent_files` 交叉确认。然后扫描实际目录。

3. 随包元数据解析

   解析顺序：

   - NFO：`tvshow.nfo`、`movie.nfo`、同名 `*.nfo`
   - 图片：`poster.*`、`fanart.*`、`cover.*`、`folder.*`、`season*.jpg/png/webp`
   - 字幕：同名字幕与语言后缀
   - 媒体文件：剧集编号、季号、集数、标题别名、画质和音轨

4. 本地媒体身份

   对 TMDb 缺失内容生成：

   ```text
   media_source = piggokids
   media_id = piggo:item:<site_item_id>
   ```

   如果没有站点 ID，则使用可稳定复现的规范化标题、年份、季号和下载 hash 派生 ID，但不要把站点私密下载令牌放进 ID。

5. 交回 MP

   可选路径：

   - 在 `ChainEventType.MediaRecognize` 中回写 `mediainfo`。
   - 通过 `/api/v1/transfer/manual` 传入 `media_source + media_id` 做预览和整理。
   - 对失败历史，使用 `transfer_history_id` 或源文件路径做重新整理。

## 实现边界

- 不自动提交 TMDb。
- 不保存 PigGo Cookie、Passkey、下载令牌或账号状态。
- 不把低置信度结果无人值守整理到正式媒体库。
- 不修改做种文件内容；优先硬链接、软链接或复制，让原下载目录继续保种。
- 不把全网搜索或 LLM 猜测直接写入最终 NFO；每个关键字段都要记录来源。

## 来源

- MoviePilot 配置参考：`https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/configuration.md`
- MoviePilot 文件整理：`https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/reorganize.md`
- MoviePilot 基础说明：`https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/basic.md`
- BitTorrent BEP 3：`https://www.bittorrent.org/beps/bep_0003.html`
- MoviePilot 本地源码缓存：`C:\Users\suerwei\Documents\ChatGPT\MoviePilot-source-cache`
