# PigGoKidsMetadata V2 前端

这是 MoviePilot V2.15.6 使用的 Vue 3 模块联邦前端，暴露：

- `./Config`：插件配置；
- `./Page`：插件管理详情页；
- `./AppPage`：左侧“整理”分组中的完整工作台。

构建要求为 Node.js 20 或更高版本。执行：

```bash
npm install
npm test
npm run build
```

构建产物会直接写入 `plugins.v2/piggokidsmetadata/dist/assets`。发布 V2 插件前必须确认其中存在 `remoteEntry.js`，并将该目录随插件一起提交；`node_modules` 不提交。
