# 0002: 将 PigGo 儿童动画实现为 MoviePilot 插件媒体来源

日期：2026-08-30

## Status

Proposed

## Context

用户希望实现自动闭环：当 MoviePilot 使用 TMDb 等默认来源无法识别儿童动画或儿童动画电影时，插件自动搜索和补全元数据，让 MoviePilot 可以继续整理入库。MoviePilot V3 支持插件扩展媒体来源，并在原生识别没有取得远端身份时通过 `ChainEventType.MediaRecognize` 让插件补充 `MediaInfo`。TMDb 官方 OpenAPI 没有创建或编辑影视基础资料的公开写入接口，可写范围主要是账号收藏、观看清单、列表、评分和认证会话。

## Decision

第一版把 PigGo 儿童动画插件设计为 MoviePilot 插件媒体来源，来源标识为 `piggokids`。插件优先从下载完成后的真实内容包读取 NFO、海报、字幕和媒体文件名；种子元信息只用于下载任务关联、文件清单和季集辅助判断；PigGo 详情页与可追溯公开来源作为补充。插件返回带 `media_source=piggokids` 与稳定 `media_id` 的 `MediaInfo`。TMDb 只作为优先读取来源和可选贡献目标；插件不自动提交 TMDb，只生成贡献草稿或提醒。

## Consequences

这条路线可以让 MP 在 TMDb 缺失时继续整理，不必等待 TMDb 社区数据新增、审核或缓存更新。代价是插件需要维护自己的媒体身份、下载后扫描器、NFO/图片解析、搜索/识别模块、置信度规则和冲突暂停机制。若用户未来强烈要求自动向 TMDb 投稿，需要另行评估 TMDb 账号规则、网页提交风险和数据质量责任。
