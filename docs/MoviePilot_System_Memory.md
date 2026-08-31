# MoviePilot 系统本地记忆

更新日期：2026-08-31

这份文件用于后续对话快速恢复上下文。它记录的是 MoviePilot 系统、插件开发边界、当前自建插件库状态，以及还需要向用户确认的本地部署信息。不要在这里保存密码、Cookie、站点 Passkey、GitHub Token、API Token 或任何可直接登录/调用的密钥。

## 1. 当前用户目标与约束

- 用户已经本地部署完成 MoviePilot。
- 用户正在维护自己的 MoviePilot 插件库。当前已保留并维护修复后的 `MediaCoverGenerator`，下一项计划是新增 `PigGoKidsMetadata`；仓库只保留用户自维护插件，不恢复上游其它插件。
- 重要协作约束：后续任何 GitHub 推送前，必须先把修改内容给用户看并获得明确确认；不要直接推送。
- 当前工作区仓库路径：`/home/wei/moviepilot-plugins`
- 当前远程插件库记忆：`https://github.com/xianglongwei/MoviePilot-Plugins`

## 2. MoviePilot 是什么

MoviePilot 是 NAS 媒体库自动化管理工具，基于 NAStool 部分代码重新设计，核心流程包括订阅、搜索、下载、整理、刮削、媒体库刷新和消息通知。官方 README 说明后端基于 FastAPI，前端基于 Vue 3，支持下载器、媒体服务器、元数据源、消息渠道、插件、工作流和 AI Agent 等组合能力。

官方入口：

- 官网：`https://movie-pilot.org/`
- Wiki：`https://wiki.movie-pilot.org/`
- 后端主仓库：`https://github.com/jxxghp/MoviePilot`
- 前端主仓库：`https://github.com/jxxghp/MoviePilot-Frontend`
- 官方插件仓库：`https://github.com/jxxghp/MoviePilot-Plugins`
- Wiki 源码：`https://github.com/jxxghp/MoviePilot-Wiki`

核心理解：

- MoviePilot 不是泛下载工具，而是围绕媒体识别、订阅、搜索、下载、整理、刮削、媒体服务器刷新和通知构成的自动化系统。
- 资源进入下载、整理等后续链路前，需要先完成媒体识别；识别失败通常会阻断后续自动化。
- V3 后端把职责拆成 Domain、Application、Chain、Module、Adapter、DB Oper/Adapter、Runtime、Startup、SDK/Compat 等层。插件优先调用稳定 SDK、Chain、Oper 或宿主服务，不应越过边界直接依赖内部目录结构。

## 3. 典型部署与运行形态

- 官方推荐 Docker 部署。V3 正式版镜像是 `jxxghp/moviepilot-v3:latest`，V2 镜像是 `jxxghp/moviepilot-v2:latest`。
- 默认 Web 端口通常是 `3000`，API 服务端口通常是 `3001`，可由 `NGINX_PORT` 和 `PORT` 调整。
- 持久化配置、数据库、日志和缓存默认映射到容器内 `/config`；浏览器内核缓存可映射到 `/moviepilot/.cloakbrowser`。
- 如果计划用硬链接整理，下载目录和媒体库目录必须在容器内映射到同一个根目录，否则跨文件系统会导致硬链接失败。
- V3 可复用 V2 的 `/config` 配置目录和数据库；但 V3 完成数据库升级后不能直接换回 V2 镜像，回退前要按官方降级说明恢复字段并保留备份。
- V3 全新安装时，管理员用户名、密码和 API Key 通过首次打开 Web 初始化页面创建，不再依赖 Docker 模板里的 `SUPERUSER`、`SUPERUSER_PASSWORD` 或 `API_TOKEN`。

## 4. 配置优先级

MoviePilot 配置优先级是：环境变量 > `env` 配置文件 == Web 界面。只要某项已经通过环境变量注入，前端界面和 `app.env` 中的同名配置就不会覆盖它。

插件开发/联调常用配置：

- `PLUGIN_MARKET`：第三方插件市场仓库地址，只支持 GitHub 仓库 `main` 分支；多个仓库用英文逗号分隔。
- `PLUGIN_LOCAL_REPO_PATHS`：本地插件仓库路径，适合本地联调。
- `PLUGIN_AUTO_RELOAD=true`：插件源码变化后自动重新加载。
- `DEBUG=true`：有助于看到 V3 旧导入兼容警告和更多排障信息。
- `GITHUB_TOKEN` / `GITHUB_PROXY` / `PROXY_HOST`：插件市场同步、GitHub 访问和网络环境相关，具体值不能写入本文件。

## 5. MoviePilot 插件运行模型

