import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import federation from "@originjs/vite-plugin-federation";

export default defineConfig({
  plugins: [
    vue(),
    federation({
      name: "PigGoKidsMetadata",
      filename: "remoteEntry.js",
      exposes: {
        "./Config": "./src/Config.vue",
        "./Page": "./src/Page.vue",
        "./AppPage": "./src/AppPage.vue",
      },
      shared: {
        vue: { requiredVersion: false, generate: false },
        vuetify: { requiredVersion: false, generate: false, singleton: true },
        "vuetify/styles": {
          requiredVersion: false,
          generate: false,
          singleton: true,
        },
      },
      format: "esm",
    }),
  ],
  build: {
    target: "esnext",
    minify: true,
    cssCodeSplit: true,
    // 保留上一版带哈希的联邦分包，兼容浏览器仍缓存旧 remoteEntry.js 的场景。
    emptyOutDir: false,
    outDir: "../../plugins.v2/piggokidsmetadata/dist/assets",
    assetsDir: "",
  },
});
