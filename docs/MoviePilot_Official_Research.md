# MoviePilot 官方来源调研笔记

> 调研日期：2026-08-30  
> 来源边界：只使用 MoviePilot 官方网站、官方 Wiki、`jxxghp` 维护的官方 GitHub 仓库及其源码/文档。未引用社区文章、第三方博客或非官方整理。

## 一手来源清单

- 官方网站：MoviePilot 将自己定位为“新一代智能化个人媒体库管理工具”，强调前后端分离、插件生态、搜索、下载、整理、媒体服务器集成和通知能力。[官方首页](https://movie-pilot.org/)
- 官方 Wiki：Wiki 首页声明与 `jxxghp/MoviePilot-Wiki` 保持同步，并列出安装、配置、站点、搜索、下载、订阅、整理、通知、插件、智能助手、MCP 等官方文档入口。[Wiki 首页源码](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/home.md)
- 后端/主程序仓库：`jxxghp/MoviePilot` 是 NAS 媒体库自动化管理工具，README 明确其基于 NAStool 部分代码重新设计，聚焦自动化核心需求。[MoviePilot README](https://raw.githubusercontent.com/jxxghp/MoviePilot/v3/README.md)
- 前端仓库：`jxxghp/MoviePilot-Frontend` 是 MoviePilot 前端项目，基于 Vue 3、Vuetify 3、Vite，并支持插件远程组件动态加载。[MoviePilot-Frontend README](https://raw.githubusercontent.com/jxxghp/MoviePilot-Frontend/v3/README.md)
- 官方插件仓库：`jxxghp/MoviePilot-Plugins` 是官方插件仓库，也是默认插件市场的源码与索引仓库。[MoviePilot-Plugins README](https://github.com/jxxghp/MoviePilot-Plugins)
- 插件开发主文档：官方插件仓把 V3 插件开发指南定义为当前主指南，新插件应从该文档开始。[V3 插件开发指南](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- V2/V3 迁移专题：官方把 V2 迁移到 V3 的旧导入、事务、媒体身份、音乐链、数据迁移和合同差异集中在迁移文档中。[V2 插件迁移到 V3](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/V3_Plugin_Adaptation.md)
- 仓库与发布规则：官方插件仓将索引、版本选择、元数据、CI、Release、跨仓协作边界集中在仓库指南中。[Repository Guide](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Repository_Guide.md)

## 核心系统定位

- MoviePilot 的主线不是泛资源下载器，而是个人/NAS 媒体库自动化管理：订阅、搜索、下载、整理、刮削、媒体库刷新和消息通知构成核心流程。[MoviePilot README](https://raw.githubusercontent.com/jxxghp/MoviePilot/v3/README.md)
- 官方首页把 MoviePilot 称为“新一代智能化个人媒体库管理工具”，并把搜索、自动下载、智能重命名整理、媒体服务器集成、通知系统、内置智能助手列为关键能力。[官方首页](https://movie-pilot.org/)
- 后端采用 FastAPI，前端采用 Vue 3，官方 README 明确前后端分离让部署和扩展边界更清晰。[MoviePilot README](https://raw.githubusercontent.com/jxxghp/MoviePilot/v3/README.md)
- MoviePilot 可组合下载器、媒体服务器、元数据源、消息渠道、插件、工作流和 AI Agent 能力，说明插件不是外围小功能，而是系统扩展面的一部分。[MoviePilot README](https://raw.githubusercontent.com/jxxghp/MoviePilot/v3/README.md)
- 媒体识别是 MoviePilot 区别于一般搜索下载工具的核心：系统从资源名称提取关键字，到 TheMovieDb 或豆瓣等元数据源匹配；识别失败的资源不会进入下载、整理等后续处理。[Wiki 基础：媒体识别](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/basic.md)

## 部署与运行形态

- 官方安装文档推荐 Docker 部署，并说明 Docker 镜像内置虚拟显示、浏览器仿真、内建重启、代理缓存等能力。[Wiki 安装指引](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/install.md)
- V3 正式版 Docker 镜像是 `jxxghp/moviepilot-v3:latest`，也可使用具体版本号标签；容器示例暴露 `3000` 作为 Web/UI 端口，`3001` 作为 API 端口。[Wiki 安装指引：V3 Docker](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/install.md)
- V3 的典型持久化映射包括 `/config`、`/media`、浏览器内核目录和只读 Docker Socket；官方示例建议容器优雅停止时间设置为 `120` 秒，以便关闭后台任务、插件和其他运行模块。[Wiki 安装指引：V3 Docker](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/install.md)
- V3 全新启动不预置管理员用户、密码或 API Key，首次访问 Web 地址会进入初始化页面，由用户创建管理员并生成 API Key。[Wiki 安装指引：V3 首次初始化](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/install.md)
- V2 到 V3 可复用 V2 的 `/config`、SQLite 或 PostgreSQL 数据库，以及未使用 V3 变更合同的已安装插件；但 V3 首次启动仍会升级数据库结构，升级前必须备份。[V2 到 V3 总览：数据边界](https://raw.githubusercontent.com/jxxghp/MoviePilot/v3/docs/v2-to-v3-overview.md)
- V3 写入新结构后不能只把镜像换回 V2，因为统一媒体身份、新表/字段和删除的旧字段无法被 V2 完整理解；官方建议回退优先恢复升级前完整备份。[V2 到 V3 总览：回退边界](https://raw.githubusercontent.com/jxxghp/MoviePilot/v3/docs/v2-to-v3-overview.md)
- 数据库配置支持 `sqlite` 和 `postgresql`，PostgreSQL 支持从 v2.7.3+ 开始；SQLite 默认启用 WAL 以提升并发，但官方也提示异常情况下可能增加数据丢失风险。[Wiki 配置参考：数据库配置](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/configuration.md)
- Redis 是可选缓存后端，可减少主程序内存占用并迁移本地文件缓存；缓存类型通过 `CACHE_BACKEND_TYPE=redis` 与 `CACHE_BACKEND_URL` 配置。[Wiki 配置参考：Redis](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/configuration.md)
- 除 Docker 外，Wiki 首页列出 Windows、群晖和 macOS/Linux 本地 CLI 部署入口；本地 CLI 适合不使用 Docker 或需要本机管理前后端服务的场景。[Wiki 首页](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/home.md)、[Wiki 安装指引：本地 CLI](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/install.md)

## 核心模块与数据流

- V3 后端将职责拆为 Domain、Application、Chain、Module、Adapter、DB Oper/Adapter、Runtime、Startup、SDK/Compat 等层，目标是让媒体识别、应用能力、流程编排、外部服务适配、事务、插件和兼容层边界更清楚。[V2 到 V3 总览：分层结构](https://raw.githubusercontent.com/jxxghp/MoviePilot/v3/docs/v2-to-v3-overview.md)
- Domain 负责不依赖数据库和网络的媒体识别、命名、身份等业务规则；Application 表达“识别一部媒体”“保存一条订阅”等应用能力；Chain 把搜索、下载、整理、通知等能力串成完整流程。[V2 到 V3 总览：分层结构](https://raw.githubusercontent.com/jxxghp/MoviePilot/v3/docs/v2-to-v3-overview.md)
- Module 覆盖下载器、媒体服务器、元数据源和通知渠道等可替换实现；Adapter 对接 HTTP、Redis、文件系统、浏览器、Rust 和外部服务等技术连接。[V2 到 V3 总览：分层结构](https://raw.githubusercontent.com/jxxghp/MoviePilot/v3/docs/v2-to-v3-overview.md)
- 搜索数据流先由前端聚合搜索入口搜索媒体信息或人物，再按设置中的媒体数据源顺序展示结果；资源搜索时按“搜索站点”和过滤规则决定站点范围与全局过滤。[Wiki 搜索](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/search.md)
- 精确搜索依赖先识别媒体信息，结果只包含对应媒体的资源；模糊搜索直接展示站点返回数据，不经过过滤规则和优先级规则处理。[Wiki 搜索：精确/模糊](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/search.md)
- 订阅数据流是：添加订阅后 3 分钟内进行一次全订阅站点搜索；之后“订阅刷新”后台任务定期检查站点新增资源，逐个识别匹配并缓存结果，符合订阅则添加下载。[Wiki 订阅：订阅刷新](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/subscribe.md)
- 订阅有“自动”和“站点 RSS”两种模式；自动模式按随机间隔抓取站点种子列表并支持促销标识、做种数等数据，RSS 模式访问间隔可配置但不支持促销检测和做种数判定。[Wiki 订阅：订阅模式](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/subscribe.md)
- 下载数据流要求 MoviePilot 的下载目录与下载器使用目录一致，尤其 Docker 部署时必须把对应根目录映射进容器，否则文件会落到容器内部或路径无法对应。[Wiki 下载：下载目录](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/download.md)
- 手动下载也必须先识别资源，下载目录由系统识别资源后自动判定，不能在手动下载时直接指定。[Wiki 下载：手动下载](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/download.md)
- V2 文件整理已内建下载器监控和目录监控；下载器监控自动整理间隔为 5 分钟，目录监控为实时，但官方提醒网盘目录监控可能触发大量 API 请求导致流控。[Wiki 文件整理：V2 自动整理](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/reorganize.md)
- 文件整理方式包括硬链接、软链接、复制和移动；硬链接要求同一磁盘/存储空间/映射路径，软链接要求映射前后路径保持一致，移动会影响原文件做种。[Wiki 文件整理：整理方式](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/reorganize.md)
- 通知通道配置完成后可承担远程交互下载和订阅；若同时启用智能助手，消息入口还可承担智能助手远程交互能力。[Wiki 下载：远程下载](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/download.md)、[Wiki 订阅：远程订阅](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/subscribe.md)
- V3 增加音乐媒体类型，音乐沿用现有搜索、下载、订阅、历史和通知链路，不另建下载器或任务队列。[Wiki V3 版本说明：音乐流程](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/v3.md)

## 插件市场与插件包结构

- Wiki 插件页说明，配置 `PLUGIN_MARKET` 增加第三方插件仓库地址后即可在插件市场看到对应插件；插件市场下载和更新要求能连接 GitHub 并配置 GitHub Token。[Wiki 插件市场](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/plugin.md)
- 插件市场仓库地址仅支持 GitHub 仓库 `main` 分支，多个地址用逗号分隔；官方插件仓为 `https://github.com/jxxghp/MoviePilot-Plugins`。[Wiki 插件市场](https://raw.githubusercontent.com/jxxghp/MoviePilot-Wiki/main/plugin.md)
- 官方插件仓 README 明确自身不是独立运行时：`MoviePilot` 负责插件加载、事件分发、API、服务、数据、工作流和 Agent 运行时；`MoviePilot-Frontend` 负责配置页、详情页、仪表板和 Vue 联邦组件渲染；`MoviePilot-Plugins` 保存源码、市场索引、图标、测试、文档和发布流程。[MoviePilot-Plugins README](https://github.com/jxxghp/MoviePilot-Plugins)
- V3 新插件目录结构是 `plugins.v3/<plugin_id_lower>/`、`tests/v3/<plugin_id_lower>/` 和 `package.v3.json`；插件目录名必须是主类名的小写形式，主类必须在该目录的 `__init__.py` 中。[V3 插件开发指南：目录](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- V3 插件市场元数据写入 `package.v3.json`，键名使用插件 ID，常见字段包括 `name`、`description`、`labels`、`version`、`icon`、`author`、`level`、`system_version`、`history`。[V3 插件开发指南：最小插件](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- 代码中的 `plugin_version`、索引中的 `version`、`history` 顶部当前版本必须一致，历史记录应按语义版本从新到旧排列。[V3 插件开发指南：版本一致性](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- `release: true` 表示插件使用 GitHub Release 压缩包分发，Release Tag 采用 `插件ID_v插件版本号`，压缩包文件名采用 `插件目录小写_v插件版本号.zip`。[Repository Guide：发布流程](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Repository_Guide.md)
- MoviePilot 的插件 API 会动态注册到 `/api/v1/plugin/<PluginID>/<path>`，面向插件前端页面的接口通常使用 `bear` 认证，外部系统调用可使用 `apikey`，官方不建议无特殊原因匿名开放。[V2 历史开发指南：插件 API](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/V2_Plugin_Development.md)
- 插件可通过 `get_form()`/`get_page()` 输出 Vuetify JSON，也可通过 `get_render_mode()` 声明 Vue 联邦远程组件；复杂交互或完整页面适合 Vue 联邦模式。[V3 插件开发指南：页面和仪表板](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- 前端仓模块联邦指南要求构建后将 `dist` 产物上传到插件后端目录，默认 `dist/assets`；插件后端需返回 `("vue", "dist/assets")` 以集成远程组件。[MoviePilot-Frontend 模块联邦指南](https://raw.githubusercontent.com/jxxghp/MoviePilot-Frontend/v3/docs/module-federation-guide.md)

## V2 / V3 插件差异

- 官方明确“当前新插件统一面向 V3”，V2 插件开发指南只供仍维护 V2 实现的开发者参考。[V2 历史开发指南](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/V2_Plugin_Development.md)
- V2 版本选择规则是先读取 `package.v2.json`，找不到目标插件再检查 `package.json`，且 `package.json` 中只有显式声明 `"v2": true` 才视为 V2 兼容插件。[V2 历史开发指南：版本选择](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/V2_Plugin_Development.md)
- V2 专用实现放在 `plugins.v2/<plugin_id_lower>/` 并写入 `package.v2.json`；单实现跨版本兼容可继续放在 `plugins/` 并在 `package.json` 中声明 `"v2": true`。[V2 历史开发指南：版本选择](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/V2_Plugin_Development.md)
- V3 新插件应放在 `plugins.v3/` 并写入 `package.v3.json`，V3 回退加载旧实现只是为兼容已发布插件，不是新插件继续写入 `plugins/` 或 `plugins.v2/` 的理由。[V3 插件开发指南：V3 目录](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- V3 为插件整理了稳定 SDK，新插件应优先从 `app.sdk` 导入配置、事件、日志、缓存、媒体、网络、浏览器、数据库备份、插件、服务和通用工具能力。[V3 插件开发指南：稳定 SDK](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- V3 中 `app.core.*`、`app.helper.*`、`app.utils.*` 等旧导入由精确兼容层承接存量插件；新增或新发布插件不得继续使用这些旧路径。[V3 插件开发指南：兼容桥接](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- V3 后端统一媒体身份使用 `media_source + media_id`；订阅、下载任务、整理任务、识别结果、历史、媒体服务器事件和 Webhook 载荷中的主身份都遵守同一字段对，插件比较身份时不能只比较裸 `media_id`。[V2 插件迁移到 V3：统一媒体身份](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/V3_Plugin_Adaptation.md)
- V3 普通 JSON API 统一返回 `{success, message, data}` 三字段；但插件通过 `get_api()` 注册的路由不会被宿主隐式包装，插件可返回业务模型或显式返回 `Response[T]`，前端需按自身合同读取。[V2 插件迁移到 V3：REST 响应合同](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/V3_Plugin_Adaptation.md)
- V3 新建插件分身不复制源码，而是在实例专属模块命名空间中重新执行同一份源码；配置、结构化数据、数据目录、事件、动态 API 和定时服务按运行实例 ID 隔离，但进程级单例、固定端口、固定外部 Webhook 等仍需插件自己隔离。[V3 插件开发指南：虚拟分身兼容](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- V3 前端变化不是推倒重写，仍基于 Vue 3、Vuetify 3 和 Vite；关键变化是统一媒体身份、普通 API 响应、插件实例作用域和后台刷新生命周期。[V2 到 V3 总览：前端变化](https://raw.githubusercontent.com/jxxghp/MoviePilot/v3/docs/v2-to-v3-overview.md)

## 依赖管理风险

- 插件与主程序运行在同一个 Python 进程和依赖环境中，没有独立虚拟环境，所以插件必须控制第三方依赖、后台线程、全局状态和导入副作用。[V3 插件开发指南：运行位置](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- V3 插件额外 Python 依赖应写在插件目录的 `pyproject.toml` 中；`dynamic = ["version"]` 不表示插件版本来源，宿主只读取静态 `project.dependencies`。[V3 插件开发指南：第三方依赖](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- V1/V2 历史实现继续使用 `requirements.txt`，V3 插件不提交 `uv.lock`，也不应在插件代码中直接执行 pip 或 uv。[V3 插件开发指南：第三方依赖](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- 依赖安装在宿主共享环境，因此插件只应声明真正需要且宿主尚未提供的依赖，不能要求降级或覆盖 MoviePilot 核心依赖，并应尽量设置合理版本范围避免无上限升级破坏宿主。[V3 插件开发指南：第三方依赖](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- V3 会检查插件依赖，拒绝会降级或覆盖 MoviePilot 核心依赖的安装要求，以避免安装单个插件后整个主程序无法启动。[V2 到 V3 总览：插件依赖保护](https://raw.githubusercontent.com/jxxghp/MoviePilot/v3/docs/v2-to-v3-overview.md)
- 主程序依赖图本身较大，包含 FastAPI、Pydantic、SQLAlchemy、Starlette、Uvicorn、Redis、qBittorrent API、Transmission RPC、requests、httpx2 等运行依赖；插件依赖若与这些核心库冲突，影响面会覆盖整个宿主进程。[MoviePilot pyproject.toml](https://raw.githubusercontent.com/jxxghp/MoviePilot/v3/pyproject.toml)
- V3 插件异步 HTTP 应使用 `app.sdk.network.AsyncRequestUtils` 与 HTTPX2；直接依赖响应或异常类型的插件应导入 `httpx2`，不能用 `httpx.RequestError` 捕获 HTTPX2 异常，也不要调用 `httpx2.alias_httpx()` 全局改写 `httpx`。[V3 插件开发指南：异步 HTTP 客户端](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- 宿主依赖清单解析优先选择 `pyproject.toml`，其次才是 `requirements.txt`；`pyproject.toml` 会严格要求 `[project]`、非空 `project.name`、合法 `dynamic` 和静态字符串数组 `project.dependencies`，无效现代清单不会安全回退。[MoviePilot 插件依赖清单解析源码](https://raw.githubusercontent.com/jxxghp/MoviePilot/v3/app/adapters/system/plugin/manifest.py)

## 对当前自建插件库的启示

- 当前仓库已有 `plugins.v2/`、`plugins.v3/`、`package.v2.json`、`package.v3.json`，这个形态与官方插件仓 V2/V3 分代目录和索引结构一致；后续新增 V3 插件应默认进入 `plugins.v3/<插件主类小写>/` 并写入 `package.v3.json`。[本仓库 package.v3.json](../package.v3.json)、[V3 插件开发指南：目录](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- 当前 `package.v3.json` 已为插件声明 `system_version: ">=3.0.0"`，这与官方 V3 元数据示例一致；继续维护时应保持该字段与插件实际 API/SDK 使用范围一致。[本仓库 package.v3.json](../package.v3.json)、[V3 插件开发指南：最小插件](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- 当前 `plugins.v3/mediacovergenerator/` 使用 `pyproject.toml`，`plugins.v2/mediacovergenerator/` 使用 `requirements.txt`，这与官方“V3 用 pyproject、V1/V2 保留 requirements”的依赖代际规则一致。[本仓库 plugins.v3/mediacovergenerator/pyproject.toml](../plugins.v3/mediacovergenerator/pyproject.toml)、[V3 插件开发指南：第三方依赖](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- 当前 V2 和 V3 索引里的 `version` 都是 `0.9.7`，V3 历史说明写明“增加 V3 显式索引”；若 V3 代码开始使用 V3 独有 SDK、统一媒体身份、API 响应或依赖合同，建议按官方仓库指南把代际合同变化视为更高风险版本变化，而不是普通补丁说明。[本仓库 package.v2.json](../package.v2.json)、[本仓库 package.v3.json](../package.v3.json)、[Repository Guide：版本一致性](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Repository_Guide.md)
- V3 插件应优先复用 `app.sdk`、Oper、Chain 或宿主服务帮助类，不要直接读取或修改宿主配置文件、宿主 ORM Model、裸 SessionFactory 或宿主内部目录布局；这能降低 V3 后续分层调整对自建插件的破坏面。[V3 插件开发指南：稳定 SDK 与数据库边界](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- 插件运行数据应写入 `save_data()`、`get_data()` 或 `get_data_path()` 指向的数据目录，不应写回插件源码目录；这对当前媒体封面类插件尤其重要，因为源码目录可能在插件更新时被替换。[V3 插件开发指南：结构化数据与文件](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- 依赖约束应保持宽而有边界：不要固定会拖低宿主核心库的版本，也不要无上限依赖会破坏宿主的图像、HTTP、数据库或 AI 相关库；官方 V3 会拒绝降级/覆盖核心依赖，但插件仓仍应在提交前主动审查依赖冲突。[V3 插件开发指南：第三方依赖](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)、[V2 到 V3 总览：插件依赖保护](https://raw.githubusercontent.com/jxxghp/MoviePilot/v3/docs/v2-to-v3-overview.md)
- 若当前插件后续增加复杂配置页、历史页或任务工作台，应先判断是否真的需要 Vue 联邦组件；简单表单和轻量详情页用 Vuetify JSON 更贴近官方推荐，复杂交互或全页入口再使用 Vue 模式并遵守前端联邦构建、CSS 隔离和 `props.api` 调用规则。[Repository Guide：插件形态选择](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Repository_Guide.md)、[MoviePilot-Frontend 模块联邦指南](https://raw.githubusercontent.com/jxxghp/MoviePilot-Frontend/v3/docs/module-federation-guide.md)
- 发布前至少做三类校验：宿主虚拟环境下 `compileall`/`py_compile`，`check_plugin_versions.py` 检查索引版本与插件类版本一致，`git diff --check` 检查空白；若有 Vue 远程组件，还需 `yarn typecheck` 和 `yarn build` 并只提交联邦所需产物。[V3 插件开发指南：最小检查与真实加载](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)、[Repository Guide：校验建议](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Repository_Guide.md)
- 真实加载验证不能省略：官方强调仅语法编译无法发现导入路径、宿主依赖、API 注册、服务清理或前端加载问题；自建插件库在发布前应至少在真实 MoviePilot V3 宿主中安装、启动、禁用、重载一次。[V3 插件开发指南：真实加载检查](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)

## 维护检查清单

- 新增 V3 插件：目录 `plugins.v3/<id小写>/`、主类在 `__init__.py`、索引写 `package.v3.json`、测试放 `tests/v3/<id小写>/`。[V3 插件开发指南：目录](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- 维护 V2 插件：目录 `plugins.v2/<id小写>/`、索引写 `package.v2.json`，不要让 V2 改动反向破坏 V3 实现。[V2 历史开发指南：版本选择](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/V2_Plugin_Development.md)
- 迁移到 V3：优先替换旧导入为 `app.sdk`，检查统一媒体身份、数据库事务、API 响应、虚拟分身、HTTPX2 和依赖清单。[V2 插件迁移到 V3](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/V3_Plugin_Adaptation.md)
- 更新版本：同步插件类 `plugin_version`、对应 `package*.json` 的 `version`、`history` 顶部当前版本说明。[Repository Guide：版本一致性](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Repository_Guide.md)
- 管理依赖：V3 使用 `pyproject.toml` 的 `[project].dependencies`，不提交 `uv.lock`，不在插件代码中执行包管理器，不声明会降级或覆盖宿主核心依赖的约束。[V3 插件开发指南：第三方依赖](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Plugin_Development.md)
- 发布分发：需要 Release 压缩包时在索引声明 `release: true`，确认目录名、索引版本、代码版本和上次 Tag 后源码变化都满足官方工作流规则。[Repository Guide：发布流程](https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/docs/Repository_Guide.md)