- `MoviePilot-Plugins` 不是独立运行时，只保存插件源码、市场索引、图标、测试和发布文档。
- 插件实际运行在 `MoviePilot` 后端宿主里；UI 由 `MoviePilot-Frontend` 渲染。
- 插件与主程序运行在同一个 Python 进程和同一个依赖环境中，不是独立微服务，也没有单独虚拟环境。
- 因为共享运行环境，插件依赖必须保守：只声明真正需要的依赖，不要求降级或覆盖 MoviePilot 核心依赖，尽量使用合理版本范围，避免污染宿主环境。
- 这正是之前安装失败的根因：旧插件用 `==` 精确锁定 `numpy`、`pillow`、`pytz`、`pyyaml`，而用户 MoviePilot 环境已安装更高版本，宿主为避免污染共享环境拒绝安装。
- 插件动态 API 通常挂到 `/api/v1/plugin/<PluginID>/<path>`；面向插件前端的接口通常使用登录态认证，外部系统调用再考虑 API Key。
- 简单配置页和轻量详情页优先用 Vuetify JSON；复杂交互、完整页面或侧栏入口再用 Vue 联邦远程组件，并遵守前端构建产物、CSS 隔离和实例作用域 API 规则。

## 5.1 儿童动画本地元数据插件方向

- 用户现在更倾向于“下载后自动读取真实下载文件夹”，而不是优先全网搜索或自动写回 TMDb。
- 用户提出的产品流程是：获取 PigGo 儿童动画 RSS、在插件里选择/搜索或粘贴下载链接、调用 MoviePilot 已配置下载器下载、下载后做自动识别增强。
- `.torrent` 不是 NFO/海报内容来源。它通常只能提供任务名、文件路径列表、文件大小、分片哈希和 tracker 等种子元信息；若发布包里包含 `*.nfo`、`poster.jpg`、`fanart.jpg` 等，它可以从文件列表里看出来，但实际内容必须到下载完成后的目录中读取。
- 推荐自动闭环：`DownloadAdded` 时保存下载 hash、站点条目、种子元信息和下载目录映射；下载完成或整理失败时扫描下载内容包；优先解析随包 NFO、海报、字幕和文件名；再 fallback 到 PigGo 详情页、公开来源和全网候选。
- 对 TMDb 缺失的儿童动画，插件注册 `piggokids` 媒体来源并返回带 `media_source + media_id` 的 `MediaInfo`，再让 MoviePilot 的识别、手动整理或整理重试链路继续工作。
- 对 TMDb 已存在但信息不完整的内容，优先保留 TMDb 身份入库，同时生成本地 NFO/图片补强和 TMDb 贡献草稿；不要自动提交 TMDb。
- TMDb API Key 不能解决自动新增/编辑 TMDb 基础资料问题。官方公开 API 可用于搜索、认证和账号级收藏/列表/评分等，但没有稳定公开的创建或编辑电影/剧集基础资料接口。
- TMDb changes 接口是读接口，可用于监控已存在电影/剧集 ID 的近期字段变化，并在更新后提示或触发 MP 重新刮削；不能用于创建或编辑资料。

## 6. V2 插件规则

- V2 专用插件源码放在 `plugins.v2/<plugin_id_lower>/`。
- V2 专用索引写入 `package.v2.json`。
- 插件主类必须在对应目录的 `__init__.py` 中，目录名是类名小写。
- V2 历史依赖文件使用 `requirements.txt`。
- V2 下插件 UI 可以通过 Vuetify JSON 返回配置/详情页，也可以提供 Vue 联邦远程组件。

## 7. V3 插件规则

- 新插件开发以 V3 为主，源码放在 `plugins.v3/<plugin_id_lower>/`。
- V3 专用索引写入 `package.v3.json`。
- V3 额外 Python 依赖使用插件目录内的 `pyproject.toml`，宿主读取静态 `project.dependencies`。
- 新 V3 插件应优先从 `app.sdk` 导入稳定接口，避免继续依赖 `app.core.*`、`app.helper.*`、`app.utils.*` 等旧路径。
- V3 可以回退加载部分旧 V2 插件；只有插件使用了 V3 已变更的合同，才需要建立 V3 专用副本。
- 如果已经建立 V3 专用副本，旧索引同名条目应考虑声明 `"v3": false`，避免 V3 回退加载旧合同实现。当前已发布的 `MediaCoverGenerator` 已有 V3 显式索引，后续插件也应在真实 V3 宿主中加载验证。
- 涉及媒体身份、音乐链、宿主 REST API、数据库事务或媒体库相关链路的插件，迁移 V3 前要额外检查官方 V2 到 V3 迁移说明。
- V3 统一媒体身份使用 `media_source + media_id`；涉及订阅、下载任务、整理任务、识别结果、历史、媒体服务器事件或 Webhook 的插件，不要只比较裸 `media_id`。
- V3 普通 JSON API 统一响应形态是 `{success, message, data}`；但插件通过 `get_api()` 注册的路由不会被宿主隐式包装，插件要明确自己的返回合同。

## 8. 当前自建插件库状态

当前自建插件库保留 `MediaCoverGenerator`，并已开始实现 `PigGoKidsMetadata`。后者仍是本地开发版本，尚未提交、推送或在真实宿主发布：

- 展示名：`Emby媒体库封面生成`
- 插件 ID：`MediaCoverGenerator`
- V2 源码目录：`plugins.v2/mediacovergenerator/`
- V3 源码目录：`plugins.v3/mediacovergenerator/`
- V2 索引：`package.v2.json`
- V3 索引：`package.v3.json`
- 当前版本：`0.9.7`
- 图标：`icons/emby.png`
- 支持：Emby/Jellyfin 媒体库动态/静态封面生成

