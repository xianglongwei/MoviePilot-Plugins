# MpEventBridge 0.1.0

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

## Offline verification

```bash
python -m unittest
python -m unittest discover -s tests/v3/mpeventbridge -v
```

Tests use lightweight SDK stubs and fake asynchronous clients. The fixed HMAC
vector was independently calculated with OpenSSL. No network, container,
deployment, 115 storage operation, STRM generation or media-server refresh is
part of implementation or testing. Live host route mounting/auth enforcement
and receiver mapping compatibility require a later authorized integration test.
The provided SDK snapshot names its transfer result schema
`TransferResultContractData`, not `TransferCompleteContractData`; the bridge
reads its documented payload fields without depending on either schema name.
