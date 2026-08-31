# 0003: PigGo 儿童动画优先使用下载内容包识别

日期：2026-08-30

## Status

Proposed

## Context

用户希望下载完成后自动读取种子相关信息，判断是否能利用随包 NFO、海报和正确文件名完成儿童动画识别。MoviePilot 官方说明中，媒体识别失败会阻断下载、整理等后续处理；整理流程支持下载器监控、目录整理、失败历史和重新整理。MoviePilot V3 源码提供 `download_added`、`torrent_files`、`transfer_completed`、`MediaRecognize` 和 `TransferFailed` 等插件可利用的扩展点。

`.torrent` 是 BitTorrent metainfo，主要包含任务名、文件路径、文件大小、分片哈希和 tracker。它可以作为下载任务映射和文件列表来源，但不能直接提供 NFO 正文或海报图片内容。

## Decision

PigGo 儿童动画插件第一版优先实现下载后内容包扫描：

1. `DownloadAdded` 时保存下载 hash、下载器、下载目录、站点条目和种子元信息。
2. 下载完成或整理失败时扫描真实下载目录。
3. 优先解析 `*.nfo`、`poster.*`、`fanart.*`、字幕和媒体文件名。
4. 只有随包元数据不足时才补充读取 PigGo 详情页、公开元数据源和全网搜索候选。
5. 生成 `piggokids` 媒体身份，并通过 MoviePilot 的插件媒体来源、识别补充或手动整理链路继续入库。

## Consequences

这条路线更符合用户“已经下载下来了就读本地内容”的需求，也能最大限度利用发布包自带的 NFO、海报和准确文件名。代价是插件必须处理不同发布组的 NFO 格式、编码、图片命名、合集结构和低置信度冲突。`.torrent` 仍然有价值，但定位从“元数据事实源”降为“文件清单与任务关联源”。
