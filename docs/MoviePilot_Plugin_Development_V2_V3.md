# MoviePilot 插件开发手册：V2 / V3 双版本维护

本文主要是本仓库维护 `MediaCoverGenerator` 插件的工作手册。它不是官方文档的复制版，而是基于 MoviePilot Wiki 和官方插件仓开发文档整理出的落地规则，用来约束后续修改、发布和排错。计划新增的 `PigGoKidsMetadata` 以 `docs/PigGoKidsMetadata_Product_Requirements.md` 为需求基线，并复用本文的双版本、依赖和发布规则。

## 资料入口

- MoviePilot Wiki 插件页：https://wiki.movie-pilot.org/zh/plugin
- 插件开发步骤：https://wiki.movie-pilot.org/plugindev
- V3 主开发指南：https://github.com/jxxghp/MoviePilot-Plugins/blob/main/docs/Plugin_Development.md
- V2 历史开发指南：https://github.com/jxxghp/MoviePilot-Plugins/blob/main/docs/V2_Plugin_Development.md
- V2 迁移到 V3：https://github.com/jxxghp/MoviePilot-Plugins/blob/main/docs/V3_Plugin_Adaptation.md
- V3 API 响应专题：https://github.com/jxxghp/MoviePilot-Plugins/blob/main/docs/V3_API_Response_Adaptation.md
- 仓库与发布指南：https://github.com/jxxghp/MoviePilot-Plugins/blob/main/docs/Repository_Guide.md

## 本仓库目标

当前已发布插件只有 `MediaCoverGenerator`，展示名为 `Emby媒体库封面生成`。计划新增 `PigGoKidsMetadata` 后，仓库仍只保留用户自己维护的这两个插件，不恢复任何上游无关插件。两个插件都必须让 MoviePilot V2 和 V3 正确发现、安装并加载。

当前采用双目录、双索引策略：

```text
MoviePilot-Plugins/
├── package.v2.json
├── package.v3.json
├── plugins.v2/
│   └── mediacovergenerator/
├── plugins.v3/
│   └── mediacovergenerator/
├── icons/
├── fonts/
└── images/
```

V2 插件市场读取 `package.v2.json`，源码位于 `plugins.v2/mediacovergenerator/`。V3 插件市场读取 `package.v3.json`，源码位于 `plugins.v3/mediacovergenerator/`。两边的插件类名仍然是 `MediaCoverGenerator`，目录名必须保持类名小写。

## V2 与 V3 的关键差异

| 事项 | V2 | V3 |
| --- | --- | --- |
| 插件目录 | `plugins.v2/<id_lower>/` | `plugins.v3/<id_lower>/` |
| 市场索引 | `package.v2.json` | `package.v3.json` |
| 依赖文件 | `requirements.txt` | `pyproject.toml` |
| 推荐导入 | 可继续使用 V2 旧路径 | 新代码优先使用 `app.sdk.*` |
| API 普通响应 | 历史插件常见自定义字典 | 明确选择裸业务模型或 `schemas.Response[T]` |
| 媒体身份 | 旧插件常见 `tmdbid` / `doubanid` | 通用链路使用 `media_source` + `media_id` |
| 数据库访问 | 旧插件可能直接使用宿主 Model | 使用 Oper、Chain、SDK；事务装饰器只用于插件自有表 |

## 插件类必须满足的生命周期

插件主类继承 `app.plugins._PluginBase`。每次修改都要优先保证这些方法行为稳定：

- `init_plugin(config)`：可重复调用，负责读取配置、初始化运行态。
- `get_state()`：返回插件是否启用。
- `get_api()`：声明插件后端 API；没有时返回空列表。
- `get_form()`：返回配置页面 JSON 和默认配置模型。
- `get_page()`：返回插件详情页；没有内容时返回空列表或 `None`。
- `stop_service()`：清理调度器、线程、连接、文件句柄等后台资源。

不得在模块导入阶段启动定时任务、访问网络、连接数据库或写入运行数据。插件启用、停用、重载和安装升级都会反复触发生命周期，代码必须能承受重复初始化。

## 配置、数据和文件

用户配置只通过基类方法保存：

```python
self.update_config({
    "enabled": self._enabled,
})
```

结构化小数据优先使用 `save_data()` / `get_data()` / `del_data()`。图片、缓存、报告和字体等文件写到 `self.get_data_path()` 返回的数据目录，不写回插件源码目录。源码目录会被插件升级覆盖，运行数据放进去很容易丢失。

支持插件分身时，不要硬编码插件 ID 去读写配置和数据；优先使用当前运行类名或基类方法的默认实例作用域。

## 依赖管理规则

MoviePilot 插件和主程序共享同一个 Python 环境。插件没有独立虚拟环境，所以依赖声明必须保守。

本插件依赖：

```text
pillow>=11.2.1
numpy>=2.2.0
pytz>=2025.2
pyyaml>=6.0.2
```

