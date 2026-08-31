# PigGoKidsMetadata V3

当前版本 `0.3.0` 在 RSS/粘贴链接到下载后识别闭环上增加只读 TMDb 严格匹配，并修复并发提交、逐文件整理结算和 RSS 网络边界。它不会修改 TMDb，也不会直接移动文件；整理通过 MoviePilot 的整理链完成，且自动整理默认关闭。

已实现：

- 配置一个或多个 HTTP(S) RSS/Atom 地址，定时或手工刷新并稳定去重。
- 本地搜索候选，并导入磁力链接或 HTTP(S) 下载引用。
- 完整私密下载引用只保留在插件配置或当前进程内存；候选、任务、页面和普通日志只保存脱敏显示值与 SHA-256 指纹。
- 用户显式选择候选后，调用 MoviePilot 已配置的下载器，不保存下载器凭据。
- 监听下载加入、整理完成和整理失败事件，并每 5 分钟轮询下载器补偿漏事件或重启恢复。
- 下载完成后在配置的根目录内安全扫描真实内容包，解析 NFO、图片、字幕和媒体文件名。
- 多季混包、类型冲突、集数缺失和多正片电影进入待处理；高置信度项目才进入可整理状态。
- 高置信度项目优先调用 MoviePilot 的 TMDb 读取链；仅在标题或别名、类型以及已有年份/季信息不冲突时沿用 TMDb 身份。
- 从安全识别决策按需生成只读贡献草稿和相对路径证据清单；插件不会自动提交或修改 TMDb。
- 向 MoviePilot V3 注册 `piggokids` 媒体来源，支持登记表精确识别和本地搜索。
- 自动整理为显式开关，默认关闭；插件只向 MoviePilot 提交整理请求。

## 最小配置

1. 启用插件。
2. 填写 MoviePilot 容器内可见的下载根目录。
3. 可选填写 PigGo RSS 地址（多个地址每行一个）。
4. 可选指定 MoviePilot 下载器和保存目录。
5. 首次联调保持“自动整理”关闭，先刷新候选并手工选择一个样例。

没有 RSS 也可以使用 `POST /candidates/import` 粘贴磁力或下载链接。手工扫描仍可使用：

```text
POST /api/v1/plugin/PigGoKidsMetadata/scan
Authorization: Bearer <MoviePilot 登录态>
```

```json
{
  "relative_path": "Kids/Example.Show.S01",
  "site_item_id": "30876",
  "download_hash": "0123456789abcdef0123456789abcdef01234567"
}
```

扫描路径必须位于下载根目录内。`site_item_id` 和 `download_hash` 会先经过格式校验；URL、passkey 或 token 不会被当作身份字段保存。

## 当前验收边界

本地单元测试和轻量宿主合同测试已通过。正式启用前仍需在真实 MoviePilot V3 实例验证插件安装/重载、实际 PigGo RSS 字段、下载器返回值、容器路径映射、整理目标和媒体服务器入库。
