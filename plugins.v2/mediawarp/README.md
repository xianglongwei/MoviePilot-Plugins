# MediaWarp 多服务器 1.1.0

基于 [DDSRem 的 MediaWarp 插件 1.0.8](https://github.com/DDSRem-Dev/MoviePilot-Plugins/tree/main/plugins.v2/mediawarp) 改造，实际代理使用 [DDSRem-Dev/MediaWarp](https://github.com/DDSRem-Dev/MediaWarp) 0.1.12。感谢原作者 DDSRem、Akimio521。适用于 Linux amd64/arm64 的 MoviePilot V2 容器。

## 安装和更新

1. 在 MoviePilot 插件市场添加仓库 `https://github.com/xianglongwei/MoviePilot-Plugins`，刷新市场。
2. 安装“MediaWarp 多服务器”；已安装原版时使用同一个插件 ID `MediaWarp`，更新到 `1.1.0`。多个市场源同时提供该插件时，请确认更新来自本仓库。
3. 在 MoviePilot 设置 → 媒体服务器中配置 Emby/Jellyfin 的地址和 API Key。地址必须从 MoviePilot 容器内可达，不能指向本插件的反代端口。
4. 打开插件配置，勾选需要反代的服务器。每台服务器设置不同的端口，例如主服务器 `9001`、第二台 `9002`；未勾选服务器不会启动。
5. STRM 路径填写 Emby/Jellyfin 中看到的媒体库路径，一行一个；留空关闭该服务器 HTTP STRM 优化。无需向 MoviePilot 挂载 STRM 目录。
6. 启用并保存。首次启动自动下载 MediaWarp，需要容器能访问 GitHub Release；失败时修复网络后重新保存插件配置。

bridge 网络必须在 **MoviePilot 容器** 上增加 `9001:9001`、`9002:9002` 等映射；host 网络直接开放对应端口。每个端口均需避开 MoviePilot 和其他服务占用的端口。

之后分别用 `http://MoviePilot主机IP:9001` 和 `http://MoviePilot主机IP:9002` 登录对应的 Emby。可在现有 Nginx/NPM 等反代中给各端口配置不同域名及 HTTPS；WebSocket 也应放行。

## 行为

- 每台服务器独立配置端口、STRM 路径、CRX 美化、头像过滤、同人图、外置播放器、弹幕、共同观影、SRT 转 ASS。
- 每台服务器启动一个 MediaWarp 进程；配置、日志、静态目录分别隔离。不是将多个服务器合并成一个媒体库，也不做负载均衡。
- 保存配置会停止并等待所有旧进程退出，再启动新实例；关闭插件停止所有实例。
- 每 15 秒检查子进程；异常退出（包括退出码 0）时，两次启动之间至少间隔 60 秒重试，避免不断重启。
- 保留原服务器转码设置默认开启；播放仍受上游、客户端、STRM 链接和网盘直链限制。
- 新实例不会开启上游示例配置中的演示 Alist 连接或额外脚本。

## 旧版迁移和高级配置

旧版 `port`、`media_strm_path` 和功能开关仅作为第一台已选服务器的初始值；其余服务器必须单独填写端口。保存后使用按服务器名称生成的稳定字段；修改服务器名称后需重新填写设置。

数据保存在 MoviePilot 插件数据目录下的 `mediawarp/`：

```text
mediawarp/
  MediaWarp                    # 共享下载源；每个实例使用独立物理副本
  version.txt
  config/config.yaml           # 保留旧版配置，首次迁移时读取
  servers/server_<hash>_instance/
    MediaWarp
    config/config.yaml         # 含该服务器 API Key，不要公开上传
    static/                    # 独立自定义静态资源
    logs/                      # MediaWarp 日志
    console.log                # 本次启动输出
    console.previous.log       # 上一次启动输出
```

详情页显示服务器端口、进程状态和实例目录。新启动后最多等待 15 秒更新端口就绪状态。

第一次迁移会复制旧 `config/config.yaml` 和 `config/static/` 到第一台服务器实例。现有高级配置保留，插件管理的端口、上游地址/API Key、日志、HTTP STRM 选项和页面功能开关每次保存时更新。后续请编辑对应实例的配置和静态目录。自定义 `static/index.html` 存在时自动启用。

配置文件语法或结构错误时不覆盖原文件；该实例启动失败，其余实例继续启动。手工修改实例高级配置后，重新保存插件配置使其生效。日志没有自动清理，请定期检查磁盘占用。

## 测试

仓库提供 `tests/test_mediawarp.py`，测试配置隔离、端口校验、迁移、进程重启/停止、损坏配置保护和下载解包路径保护。MoviePilot 宿主接口使用测试替身；真实 YAML、进程等待逻辑使用实际依赖。

```bash
python -m pip install ruamel.yaml psutil APScheduler requests
python -m unittest discover -s tests -p test_mediawarp.py -v
```

本版本需要在真实 MoviePilot 容器中验收：两台服务器分别登录、WebSocket、STRM 302 直链、普通媒体播放/转码，以及关闭插件后端口释放。