维护规则：

- 不使用 `==` 锁死版本，避免和 MoviePilot 主程序已有依赖冲突。
- 不声明会降级主程序核心库的依赖。
- 新增依赖前确认代码确实使用，且宿主没有内置等价能力。
- 可选能力应延迟导入，缺依赖时给出清楚错误。
- V2 写入 `requirements.txt`；V3 写入 `pyproject.toml` 的 `[project].dependencies`。
- 不提交 `uv.lock`，也不在插件代码里执行 `pip` 或 `uv`。

## V3 迁移与兼容要求

当前 V3 目录是为了让 V3 插件市场显式发现插件。后续做真正 V3 原生化时，按以下顺序改：

1. 替换旧导入。优先从 `app.sdk.config`、`app.sdk.events`、`app.sdk.logging`、`app.sdk.network`、`app.sdk.services`、`app.sdk.utilities` 获取宿主能力。
2. 检查 API。`get_api()` 中普通 JSON endpoint 要声明明确的 `response_model`，并决定返回裸业务模型还是 `schemas.Response[T]`。
3. 检查宿主 API 调用。调用 MoviePilot 普通 REST API 时从 `success/message/data` envelope 中读取业务数据。
4. 检查媒体身份。涉及识别、搜索、订阅、下载、整理、刮削、媒体库事件时，通用主身份必须成对使用 `media_source` 和 `media_id`。
5. 检查数据库。不要直接导入或操作宿主 ORM Model；宿主数据通过 Oper、Chain 或 SDK 访问。插件自有表才使用公共事务装饰器。
6. 在 V3 宿主启用 `DEBUG=true`，确认没有可迁移的旧导入警告。

如果 V3 代码开始依赖 V3 独有合同，必须保留 V2 目录不动，并只在 `plugins.v3/` 中改造；不要为了 V3 迁移顺手破坏 V2。

## API 和前端页面规则

插件 API 最终路径是：

```text
/api/v1/plugin/MediaCoverGenerator/<path>
```

认证选择：

- 插件页面调用通常使用 `bear`。
- 外部系统调用可使用 `apikey`。
- 不要默认匿名开放 API。

V3 普通 JSON API 有两种可选合同：

- 直接返回业务模型，前端直接读返回对象。
- 返回 `schemas.Response[T]`，前端按 `success/message/data` 读取。

不要把业务数据塞进 `message`，不要返回双层 `data`，不要在失败消息里暴露 Token、Cookie、绝对路径或异常堆栈。

当前插件使用 Vuetify JSON 模式，不需要 Vue 模块联邦。如果将来改成 Vue 远程组件，必须避免打包 Vuetify/MDI 全局样式，并使用宿主注入的 `api` 或 `window.MoviePilotAPI`，不要自行创建绕过认证和统一反馈的 HTTP 客户端。

## 发布与索引规则

每次发布至少同步三处：

- 插件类里的 `plugin_version`
- `package.v2.json` / `package.v3.json` 中的 `version`
- `history` 顶部的当前版本说明

当前发布状态下，索引文件中只有一个顶层键：

```json
{
    "MediaCoverGenerator": {}
}
```

`PigGoKidsMetadata` 实现并通过验收后，V2/V3 索引将各新增其顶层键；在实现前不得提前发布空索引项。

MoviePilot 插件市场只读取 GitHub 仓库的 `main` 分支。修改完成后必须推送到 `main`，否则用户刷新插件市场看不到更新。

## 本插件修改前检查清单

- [ ] 修改前确认目标是 V2、V3，还是两边都要改。
- [ ] 依赖变更同时更新 V2 `requirements.txt` 和 V3 `pyproject.toml`。
- [ ] 代码版本、索引版本和 `history` 一致。
- [ ] 资源链接指向 `xianglongwei/MoviePilot-Plugins`，不要重新指回上游仓库。
- [ ] 运行数据只写入 `get_data_path()`。
- [ ] `stop_service()` 能清理后台任务。
- [ ] API 不匿名暴露，响应合同明确。
- [ ] V3 专属改造不反向破坏 V2。
- [ ] 仓库里只保留本插件相关目录和资源。

## 最小校验命令

没有完整 MoviePilot 宿主时，至少执行：

```bash
python -m compileall plugins.v2/mediacovergenerator
python -m compileall plugins.v3/mediacovergenerator
python -c "import json; json.load(open('package.v2.json', encoding='utf-8')); json.load(open('package.v3.json', encoding='utf-8'))"
git diff --check
```

有真实宿主时，还要在 V2 和 V3 环境各验证一次：

- 插件市场能发现插件。
- 安装不会因为依赖冲突被拒绝。
- 配置页能打开并保存。
- 立即生成、定时生成、停用重载都正常。
- V3 `DEBUG=true` 下没有必须迁移的旧导入警告。
