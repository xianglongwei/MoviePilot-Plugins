# MpEventBridge 0.1.1

For MoviePilot v3. Enable the plugin and configure the same signing secret as
p115-302. `endpoint` is an HTTP(S) origin, defaulting to
`http://192.168.10.25:7811`; the plugin appends `/api/hooks/mp`.

The four form models are `enabled` (false), `endpoint`, `secret` (empty), and
`debounce_seconds` (45). MoviePilot namespaces the form with
`plugin_config_prefix = "mpeventbridge_"`; persisted configuration uses the bare
model names, as in the SDK reference plugins. Configuration is loaded/saved with
`get_config()` / `update_config()` and takes effect without restarting MoviePilot.
An enabled plugin with an empty secret or invalid endpoint stays inactive.

## Wire mapping

Only the five fields defined in `mp-hook-contract.md` are sent:

| Field | Source |
| --- | --- |
| `event` | `TransferComplete` → `transfer.complete`; `MetadataScrape` → `metadata.scrape` |
| `target` | `transferinfo.target_dir`, then the SDK's destination `transferinfo.target_diritem.path`, then `fileitem.path`, then the common parent of `file_list` files |
| `episodes` | `meta.season` / `meta.episode`, falling back to the union of `file_contexts[*].meta`; otherwise `[]` |
| `idempotency_key` | Host payload key, otherwise `mp-` plus SHA1 of `event\|target\|episodes` with canonical JSON episodes |
| `occurred_at` | Finite payload timestamp, otherwise local Unix seconds at normalization |

Paths use purely lexical POSIX operations. Files become their parent directories;
`FileItem.type == "dir"` keeps the directory itself. `/115` is stripped only as
a leading path segment, and trailing slashes are removed except for `/`.
Relative or missing targets are ignored. There is no directory lookup.

## Delivery and lifecycle

Event handlers normalize and schedule a thread-safe, nonblocking queue insertion;
they never wait for HTTP. One worker merges by normalized target. The default
45-second quiet window resets on each arrival; episode pairs are sorted and
deduplicated. When different event kinds share a target, the latest event kind
is retained. Merged events receive a newly computed content key and the latest
`occurred_at`.

The worker signs the exact UTF-8 bytes passed to `httpx.AsyncClient.post(content=...)`:
`X-MP-Timestamp` contains sending-time Unix seconds and `X-MP-Signature` contains
`sha256=` plus HMAC-SHA256(secret, timestamp + `.` + raw body). Every retry refreshes
the timestamp/signature while retaining the same body and idempotency key.
The client timeout is 5 seconds. Three retries follow the initial attempt, after
1, 5 and 30 seconds. Only HTTP 200 with `ok: true` counts as success, including
`reason: duplicate`. Other results retry, then produce one failure record and
one warning before the event is discarded.

Initialization uses an existing running event loop when available, otherwise a
plugin-owned daemon thread runs its asyncio loop. Queue mutations and counters
stay on that loop. Reload cancels and awaits preceding workers before opening a
new client. `close()` schedules cancellation and awaits worker/client cleanup;
its returned task/future can also be awaited by asynchronous callers. A
synchronous host need not await it for cleanup to run. `stop_service()` cancels
the worker while retaining an owned loop for subsequent reinitialization;
`close()` also stops the owned loop.

Queue contents and debounce state are in memory and are dropped on configuration
reload, disable, shutdown or process restart. The receiver's TTL scan remains the
fallback. Counts and the last 20 completed deliveries are also in memory, with
counts retained across configuration reloads on the same plugin instance.
`received` counts normalized events actually admitted to the queue; `merged`
counts arrivals combined into an existing pending target; `sent_success` and
`failed` count completed logical deliveries, not HTTP attempts. Cancelled work
is not counted as a failed delivery.

## Authenticated diagnostics

Both routes use the host's `auth: "bear"` bearer-token dependency. No plugin-specific
authentication is added. Replace the variables below with an existing MP address,
MP login access token and a directory covered by the receiver's mappings.
These commands are usage examples and were not run as part of implementation.

```bash
curl -H "Authorization: Bearer $MP_ACCESS_TOKEN" \
  "$MP_URL/api/v1/plugin/MpEventBridge/state"

curl -X POST -G -H "Authorization: Bearer $MP_ACCESS_TOKEN" \
  --data-urlencode 'target=/115/link/电视剧/某剧/Season 1' \
  "$MP_URL/api/v1/plugin/MpEventBridge/test"
```

`state` returns `enabled`, `config_error`, `counts` and `records`. Each record has
`time`, `event`, `target`, `http_status` (null for transport failure), and `result`.

`test` takes an optional query parameter `target` and returns the queue admission
result and idempotency key immediately. The synthetic `metadata.scrape` event
uses empty episodes and a unique test key, then follows normal normalization,
queueing, debounce, signing and retry. Check `state` after the debounce window
for the delivery result. With no parameter it targets
`/115/link/__mp_event_bridge_test__`; an unmapped directory returns `unknown_dir`
and counts as failure even though its HTTP status is 200. A disabled plugin
returns `ok: false`, `queued: 0`, `reason: disabled` without starting a worker.

## 入站订阅进度回写

`POST /api/v1/plugin/MpEventBridge/progress` 声明 `auth: "bear"`，使用 MP 的
鉴权依赖，不使用出站事件的 HMAC 密钥。入站回写不依赖出站 `enabled` 开关，
也不需要启动事件队列或事件循环。请求字段沿用 `mp-progress-contract.md`：

```http
POST /api/v1/plugin/MpEventBridge/progress
X-API-KEY: <MP API_TOKEN>
Content-Type: application/json

{"sub_id":1573,"season":4,"episodes":[1,2,3,4],"origin":"p115-302","observed_at":1793585616.0}
```

