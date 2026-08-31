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
    emptyOutDir: true,
    outDir: "../../plugins.v2/piggokidsmetadata/dist/assets",
    assetsDir: "",
  },
});
