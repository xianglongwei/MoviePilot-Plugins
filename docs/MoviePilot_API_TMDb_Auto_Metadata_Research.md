# MoviePilot API 与 TMDb 自动元数据写入能力调研

调研日期：2026-08-30  
范围：MoviePilot 官方 API/OpenAPI、MoviePilot v3 源码、TMDb 官方开发文档/OpenAPI/贡献规则。  
来源原则：只引用一手来源；GitHub 源码链接固定到本次调研使用的 MoviePilot v3 提交 `3e9c2dcf42d899d627459d67068414e3b86f3415`。

## 结论摘要

1. MoviePilot 提供媒体识别、下载、手动整理、刮削、插件动态 API、事件/工作流等能力；这些能力可以在“下载完成/整理完成/需要刮削”后触发本地自动化。
2. MoviePilot 的 TMDb 模块是读取、匹配、取图、写本地识别缓存和本地媒体库元数据，不包含向 TMDb 站点创建/编辑电影、剧集、人物、图片或翻译的官方写入路径。
3. TMDb 官方 v3 OpenAPI 中的写操作集中在认证会话、账号收藏/观看列表、列表、评分等用户账号行为；没有电影/剧集/人物条目创建、基础字段编辑、图片上传或翻译写入 API。
4. TMDb 条目创建/编辑是登录用户在 TMDb 网站上的贡献流程，而不是官方第三方程序接口。官方贡献规则要求遵守内容、来源、图片、翻译等约束，违规内容可被删除、锁定或导致账号处理。
5. 因此，MoviePilot 插件可以做“识别失败检测、生成本地 NFO/图片、生成待人工审核的 TMDb 贡献任务、跳转到 TMDb 页面”等，但不能基于官方 TMDb API 自动创建或编辑 TMDb 公共条目。

## 一手来源

MoviePilot：

