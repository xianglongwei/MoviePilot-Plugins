<template>
  <Workbench
    :api="api"
    :plugin-id="pluginId"
    compact
    @action="$emit('action')"
    @switch="$emit('switch')"
  />
</template>

<script setup>
import Workbench from "./Workbench.vue";

defineEmits(["action", "switch", "close"]);
defineProps({
  api: { type: Object, required: true },
  pluginId: { type: String, default: "PigGoKidsMetadata" },
  nativeSubscribe: { type: Function, default: null },
});
</script>
