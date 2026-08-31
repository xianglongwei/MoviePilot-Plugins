# MoviePilot-Plugins
MoviePilot官方插件市场：https://github.com/jxxghp/MoviePilot-Plugins

### [媒体库封面生成](https://github.com/justzerock/MoviePilot-Plugins/tree/main/plugins.v2/mediacovergenerator)
  > 参考项目：https://github.com/HappyQuQu/jellyfin-library-poster

  在群里受到这个项目的启发，督促 AI 帮我写封面处理的代码，于是有了这个插件，支持切换风格

  ![插件界面](https://raw.githubusercontent.com/justzerock/MoviePilot-Plugins/main/images/plugin.webp)

### PigGo 儿童动画增强识别

  > 当前开发版本：`0.3.0`（第三阶段首版，本地待宿主验收）

  从一个或多个 PigGo RSS 或用户粘贴的磁力/下载链接建立候选，经用户确认后调用 MoviePilot 下载器，并按 hash 跟踪下载、扫描真实内容包中的 NFO、图片和文件名。高置信度结果会优先尝试 MoviePilot 的只读 TMDb 精确匹配，未命中时使用本地身份；自动整理默认关闭。V3 另外注册稳定的 `piggokids` 本地媒体来源。
