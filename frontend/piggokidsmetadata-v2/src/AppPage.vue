<template>
  <div class="piggo-app-page">
    <Workbench :api="api" :plugin-id="pluginId" @action="$emit('action')" />
  </div>
</template>

<script setup>
import Workbench from "./Workbench.vue";

defineEmits(["action"]);
defineProps({
  api: { type: Object, required: true },
  pluginId: { type: String, default: "PigGoKidsMetadata" },
  navKey: { type: String, default: "main" },
  nativeSubscribe: { type: Function, default: null },
});
</script>

<style scoped>
.piggo-app-page {
  padding: 20px;
  max-width: 1500px;
  margin: 0 auto;
}
@media (max-width: 600px) {
  .piggo-app-page {
    padding: 12px;
  }
}
</style>