- [MoviePilot API Swagger UI](https://api.movie-pilot.org/#/)
- [MoviePilot OpenAPI JSON](https://api.movie-pilot.org/openapi.json)
- [MoviePilot v3 源码，提交 `3e9c2dcf42d899d627459d67068414e3b86f3415`](https://github.com/jxxghp/MoviePilot/tree/3e9c2dcf42d899d627459d67068414e3b86f3415)

TMDb：

- [TMDb Developer Docs 索引](https://developer.themoviedb.org/llms.txt)
- [TMDb v3 OpenAPI JSON](https://developer.themoviedb.org/openapi/tmdb-api.json)
- [TMDb Getting Started](https://developer.themoviedb.org/docs/getting-started)
- [TMDb Application Authentication](https://developer.themoviedb.org/docs/authentication-application)
- [TMDb Rate Limiting](https://developer.themoviedb.org/docs/rate-limiting)
- [TMDb Daily ID Exports](https://developer.themoviedb.org/docs/daily-id-exports)
- [TMDb FAQ](https://developer.themoviedb.org/docs/faq)
- [TMDb Image Languages](https://developer.themoviedb.org/docs/image-languages)
- [TMDb Movie Images API Reference](https://developer.themoviedb.org/reference/movie-images)
- [TMDb TV Series Images API Reference](https://developer.themoviedb.org/reference/tv-series-images)
- [TMDb TV Season Images API Reference](https://developer.themoviedb.org/reference/tv-season-images)
- [TMDb TV Episode Images API Reference](https://developer.themoviedb.org/reference/tv-episode-images)
- [TMDb Person Images API Reference](https://developer.themoviedb.org/reference/person-images)
- [TMDb TV Season Translations API Reference](https://developer.themoviedb.org/reference/tv-season-translations)
- [TMDb Contribution Bible](https://www.themoviedb.org/bible?language=en-US)
- [TMDb Creating a New Entry](https://www.themoviedb.org/bible/new_content?language=en-US)
- [TMDb General Contribution Guidelines](https://www.themoviedb.org/bible/general?language=en-US)
- [TMDb Movie Bible](https://www.themoviedb.org/bible/movie?language=en-US)
- [TMDb TV Bible](https://www.themoviedb.org/bible/tv?language=en-US)
- [TMDb Image Bible](https://www.themoviedb.org/bible/image?language=en-US)
- [TMDb Poster Image Rules](https://www.themoviedb.org/bible/image/59f7582c9251416e7100005f?language=en-US)

## MoviePilot 官方 API 面

### OpenAPI 可访问性

`https://api.movie-pilot.org/openapi.json` 可访问。本次解析结果：

- OpenAPI 版本：`3.1.0`
- 标题：`MoviePilot`
- API 版本：`0.1.0`
- 路径数量：`337`

多数相关接口声明了以下认证方式之一或组合：

- `OAuth2PasswordBearer`
- `api_key_query`
- `api_key_header`
- `api_token_query`

### 媒体识别

OpenAPI 中存在两类识别入口：

| 方法 | 路径 | 作用 | 关键参数 |
|---|---|---|---|
| `GET` | `/api/v1/media/recognize` | 识别媒体信息（种子） | `title` 必填，`subtitle`、`custom_words`、`source` 可选 |
| `GET` | `/api/v1/media/recognize_file` | 识别媒体信息（文件） | `path` 必填，`source` 可选 |

源码中，MoviePilot 的内置媒体源包含 TMDb、豆瓣、Bangumi、AniList、IMDb、TVDB、MusicBrainz、TheAudioDB 等；启用插件还可以注册额外媒体源。参见 [`app/api/endpoints/media.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/api/endpoints/media.py#L42-L87)。

### 刮削

OpenAPI 中存在：

| 方法 | 路径 | 作用 | 关键输入 |
|---|---|---|---|
| `POST` | `/api/v1/media/scrape/{storage}` | 刮削媒体信息 | `FileItem` 请求体，`media_source`、`media_id`、`type_name` 查询参数 |

源码实现会在显式 `media_id` 场景下要求 `media_source`，然后调用媒体识别、获取图片、再调用 `ScrapingChain().scrape_metadata(..., overwrite=True)` 写入本地元数据；音乐路径会走 `scrape_music_metadata`。参见 [`app/api/endpoints/media.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/api/endpoints/media.py#L391-L511)。

关键边界：这里的“刮削”是对 MoviePilot 管理的文件/媒体库写本地 NFO、图片等元数据，不是向 TMDb 公共数据库写入。

### 下载与手动整理

OpenAPI 中相关入口：

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/api/v1/download/` | 添加下载（含媒体信息） |
| `POST` | `/api/v1/download/add` | 添加下载（不含媒体信息） |
| `POST` | `/api/v1/transfer/manual` | 手动转移 |

下载接口会通过 `MediaChain` 识别媒体，再调用 `DownloadChain().download_single(...)`。参见 [`app/api/endpoints/download.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/api/endpoints/download.py#L114-L275)。

手动整理接口会解析历史记录、文件列表、媒体类型、季集格式、是否后台运行、是否刮削等参数，再调用 `TransferChain().manual_transfer(...)`。参见 [`app/api/endpoints/transfer.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/api/endpoints/transfer.py#L447-L755)。

### 插件 API 与动态路由

OpenAPI 中存在插件配置、卸载、表单、远程插件、侧边栏等接口；源码还支持插件注册动态 API。

插件基类 `_PluginBase` 定义了 `get_api()`，插件可返回路径、处理函数、方法、认证方式、摘要和描述。参见 [`app/plugins/__init__.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/plugins/__init__.py#L96-L109)。

插件还可以通过 `get_media_source()` 注册媒体数据源，通过 `get_actions()` 注册工作流动作。参见 [`app/plugins/__init__.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/plugins/__init__.py#L202-L225)。

动态插件路由由插件管理层注册，再由 Web 适配器挂到 FastAPI 路由表，并刷新 OpenAPI 缓存。参见 [`app/application/plugin/routes.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/application/plugin/routes.py#L30-L47) 与 [`app/adapters/web/plugin/routes.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/adapters/web/plugin/routes.py#L12-L138)。

### 事件与工作流触发点

MoviePilot 定义了下载、整理、刮削、插件、订阅、消息、配置变更等事件。与自动元数据流程最相关的是：

- `download.added`
- `transfer.complete`
- `transfer.failed`
- `metadata.scrape`
- `plugin.action`
- `workflow.execute`

事件枚举参见 [`app/schemas/types.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/schemas/types.py#L196-L258)。

下载历史链路在添加下载后会发送 `DownloadAdded`。参见 [`app/chain/download/history.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/chain/download/history.py#L149-L168)。

整理结算链路会根据文件类型和成功状态选择 `TransferComplete`、`TransferFailed`、字幕/音轨完成或失败事件。参见 [`app/chain/transfer/settlement.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/chain/transfer/settlement.py#L88-L102) 与 [`app/chain/transfer/settlement.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/chain/transfer/settlement.py#L240-L251)。

整理后需要元数据刮削时，MoviePilot 会构造并发送 `MetadataScrape` 事件，事件载荷包含 `meta`、`mediainfo`、`fileitem`、文件列表、是否覆盖等上下文。参见 [`app/chain/transfer/scrape.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/chain/transfer/scrape.py#L22-L75)。

## MoviePilot v3 中的 TMDb 读写边界

### TMDb 模块配置与用途

MoviePilot 的 TMDb 模块读取 `TMDB_API_KEY`、`TMDB_API_DOMAIN`、`TMDB_LOCALE`、`PROXY_HOST` 等配置，初始化缓存、API 包装器、分类辅助与刮削器。参见 [`app/modules/themoviedb/__init__.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/modules/themoviedb/__init__.py#L119-L139)。

连通性测试使用的是 TMDb v3 电影读取接口。参见 [`app/modules/themoviedb/__init__.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/modules/themoviedb/__init__.py#L174-L184)。

识别计划会排除非 TMDb 或非数字 `media_id` 的情况，随后按缓存、分组、ID 查询、名称搜索、详情加载、缓存保存等步骤执行。参见 [`app/modules/themoviedb/__init__.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/modules/themoviedb/__init__.py#L190-L244)、[`app/modules/themoviedb/__init__.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/modules/themoviedb/__init__.py#L760-L874)。

### TMDb API 包装器主要是读接口

`TmdbApi` 包装了搜索、电影、剧集、季、集、发现、趋势、人物、合集等对象。源码中的识别、匹配、详情、图片、翻译等调用都是读取 TMDb 数据。参见 [`app/modules/themoviedb/tmdbapi.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/modules/themoviedb/tmdbapi.py#L52-L127) 与 [`app/modules/themoviedb/tmdbapi.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/modules/themoviedb/tmdbapi.py#L546-L600)。

`TmdbScraper` 负责生成本地 NFO 和获取本地所需图片 URL/内容，例如海报、背景、剧集静帧等。参见 [`app/modules/themoviedb/scraper.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/modules/themoviedb/scraper.py#L35-L204)。

底层 `tmdbv3api` 客户端会拼接 `/3` API URL，并自动附带 API key、语言参数和限流处理。参见 [`app/modules/themoviedb/tmdbv3api/tmdb.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/modules/themoviedb/tmdbv3api/tmdb.py#L61-L69)、[`app/modules/themoviedb/tmdbv3api/tmdb.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/modules/themoviedb/tmdbv3api/tmdb.py#L354-L388)。

### MoviePilot 的 TMDb“写”是本地缓存写入

MoviePilot OpenAPI 中有 TMDb 缓存查询、删除、清空等接口，例如 `/api/v1/tmdb/cache` 与 `/api/v1/tmdb/cache/{cache_key}`。源码中的 `update_recognize_cache`、`tmdb_cache_items`、`tmdb_cache_delete`、`tmdb_cache_clear` 操作的是 MoviePilot 本地识别缓存。参见 [`app/modules/themoviedb/__init__.py`](https://github.com/jxxghp/MoviePilot/blob/3e9c2dcf42d899d627459d67068414e3b86f3415/app/modules/themoviedb/__init__.py#L1044-L1085)。

MoviePilot 仓库内 vendored `tmdbv3api` 确实包含收藏、观看列表、评分、列表等 TMDb 账号类写操作，但这些不是公共媒体库条目的创建/编辑接口，也没有被 MoviePilot TMDb 识别/刮削 API 暴露为“创建或编辑 TMDb 条目”的能力。

## TMDb 官方 API 写入能力

### API 认证

TMDb 官方文档要求开发者登录 TMDb 账号，在账号设置中申请 API key。v3 API 可以通过 `api_key` 参数或 Bearer token 调用。参见 [TMDb Application Authentication](https://developer.themoviedb.org/docs/authentication-application)。

这意味着：

- 使用 TMDb API 需要 TMDb 用户账号来申请凭据。
- 账号级写操作通常还需要用户会话或对应授权上下文。
- 仅拥有 API key 不代表可以修改 TMDb 公共媒体库条目。

### v3 OpenAPI 中的写操作清单

本次解析 [TMDb v3 OpenAPI JSON](https://developer.themoviedb.org/openapi/tmdb-api.json) 得到 `148` 个路径，其中非 `GET` 写操作为 `17` 个：

| 方法 | 路径 | 含义 |
|---|---|---|
| `POST` | `/3/account/{account_id}/favorite` | 添加/取消收藏 |
| `POST` | `/3/account/{account_id}/watchlist` | 添加/取消观看列表 |
| `POST` | `/3/authentication/session/convert/4` | 从 v4 token 创建会话 |
| `POST` | `/3/authentication/session/new` | 创建会话 |
| `DELETE` | `/3/authentication/session` | 删除会话 |
| `POST` | `/3/authentication/token/validate_with_login` | 使用登录验证 token |
| `POST` | `/3/list` | 创建列表 |
| `POST` | `/3/list/{list_id}/add_item` | 向列表添加电影 |
| `POST` | `/3/list/{list_id}/clear` | 清空列表 |
| `POST` | `/3/list/{list_id}/remove_item` | 从列表移除电影 |
| `DELETE` | `/3/list/{list_id}` | 删除列表 |
| `POST` | `/3/movie/{movie_id}/rating` | 添加电影评分 |
| `DELETE` | `/3/movie/{movie_id}/rating` | 删除电影评分 |
| `POST` | `/3/tv/{series_id}/rating` | 添加剧集评分 |
| `DELETE` | `/3/tv/{series_id}/rating` | 删除剧集评分 |
| `POST` | `/3/tv/{series_id}/season/{season_number}/episode/{episode_number}/rating` | 添加单集评分 |
| `DELETE` | `/3/tv/{series_id}/season/{season_number}/episode/{episode_number}/rating` | 删除单集评分 |

负面结论同样重要：官方 v3 OpenAPI 未提供以下写接口：

- 创建电影、剧集、季、集、人物、合集条目
- 编辑标题、原名、简介、日期、状态、演职员、外部 ID 等公共字段
- 上传海报、背景、Logo、剧照、人物图
- 创建或编辑翻译

TMDb 开发文档称 v3 API reference 是电影、电视、演员和图片 API 当前可用方法的权威列表。参见 [TMDb Getting Started](https://developer.themoviedb.org/docs/getting-started)。

### 图片与翻译 API 是读取边界

TMDb 官方 API Reference 中，电影、剧集、季、集、人物的图片接口是获取图片列表；翻译接口也是获取已有翻译信息。参见 [Movie Images](https://developer.themoviedb.org/reference/movie-images)、[TV Series Images](https://developer.themoviedb.org/reference/tv-series-images)、[TV Season Images](https://developer.themoviedb.org/reference/tv-season-images)、[TV Episode Images](https://developer.themoviedb.org/reference/tv-episode-images)、[Person Images](https://developer.themoviedb.org/reference/person-images)、[TV Season Translations](https://developer.themoviedb.org/reference/tv-season-translations)。

图片语言选择也发生在读取侧：`poster_path`、`backdrop_path`、`still_path` 等返回值会按语言、原始语言、无语言、高评分等策略选择；`/images` 可通过语言参数过滤，并可使用 `include_image_language` 加入回退语言。参见 [TMDb Image Languages](https://developer.themoviedb.org/docs/image-languages)。

## TMDb 官方贡献/编辑规则

### 创建与编辑入口是登录后的 Web 贡献流程

TMDb Contribution Bible 页面提示，找不到电影或剧集时需要登录后创建。参见 [TMDb Contribution Bible](https://www.themoviedb.org/bible?language=en-US)。

新建条目的官方流程在 [Creating a New Entry](https://www.themoviedb.org/bible/new_content?language=en-US) 中说明：

- 新建按钮位于 TMDb 网站页面中，入口区分电影和 TV。
- 电影最少需要原始标题与简介；TV 还需要至少一集的标题、简介和播出日期。
- 表单完成后才会创建条目，创建后页面会跳转到新条目。
- 新条目创建后仍需要补充图片和可确认的数据。
- 不支持的内容或放错分区的内容可被管理员删除。

这说明 TMDb 的公共数据库贡献是用户账号行为，且发生在网站编辑界面，不是官方 API 的批量/自动写入能力。

### 审核、删除、锁定与处理

[General Contribution Guidelines](https://www.themoviedb.org/bible/general?language=en-US) 将 TMDb 数据定位为用户贡献数据，并要求贡献者遵守规则。页面说明，错误、垃圾、破坏性或违规贡献可能被删除，严重或重复行为可能导致账号被限制。

内容问题报告由志愿内容管理员处理，管理员可锁定/解锁数据、删除条目/季/集/图片/关键词、重置 URL 和主图等。处理时间没有即时承诺，复杂或低优先级问题可能等待很久。

因此，“创建后站内出现”和“经过长期稳定审核认可”不是同一件事。官方贡献流程允许登录用户提交，但提交内容仍处在社区规则和管理员治理之下。

### 缓存、即时可见性与下游同步

官方新建流程说明，条目完成创建后页面会跳转到新条目；这可以视为站内创建成功后的即时页面可见性信号。它不是 API/CDN 缓存即时更新承诺。

TMDb FAQ 说明 API 没有服务级别协议。参见 [TMDb FAQ](https://developer.themoviedb.org/docs/faq)。

TMDb Daily ID Exports 每天发布有效 ID 文件，且官方明确这些文件不是完整数据导出；导出任务每天在固定 UTC 时间段运行。参见 [TMDb Daily ID Exports](https://developer.themoviedb.org/docs/daily-id-exports)。

对第三方程序的实际含义：

- 网站新建/编辑后，TMDb 页面可能立即出现或跳转。
- API、图片 CDN、搜索索引、每日导出和第三方缓存不应假设同步即时完成。
- MoviePilot 自身还有本地 TMDb 识别缓存，必要时只能清理或更新本地缓存，不能强制 TMDb 远端传播。

### 翻译写入边界

[Movie Bible](https://www.themoviedb.org/bible/movie?language=en-US) 对标题与简介翻译有明确约束：

- 原始标题应使用首次官方发行的原始语言标题，不能随意罗马化或添加年份/国家后缀。
- 翻译标题应使用该地区首次官方译名，不能添加非官方翻译。
- 中文译名按地区区分，例如中国大陆、香港、台湾、新加坡；只有作品在对应地区有官方发行标题时才应填写。
- 简介应客观、简洁、避免剧透，不应复制 IMDb 等受版权保护站点的文本。

这些规则说明翻译是受地区、官方来源和版权约束的人工贡献内容。官方 API 没有提供翻译创建/编辑端点。

### 图片写入边界

[Image Bible](https://www.themoviedb.org/bible/image?language=en-US) 要求图片符合质量、比例、水印、压缩、裁切等规则。

[Poster Image Rules](https://www.themoviedb.org/bible/image/59f7582c9251416e7100005f?language=en-US) 对海报进一步约束：

- 允许官方海报和符合规则的版本，不允许非官方/粉丝/AI 生成海报设计。
- 海报使用 JPEG，常规比例按官方范围处理。
- 不应上传低质量、拉伸、带第三方水印或错误语言标记的图片。
- 故意用错误语言提升图片展示权重会被视为破坏性行为。

官方 API 只提供图片读取端点，没有图片上传端点。图片贡献应理解为登录用户在 TMDb 网站界面的人工贡献流程。

## 对 MoviePilot 插件/自动化设计的影响

### 可行能力

MoviePilot 插件可以合理实现：

- 监听 `DownloadAdded`、`TransferComplete`、`MetadataScrape` 等事件。
- 在识别失败或 TMDb 缺失数据时，记录待处理清单。
- 调用 MoviePilot 现有识别、整理、刮削 API 更新本地媒体库元数据。
- 清理或更新 MoviePilot 本地 TMDb 识别缓存。
- 通过插件动态 API 暴露“待补全媒体”“本地识别结果”“人工贡献链接”“审核状态”等页面或接口。
- 基于 TMDb 官方读取 API 查询候选条目、图片、翻译、外部 ID、每日 ID 导出等数据。

### 不可作为官方能力实现的事项

基于本次一手来源，以下事项不能声明为 MoviePilot/TMDb 官方 API 支持：

- 第三方程序自动创建 TMDb 电影/剧集/人物条目。
- 第三方程序自动编辑 TMDb 公共字段。
- 第三方程序自动上传 TMDb 图片。
- 第三方程序自动写入 TMDb 翻译。
- 创建/编辑后保证 API、搜索、图片、导出或第三方缓存立即可见。

### 推荐设计边界

如果要做 “TMDb 自动元数据补全” 类插件，建议边界定义为：

1. 自动完成 MoviePilot 本地层：识别、重试、整理、NFO/图片刮削、本地缓存维护。
2. 自动生成人工贡献材料：原始标题、年份、类型、候选来源、缺失字段、建议简介草稿、图片检查清单。
3. 人工登录 TMDb 网站后提交：由用户确认来源、版权、地区翻译、图片质量和条目类型。
4. 提交后仅做跟踪：通过 TMDb 读取 API、页面链接或每日 ID 导出观察条目是否出现，避免承诺即时同步。

不建议通过浏览器自动化模拟登录用户批量提交 TMDb 贡献。即使技术上可操作，这也不是官方 API 能力，并且容易触碰 TMDb 对错误数据、垃圾内容、推广滥用、版权文本、图片语言作弊和 AI/非官方内容的治理规则。

## 最终判断

MoviePilot v3 已经具备在下载、整理、刮削链路中触发插件和工作流的足够钩子；它适合做本地媒体库元数据自动化和人工贡献辅助。

TMDb 官方 API 不提供公共媒体库条目的创建/编辑/图片上传/翻译写入能力。TMDb 的公共数据写入属于登录用户在网站上的贡献行为，受 Contribution Bible 约束，并受到社区和管理员治理。第三方程序不能基于官方 TMDb API 自动创建或编辑条目；最多可以使用官方读取 API 与 MoviePilot 事件体系生成待人工审核的贡献工作流。