`sub_id` 是订阅 ID；`season: 0`（或省略）使用订阅自身季号。季号缺失或与订阅
不匹配时返回 `skipped / no_season`，避免把其他季的进度合并到当前订阅。
`episodes` 是该季 Emby 实存集号的整数数组，允许重复、无序或空数组；其余字段忽略。
只处理电视剧及 `total_episode > 0` 的订阅；任何传入集号超过总集数时整条拒绝，
不写库、不识别、不完成，返回 `skipped / episode_out_of_range`。

更新算法保留原有 note 中的数字，与 `episodes ∩ {start_episode or 1 ... total_episode}`
取并集后排序；缺集数是预期集号与新 note 的差集大小。已有 note 中超出预期的数字保留，
但不参与补齐缺集。只写发生变化的 `note` / `lack_episode` 字段。
**空数组保留原有 note，只重算缺集数**：这遵循契约“插件侧语义”第 4 条的并集算法；
契约请求字段说明中的“清空 note”与此矛盾，本实现不清空。

处理结果默认 HTTP 200，响应示例：

```json
{"ok":true,"state":"updated","lack_episode":4,"reason":null,"note":[1,2,3,4]}
```

| state | ok | 语义 / reason |
| --- | --- | --- |
| `updated` | true | 已合并（可能无需写库）；普通未齐全时 `reason: null` |
| `finished` | true | 齐全后识别媒体，并调用 MP 完成链；携带 `note`、`lack_episode: 0` |
| `updated` | true | `pending`：状态 P；`best_version`：洗版；均只更新、不完成 |
| `skipped` | true | `not_tv`、`no_total_episode`、`no_season`、`episode_out_of_range` |
| `not_found` | false | 订阅不存在，响应只有 `ok` 和 `state` |
| `error` | false | 内部异常，`reason` 为异常文本的前 200 字符 |

齐全且非洗版、非 P 时，使用 `build_subscribe_meta` 和订阅身份参数进行一次
`MediaChain().recognize_media(..., cache=False)`，随后调用
`SubscribeChain().finish_subscribe_or_not(..., downloads=None, lefts={}, force=True)`。
暂停 S 允许完成。媒体识别失败返回 `error`，不调用完成链；进度写入可能已经成功，
下一次相同推送会重试完成。完成链抛异常也返回 `error`，不会向调用方抛出 500。
鉴权失败以及 FastAPI 对缺失/非对象 JSON body 的校验错误发生在处理函数之前，
仍使用宿主 HTTP 状态码。

重复推送未齐全的相同集合仍返回 `updated`，不重复写库；P / 洗版同理。
成功移入历史后活动订阅已删除，重推同一 ID 返回 `not_found`，不再次完成。
因此契约中“幂等”指进度和完成副作用不会重复，完成迁移后的响应状态会改变。
同一进程内的回写请求用同步锁串行执行读取、合并、写库和完成；此锁不覆盖 MP
核心或其他插件的写入，也不跨多个 MP 进程。
MP 完成链还允许其他插件通过 `SubscribeCompletionCheck` 否决迁移且不抛异常；
本接口按契约在完成链正常返回后报告 `finished`，没有额外查询历史确认这类否决。

本次快照的 `app/api/endpoints/plugin.py` 将路由注册委托给
`app.application.plugin.routes.register_plugin_api`，但未包含该模块源码，
所以暂无法从快照核实实际路由包装及 `auth: "bear"` 的依赖映射。
离线测试验证了 FastAPI 直接挂载时的 JSON body 和 HTTP 200 异常响应；
真实宿主挂载及 `X-API-KEY` 支持仍需补齐注册模块后核实。

## Offline verification

```bash
python3 -m unittest
python3 -m unittest discover -s tests/v3/mpeventbridge -v
```

0.1.1 离线验证：插件目录命令输出 57 行，`Ran 52 tests` / `OK`；
仓库根命令输出 5 行，`Ran 144 tests` / `OK (skipped=2)`。
两项跳过均为 `test_subtitle_plugins.HostSmokeTests` 中原有的宿主环境测试，
本地未安装 MP；两条命令退出码均为 0。

Tests use lightweight SDK stubs and fake asynchronous clients. The fixed HMAC
vector was independently calculated with OpenSSL. No network, container,
deployment, 115 storage operation, STRM generation or media-server refresh is
part of implementation or testing. Live host route mounting/auth enforcement
and receiver mapping compatibility require a later authorized integration test.
The provided SDK snapshot names its transfer result schema
`TransferResultContractData`, not `TransferCompleteContractData`; the bridge
reads its documented payload fields without depending on either schema name.

## 安装要求（v3 实测记录）

MoviePilot v3 **不会**因为目录存在就加载插件：

- `UserInstalledPlugins` 只回答“包在磁盘上”，装载判据是**插件实例表的启用位**
  （`runtime.py`: `loadable_plugins=lambda: list(instances.enabled_hosts())`）。
- 直接把 `plugins.v3/mpeventbridge/` 拷进容器的 `/app/app/plugins/` 并写
  `systemconfig['plugin.MpEventBridge']`，`POST /api/v1/plugin/reload/MpEventBridge`
  仍返回 `插件 MpEventBridge 加载失败：加载器未返回匹配的插件类`——因为没有任何实例记录。
  用同一套 `PluginLoader(plugins_root=/app/app/plugins)` 单独调用却能正常返回类，
  说明目录和代码没问题，缺的是实例登记。
- 正确路径：通过插件市场安装（`package.v3.json` 被 MP 读取 → 创建实例 → 启用），
  或让 UI 里的安装动作创建实例；命令行硬塞目录不可行。
