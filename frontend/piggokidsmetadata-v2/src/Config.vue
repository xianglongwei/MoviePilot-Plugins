<template>
  <VForm ref="form" @submit.prevent="save">
    <VAlert type="info" variant="tonal" class="mb-4">
      完整的
      RSS、候选和任务界面位于插件页面。插件提交的下载会由本地元数据自动识别并整理，私密下载链接不会写入持久化记录。
    </VAlert>

    <VSwitch v-model="config.enabled" label="启用插件" color="primary" />
    <VTextarea
      v-model="config.rss_urls"
      :class="{ 'secret-textarea': !showRssUrls }"
      :append-inner-icon="showRssUrls ? 'mdi-eye-off' : 'mdi-eye'"
      label="PigGo RSS 地址"
      hint="每行一个 HTTP/HTTPS 地址；含密钥时请不要截图或分享"
      persistent-hint
      rows="3"
      auto-grow
      @click:append-inner="showRssUrls = !showRssUrls"
    />
    <VAlert type="warning" variant="tonal" class="mt-3">
      不会定时或隐式刷新 RSS；只有你在工作台主动点击“刷新 RSS”时才会访问站点。
    </VAlert>
    <VRow class="mt-1">
      <VCol cols="12" md="6">
        <VTextField
          v-model="config.downloader"
          label="下载器（可留空使用默认）"
        />
      </VCol>
      <VCol cols="12" md="6">
        <VTextField
          v-model="config.download_save_path"
          label="下载保存目录（可留空）"
        />
      </VCol>
      <VCol cols="12" md="6">
        <VTextField
          v-model="config.scan_root"
          label="扫描根目录"
          hint="MoviePilot 容器内可访问的下载根目录"
          persistent-hint
        />
      </VCol>
      <VCol cols="12" md="6">
        <VTextField
          v-model.number="config.minimum_confidence"
          label="自动通过最低置信度"
          type="number"
          min="0"
          max="1"
          step="0.05"
        />
      </VCol>
      <VCol cols="12" md="6">
        <VTextField
          v-model.number="config.max_files"
          label="单次最多扫描文件数"
          type="number"
          min="100"
          max="50000"
        />
      </VCol>
    </VRow>
    <VSwitch
      v-model="config.public_match_enabled"
      label="启用只读 TMDb 精确匹配"
      color="primary"
    />
    <VSwitch
      v-model="config.auto_transfer"
      label="手工扫描的高置信度任务自动整理"
      color="warning"
      hint="RSS 或插件提交的下载固定由插件自动识别整理，不受此开关影响"
      persistent-hint
    />

    <div class="d-flex justify-end ga-2 mt-5">
      <VBtn variant="text" @click="$emit('switch')">打开工作台</VBtn>
      <VBtn variant="text" @click="$emit('close')">取消</VBtn>
      <VBtn color="primary" type="submit">保存</VBtn>
    </div>
  </VForm>
</template>

<script setup>
import { reactive, ref } from "vue";

const props = defineProps({
  initialConfig: { type: Object, default: () => ({}) },
  config: { type: Object, default: undefined },
});
const emit = defineEmits(["save", "close", "switch"]);
const form = ref(null);
const showRssUrls = ref(false);
const defaults = {
  enabled: false,
  rss_urls: "",
  downloader: "",
  download_save_path: "",
  scan_root: "",
  minimum_confidence: 0.8,
  max_files: 10000,
  public_match_enabled: true,
  auto_transfer: false,
};
const source = props.config || props.initialConfig || {};
const config = reactive({ ...defaults, ...source });

async function save() {
  const result = await form.value?.validate?.();
  if (result && !result.valid) return;
  emit("save", { ...config });
}
</script>

<style scoped>
.secret-textarea :deep(textarea) {
  -webkit-text-security: disc;
}
</style>