已修复的依赖策略：

```text
pillow>=11.2.1
numpy>=2.2.0
pytz>=2025.2
pyyaml>=6.0.2
```

V3 `pyproject.toml` 依赖与上面一致，并设置 `requires-python = ">=3.12"`。

给 MoviePilot 配置第三方插件市场时，优先使用仓库地址：

```text
https://github.com/xianglongwei/MoviePilot-Plugins
```

如需直接检查索引：

```text
https://raw.githubusercontent.com/xianglongwei/MoviePilot-Plugins/main/package.v2.json
https://raw.githubusercontent.com/xianglongwei/MoviePilot-Plugins/main/package.v3.json
```

计划新增插件：

- 展示名暂定：`PigGo 儿童动画增强识别`
- 插件 ID：`PigGoKidsMetadata`
- 目标：PigGo RSS/链接下载、下载后本地元数据扫描、`piggokids` 媒体身份、MoviePilot 自动整理和媒体库刷新。
- 版本：V2 / V3 双版本支持。
- 需求基线：`docs/PigGoKidsMetadata_Product_Requirements.md`
- 当前状态：本地开发版已推进到 `0.3.0`。V2/V3 均已实现安全 RSS/Atom 候选、粘贴链接、显式下载提交、hash 跟踪、下载轮询恢复、逐文件整理结算、下载后扫描、只读 TMDb 严格匹配、只读贡献草稿和默认关闭的可选整理；V3 继续提供 `piggokids` 来源，V2 通过本地 MediaInfo 调用整理链。已通过本地纯逻辑与轻量宿主合同测试，尚未使用真实 PigGo RSS 或真实 MoviePilot V2/V3 实例完成端到端验收。`0.2.0` 基线已有本地提交，`0.3.0` 变更仍未提交或推送。

## 9. 后续开发检查清单

每次改插件前：

- 先确认目标是 V2、V3，还是双版本都要支持。
- 先读当前插件目录、索引文件和官方最新插件开发文档。
- 不新增无关插件，不恢复已删除的上游插件。
- 不把运行数据写入插件源码目录；插件运行状态使用基类数据接口或插件数据目录。
- 不在模块导入期启动任务、访问网络或连接数据库。
- 修改依赖时优先放宽兼容范围，避免要求宿主降级。
- 修改 `plugin_version`、`package.v2.json`/`package.v3.json` 的 `version` 和 `history` 时保持一致。

每次提交/推送前：

- 运行 JSON 校验。
- 对 V2/V3 插件源码运行 Python 编译检查。
- 检查插件类 `plugin_version` 与 `package*.json` 版本是否一致。
- 检查依赖文件是否会与宿主共享环境冲突。
- 在真实 MoviePilot 宿主里至少安装、启动、禁用、重载一次；单纯语法编译发现不了导入路径、宿主依赖、API 注册、服务清理或前端加载问题。
- 向用户展示 diff/摘要，等用户确认后再推送。

建议验证命令：

```powershell
python -m json.tool package.v2.json > $null
python -m json.tool package.v3.json > $null
python -m compileall plugins.v2/mediacovergenerator plugins.v3/mediacovergenerator
git diff --check
```

## 10. 还需要补齐的本地实例信息

用户说本地 MoviePilot 已部署完成，但当前还没有给 Codex 以下信息。后续需要排障或联调时再询问，且不要记录敏感值：

- 本地访问地址，例如 `http://<nas-ip>:3000` 或反代域名。
- 当前 MoviePilot 主版本：V2 还是 V3，以及具体版本号。
- 部署方式：Docker run、docker-compose、群晖套件、Windows 安装版、Windows-MoviePilot，或源码/CLI。
- `/config`、媒体目录、下载目录在宿主机和容器内的映射关系。
- 数据库：SQLite 还是 PostgreSQL；缓存：cachetools 还是 Redis。
- 已连接的媒体服务器：Emby、Jellyfin 或 Plex；插件目前主要面向 Emby/Jellyfin。
- 下载器：qBittorrent、Transmission 等，以及下载完成整理触发方式。
- 当前 `PLUGIN_MARKET` 是否已包含用户自己的仓库。
- GitHub 访问是否需要代理或 Token，但不要把 Token 写入记忆文件。

## 11. 官方来源

- MoviePilot README：`https://github.com/jxxghp/MoviePilot`
- MoviePilot Wiki 安装指引：`https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/install.md`
- MoviePilot Wiki 配置参考：`https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/configuration.md`
- MoviePilot Wiki 插件页：`https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/plugin.md`
- MoviePilot Wiki V3 说明：`https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/v3.md`
- MoviePilot-Plugins V3 插件开发指南：`https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md`
- MoviePilot-Plugins V2 到 V3 迁移说明：`https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/V3_Plugin_Adaptation.md`
- MoviePilot-Plugins 仓库指南：`https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Repository_Guide.md`
- MoviePilot-Plugins V2 历史开发指南：`https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/V2_Plugin_Development.md`
- 本次一手来源调研笔记：`docs/MoviePilot_Official_Research.md`
