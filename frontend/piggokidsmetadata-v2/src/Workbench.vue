<template>
  <div class="piggo-workbench">
    <div class="d-flex flex-wrap align-center ga-3 mb-4">
      <div>
        <div class="text-h5 font-weight-bold">PigGo 儿童内容工作台</div>
        <div class="text-body-2 text-medium-emphasis">
          候选发现、下载跟踪和本地自动识别整理
        </div>
      </div>
      <VSpacer />
      <VChip :color="status.enabled ? 'success' : 'warning'" variant="tonal">
        {{ status.enabled ? "插件已启用" : "插件未启用" }}
      </VChip>
      <VBtn v-if="compact" variant="text" @click="$emit('switch')">
        插件设置
      </VBtn>
      <VBtn
        :loading="loading"
        prepend-icon="mdi-refresh"
        variant="tonal"
        @click="loadAll"
        >刷新</VBtn
      >
    </div>

    <VAlert
      v-if="error"
      type="error"
      variant="tonal"
      closable
      class="mb-4"
      @click:close="error = ''"
    >
      {{ error }}
    </VAlert>
    <VAlert
      v-if="!status.enabled && !loading"
      type="warning"
      variant="tonal"
      class="mb-4"
    >
      请先在插件设置中启用插件；工作台当前仅展示已有数据。
    </VAlert>

    <VRow class="mb-2">
      <VCol v-for="metric in metrics" :key="metric.label" cols="6" sm="3">
        <VCard variant="tonal" class="metric-card">
          <VCardText>
            <div class="text-caption text-medium-emphasis">
              {{ metric.label }}
            </div>
            <div class="text-h5 mt-1">{{ metric.value }}</div>
          </VCardText>
        </VCard>
      </VCol>
    </VRow>

    <VTabs v-model="tab" color="primary" show-arrows>
      <VTab value="candidates">候选资源</VTab>
      <VTab value="tasks">任务与审核</VTab>
      <VTab value="drafts">TMDb 草稿</VTab>
    </VTabs>

    <VWindow v-model="tab" class="mt-4">
      <VWindowItem value="candidates">
        <VCard variant="outlined" class="mb-4">
          <VCardTitle class="text-subtitle-1">手工下载</VCardTitle>
          <VCardText>
            <VRow>
              <VCol cols="12" md="6">
                <VTextField
                  v-model="importForm.title"
                  label="标题（可选）"
                  hide-details
                />
              </VCol>
              <VCol cols="12" md="3">
                <VSelect
                  v-model="importForm.media_type"
                  :items="mediaTypes"
                  label="媒体类型"
                  hide-details
                />
              </VCol>
              <VCol cols="12">
                <VTextarea
                  v-model="importForm.download_reference"
                  :class="{ 'secret-textarea': !showDownloadReference }"
                  :append-inner-icon="
                    showDownloadReference ? 'mdi-eye-off' : 'mdi-eye'
                  "
                  label="HTTP/HTTPS 或 BTIH 磁力链接"
                  rows="2"
                  auto-grow
                  hint="只在当前进程内短暂保留完整链接；持久化记录仅保存不可逆指纹"
                  persistent-hint
                  @click:append-inner="
                    showDownloadReference = !showDownloadReference
                  "
                />
              </VCol>
              <VCol cols="12">
                <VTextField
                  v-model="importForm.poster_url"
                  label="封面 HTTPS 地址（可选）"
                  hint="优先匹配已有 RSS 缓存；匹配不到时可填写公开封面地址，不会刷新 RSS"
                  persistent-hint
                />
              </VCol>
            </VRow>
            <div class="d-flex flex-wrap justify-end ga-2 mt-2">
              <VBtn
                :disabled="!status.rss_configured"
                :loading="actionLoading === 'rss'"
                variant="tonal"
                @click="refreshRss"
              >
                刷新 RSS
              </VBtn>
              <VBtn
                :disabled="!canDownloadManual"
                :loading="actionLoading === 'manual-download'"
                color="primary"
                @click="downloadManual"
              >
                立即下载
              </VBtn>
            </div>
          </VCardText>
        </VCard>

        <VCard v-if="feeds.length" variant="outlined" class="mb-4">
          <VCardTitle class="text-subtitle-1">RSS 抓取状态</VCardTitle>
          <VList lines="two" bg-color="transparent">
            <VListItem v-for="feed in feeds" :key="feed.feed_id">
              <template #title>
                <span>{{ feed.source || "已配置 RSS" }}</span>
              </template>
              <template #subtitle>
                最近成功：{{ dateLabel(feed.last_success_at) }} · 本次解析
                {{ feed.parsed_count || 0 }} 条
              </template>
              <template #append>
                <VChip
                  size="small"
                  :color="feed.error_code ? 'error' : 'success'"
                  variant="tonal"
                >
                  {{ feed.error_code || `HTTP ${feed.http_status || "-"}` }}
                </VChip>
              </template>
            </VListItem>
          </VList>
        </VCard>

        <div class="d-flex flex-wrap ga-2 mb-4">
          <VTextField
            v-model="query"
            prepend-inner-icon="mdi-magnify"
            label="搜索标题"
            density="compact"
            hide-details
            class="filter-field"
          />
          <VSelect
            v-model="candidateStatus"
            :items="candidateStatuses"
            label="状态"
            density="compact"
            hide-details
            clearable
            class="filter-select"
          />
          <VSelect
            v-model="candidateType"
            :items="mediaTypes"
            label="类型"
            density="compact"
            hide-details
            clearable
            class="filter-select"
          />
          <VSelect
            v-model="candidateCategory"
            :items="categoryItems"
            label="分类"
            density="compact"
            hide-details
            clearable
            class="filter-select"
          />
          <VSelect
            v-model="candidateAge"
            :items="candidateAges"
            label="发布时间"
            density="compact"
            hide-details
            class="filter-select"
          />
        </div>

        <VAlert
          v-if="filteredCandidates.length === 0"
          type="info"
          variant="tonal"
          >暂无符合条件的候选资源。</VAlert
        >
        <VRow v-else>
          <VCol
            v-for="candidate in pagedCandidates"
            :key="candidate.candidate_id"
            cols="12"
            md="6"
            xl="4"
          >
            <VCard variant="outlined" height="100%">
              <div class="candidate-header">
                <VImg
                  v-if="candidate.poster_url"
                  :src="candidate.poster_url"
                  width="92"
                  height="124"
                  cover
                  class="candidate-poster"
                >
                  <template #placeholder>
                    <div class="candidate-poster-placeholder">
                      <VProgressCircular indeterminate color="primary" size="24" />
                    </div>
                  </template>
                </VImg>
                <div class="candidate-heading">
                  <VCardTitle class="candidate-title">{{
                    candidate.title
                  }}</VCardTitle>
                  <VCardSubtitle
                    >{{ mediaTypeLabel(candidate.media_type) }} ·
                    {{ sizeLabel(candidate.size_bytes) }}</VCardSubtitle
                  >
                </div>
              </div>
              <VCardText>
                <div class="d-flex flex-wrap ga-2 mb-2">
                  <VChip
                    size="small"
                    :color="candidateColor(candidate.status)"
                    variant="tonal"
                    >{{ statusLabel(candidate.status) }}</VChip
                  >
                  <VChip
                    v-if="candidate.category"
                    size="small"
                    variant="outlined"
                    >{{ candidate.category }}</VChip
                  >
                </div>
                <div
                  v-if="candidate.summary"
                  class="text-body-2 candidate-summary"
                >
                  {{ candidate.summary }}
                </div>
                <div class="text-caption text-medium-emphasis mt-2">
                  {{
                    dateLabel(candidate.published_at || candidate.updated_at)
                  }}
                </div>
              </VCardText>
              <VCardActions>
                <VBtn
                  v-if="candidate.detail_url"
                  :href="candidate.detail_url"
                  target="_blank"
                  rel="noopener noreferrer"
                  variant="text"
                  >详情</VBtn
                >
                <VSpacer />
                <VBtn
                  v-if="
                    !candidate.task_id &&
                    ['discovered', 'ignored'].includes(candidate.status)
                  "
                  variant="text"
                  @click="openCandidateEditor(candidate)"
                >
                  编辑
                </VBtn>
                <VBtn
                  v-if="candidate.status === 'discovered' && !candidate.task_id"
                  :loading="
                    actionLoading === `${candidate.candidate_id}:ignore`
                  "
                  color="secondary"
                  variant="text"
                  @click="ignoreCandidate(candidate, true)"
                >
                  忽略
                </VBtn>
                <VBtn
                  v-if="candidate.status === 'ignored' && !candidate.task_id"
                  :loading="
                    actionLoading === `${candidate.candidate_id}:restore`
                  "
                  color="secondary"
                  variant="text"
                  @click="ignoreCandidate(candidate, false)"
                >
                  恢复
                </VBtn>
                <VBtn
                  :disabled="
                    !['discovered', 'failed'].includes(candidate.status)
                  "
                  :loading="actionLoading === candidate.candidate_id"
                  color="primary"
                  variant="tonal"
                  @click="downloadCandidate(candidate)"
                  >下载</VBtn
                >
              </VCardActions>
            </VCard>
          </VCol>
        </VRow>
        <div
          v-if="filteredCandidates.length > candidatePageSize"
          class="d-flex justify-center mt-4"
        >
          <VPagination
            v-model="candidatePage"
            :length="candidatePageCount"
            :total-visible="7"
            density="comfortable"
          />
        </div>
      </VWindowItem>

      <VWindowItem value="tasks">
        <VCard variant="outlined" class="mb-4">
          <VCardTitle class="text-subtitle-1">手工扫描已有下载</VCardTitle>
          <VCardText>
            <VAlert type="info" variant="tonal" class="mb-3">
              路径必须相对于设置中的扫描根目录，例如
              <code>Kids/Example.Show.S01</code>；不能填写绝对路径。
            </VAlert>
            <VRow>
              <VCol cols="12" md="6">
                <VTextField
                  v-model="scanForm.relative_path"
                  label="相对路径"
                  hide-details
                />
              </VCol>
              <VCol cols="12" md="3">
                <VTextField
                  v-model="scanForm.site_item_id"
                  label="站点条目 ID（可选）"
                  hide-details
                />
              </VCol>
              <VCol cols="12" md="3">
                <VTextField
                  v-model="scanForm.download_hash"
                  label="下载 Hash（可选）"
                  hide-details
                />
              </VCol>
            </VRow>
            <div class="d-flex justify-end mt-3">
              <VBtn
                color="primary"
                variant="tonal"
                :disabled="
                  !status.enabled ||
                  !status.scan_root_configured ||
                  !scanForm.relative_path.trim()
                "
                :loading="actionLoading === 'scan'"
                @click="scanPayload"
              >
                扫描并识别
              </VBtn>
            </div>
          </VCardText>
        </VCard>
        <div class="d-flex flex-wrap ga-2 mb-4">
          <VSelect
            v-model="taskFilter"
            :items="taskFilters"
            label="任务状态"
            density="compact"
            hide-details
            class="filter-select"
          />
        </div>
        <VAlert v-if="visibleTasks.length === 0" type="info" variant="tonal"
          >暂无符合条件的下载或识别任务。</VAlert
        >
        <VExpansionPanels v-else multiple variant="accordion">
          <VExpansionPanel v-for="task in visibleTasks" :key="task.task_id">
            <VExpansionPanelTitle>
              <div class="task-title-row">
                <VChip
                  size="small"
                  :color="taskColor(task.state)"
                  variant="tonal"
                  >{{ taskStateLabel(task.state) }}</VChip
                >
                <span class="font-weight-medium">{{ taskTitle(task) }}</span>
                <span class="text-caption text-medium-emphasis">{{
                  dateLabel(task.updated_at)
                }}</span>
              </div>
            </VExpansionPanelTitle>
            <VExpansionPanelText>
              <VAlert
                v-if="task.last_error_code"
                type="error"
                variant="tonal"
                class="mb-3"
                >错误码：{{ task.last_error_code }}</VAlert
              >
              <div class="task-metadata mb-3">
                <div>
                  <span class="text-medium-emphasis">下载 Hash</span>
                  <code>{{ hashLabel(task.download_hash) }}</code>
                </div>
                <div>
                  <span class="text-medium-emphasis">关联目录</span>
                  <span>{{ task.relative_source_path || "尚未定位" }}</span>
                </div>
                <div>
                  <span class="text-medium-emphasis">文件进度</span>
                  <span
                    >{{ completedCount(task) }}/{{ expectedCount(task) }}</span
                  >
                </div>
                <div>
                  <span class="text-medium-emphasis">人工重试</span>
                  <span>{{ retryCount(task) }} 次</span>
                </div>
              </div>
              <template v-if="decisionFor(task)">
                <div class="d-flex flex-wrap ga-2 mb-3">
                  <VChip size="small"
                    >置信度
                    {{ confidenceLabel(decisionFor(task).confidence) }}</VChip
                  >
                  <VChip
                    v-if="decisionFor(task).item"
                    size="small"
                    variant="outlined"
                  >
                    {{ decisionFor(task).item.title
                    }}{{
                      decisionFor(task).item.year
                        ? ` (${decisionFor(task).item.year})`
                        : ""
                    }}
                  </VChip>
                  <VChip
                    v-if="decisionFor(task).public_match?.exact"
                    size="small"
                    color="success"
                    variant="tonal"
                  >
                    TMDb 精确命中 #{{ decisionFor(task).public_match.media_id }}
                  </VChip>
                </div>
                <VAlert
                  v-for="conflict in decisionFor(task).conflicts || []"
                  :key="`${task.task_id}-${conflict.code}`"
                  :type="conflict.severity === 'hard' ? 'warning' : 'info'"
                  variant="tonal"
                  class="mb-2"
                >
                  <div class="font-weight-medium">{{ conflict.message }}</div>
                  <div
                    v-if="conflict.evidence?.length"
                    class="text-caption mt-1"
                  >
                    证据：{{ conflict.evidence.join("、") }}
                  </div>
                </VAlert>
                <div
                  v-if="decisionFor(task).transfer_preview"
                  class="text-body-2 mt-3"
                >
                  整理预览：{{
                    decisionFor(task).transfer_preview.library_section
                  }}/{{ decisionFor(task).transfer_preview.media_directory }}
                </div>
                <div
                  v-if="sourceEntries(decisionFor(task)).length"
                  class="mt-3"
                >
                  <div class="text-subtitle-2 mb-2">字段来源</div>
                  <div class="d-flex flex-wrap ga-2">
                    <VChip
                      v-for="entry in sourceEntries(decisionFor(task))"
                      :key="`${task.task_id}-${entry[0]}`"
                      size="small"
                      variant="outlined"
                    >
                      {{ fieldLabel(entry[0]) }} ← {{ entry[1] }}
                    </VChip>
                  </div>
                </div>
              </template>
              <details v-if="task.history?.length" class="task-history mt-3">
                <summary>状态历史（{{ task.history.length }}）</summary>
                <div
                  v-for="(entry, index) in task.history"
                  :key="`${task.task_id}-history-${index}`"
                  class="task-history-entry"
                >
                  <span
                    >{{ taskStateLabel(entry.from) }} →
                    {{ taskStateLabel(entry.to) }}</span
                  >
                  <span class="text-medium-emphasis">{{
                    entry.reason || "无备注"
                  }}</span>
                  <span class="text-caption text-medium-emphasis">{{
                    dateLabel(entry.time)
                  }}</span>
                </div>
              </details>
              <div class="d-flex flex-wrap justify-end ga-2 mt-4">
                <template v-if="task.state === 'NEEDS_REVIEW'">
                  <VBtn
                    :loading="actionLoading === `${task.task_id}:ignore`"
                    variant="outlined"
                    color="warning"
                    @click="reviewTask(task, 'ignore')"
                    >忽略任务</VBtn
                  >
                  <VBtn
                    :loading="actionLoading === `${task.task_id}:approve`"
                    color="primary"
                    @click="reviewTask(task, 'approve')"
                    >批准识别</VBtn
                  >
                </template>
                <VBtn
                  v-if="
                    ['READY_TO_TRANSFER', 'RETRYABLE_FAILED'].includes(
                      task.state,
                    )
                  "
                  :loading="actionLoading === `${task.task_id}:retry`"
                  color="primary"
                  @click="retryTask(task)"
                  >{{
                    task.state === "READY_TO_TRANSFER"
                      ? "确认并执行整理"
                      : "重试任务"
                  }}</VBtn
                >
              </div>
            </VExpansionPanelText>
          </VExpansionPanel>
        </VExpansionPanels>
      </VWindowItem>

      <VWindowItem value="drafts">
        <VAlert type="info" variant="tonal" class="mb-4">
          以下内容仅用于人工核对和向 TMDb 贡献，不会自动写入或提交第三方服务。
        </VAlert>
        <VAlert v-if="drafts.length === 0" type="info" variant="tonal"
          >暂无可生成的贡献草稿。</VAlert
        >
        <VRow v-else>
          <VCol v-for="draft in drafts" :key="draft.draft_id" cols="12" md="6">
            <VCard variant="outlined" height="100%">
              <VCardTitle>{{
                draft.suggested_fields?.title || "未命名草稿"
              }}</VCardTitle>
              <VCardSubtitle>
                {{
                  draft.mode === "update_existing"
                    ? `更新 TMDb #${draft.target?.media_id}`
                    : "创建或查找条目"
                }}
              </VCardSubtitle>
              <VCardText>
                <div
                  v-for="(value, key) in draft.suggested_fields"
                  :key="key"
                  class="draft-field"
                >
                  <span class="text-medium-emphasis">{{
                    fieldLabel(key)
                  }}</span>
                  <span>{{ valueLabel(value) }}</span>
                </div>
                <div class="text-caption text-medium-emphasis mt-3">
                  NFO：{{ draft.evidence?.nfo_files?.join("、") || "无" }}
                </div>
              </VCardText>
            </VCard>
          </VCol>
        </VRow>
      </VWindowItem>
    </VWindow>

    <VDialog v-model="editDialog" max-width="560">
      <VCard>
        <VCardTitle>修正候选身份</VCardTitle>
        <VCardText>
          <VAlert type="info" variant="tonal" class="mb-4">
            只修改下载前使用的标题和媒体类型；候选稳定
            ID、来源和私密引用不会改变。
          </VAlert>
          <VTextField
            v-model="editForm.title"
            label="候选标题"
            maxlength="500"
            counter
          />
          <VSelect
            v-model="editForm.media_type"
            :items="mediaTypes"
            label="媒体类型"
          />
        </VCardText>
        <VCardActions>
          <VSpacer />
          <VBtn variant="text" @click="editDialog = false">取消</VBtn>
          <VBtn
            color="primary"
            :disabled="!editForm.title.trim()"
            :loading="actionLoading === 'candidate-update'"
            @click="saveCandidateIdentity"
          >
            保存
          </VBtn>
        </VCardActions>
      </VCard>
    </VDialog>
  </div>
</template>

<script setup>
import { computed, inject, onMounted, reactive, ref, watch } from "vue";
import { createPluginApi, messageOf } from "./api";
import { filterCandidates, filterTasks, maskHash } from "./workflow";

const props = defineProps({
  api: { type: Object, required: true },
  pluginId: { type: String, default: "PigGoKidsMetadata" },
  compact: { type: Boolean, default: false },
});
const emit = defineEmits(["action", "switch"]);
const toast = inject("moviepilot:toast", null);

const client = createPluginApi(props.api, props.pluginId);
const tab = ref("candidates");
const loading = ref(false);
const actionLoading = ref("");
const error = ref("");
const status = ref({});
const candidates = ref([]);
const tasks = ref([]);
const decisions = ref({});
const drafts = ref([]);
const feeds = ref([]);
const query = ref("");
const candidateStatus = ref(null);
const candidateType = ref(null);
const candidateCategory = ref(null);
const candidateAge = ref("all");
const candidatePage = ref(1);
const candidatePageSize = 24;
const taskFilter = ref("all");
const showDownloadReference = ref(false);
const editDialog = ref(false);
const editingCandidate = ref(null);
const importForm = reactive({
  title: "",
  media_type: "unknown",
  download_reference: "",
  poster_url: "",
});
const scanForm = reactive({
  relative_path: "",
  site_item_id: "",
  download_hash: "",
});
const editForm = reactive({
  title: "",
  media_type: "unknown",
});

const mediaTypes = [
  { title: "自动判断", value: "unknown" },
  { title: "电影", value: "movie" },
  { title: "剧集", value: "tv" },
  { title: "合集", value: "collection" },
];
const candidateStatuses = [
  { title: "待选择", value: "discovered" },
  { title: "已选择", value: "selected" },
  { title: "下载中", value: "downloading" },
  { title: "已完成", value: "completed" },
  { title: "失败", value: "failed" },
  { title: "已忽略", value: "ignored" },
];
const candidateAges = [
  { title: "不限时间", value: "all" },
  { title: "最近 24 小时", value: "1" },
  { title: "最近 7 天", value: "7" },
  { title: "最近 30 天", value: "30" },
];
const taskFilters = [
  { title: "全部任务", value: "all" },
  { title: "下载中", value: "downloading" },
  { title: "待整理", value: "ready" },
  { title: "待人工审核", value: "review" },
  { title: "失败", value: "failed" },
  { title: "已完成", value: "completed" },
  { title: "已忽略", value: "ignored" },
];

const canDownloadManual = computed(
  () => status.value.enabled && importForm.download_reference.trim().length > 0,
);
const metrics = computed(() => [
  { label: "候选资源", value: status.value.candidate_count || 0 },
  { label: "跟踪任务", value: status.value.task_count || 0 },
  {
    label: "待审核",
    value: tasks.value.filter((item) => item.state === "NEEDS_REVIEW").length,
  },
  { label: "贡献草稿", value: status.value.contribution_draft_count || 0 },
]);
const categoryItems = computed(() =>
  [...new Set(candidates.value.map((item) => item.category).filter(Boolean))]
    .sort((left, right) => left.localeCompare(right, "zh-CN"))
    .map((value) => ({ title: value, value })),
);
const filteredCandidates = computed(() => {
  return filterCandidates(candidates.value, {
    query: query.value,
    status: candidateStatus.value,
    mediaType: candidateType.value,
    category: candidateCategory.value,
    ageDays: candidateAge.value,
  });
});
const candidatePageCount = computed(() =>
  Math.max(1, Math.ceil(filteredCandidates.value.length / candidatePageSize)),
);
const pagedCandidates = computed(() => {
  const offset = (candidatePage.value - 1) * candidatePageSize;
  return filteredCandidates.value.slice(offset, offset + candidatePageSize);
});
watch(
  [query, candidateStatus, candidateType, candidateCategory, candidateAge],
  () => {
    candidatePage.value = 1;
  },
);
watch(candidatePageCount, (count) => {
  if (candidatePage.value > count) candidatePage.value = count;
});
const visibleTasks = computed(() => filterTasks(tasks.value, taskFilter.value));

function notify(message, color = "success") {
  if (typeof toast?.[color] === "function") toast[color](message);
}

async function loadAll() {
  loading.value = true;
  error.value = "";
  try {
    const [statusData, candidateData, taskData, draftData, feedData] =
      await Promise.all([
        client.get("/status"),
        client.get("/candidates?limit=1000"),
        client.get("/tasks"),
        client.get("/contribution-drafts"),
        client.get("/feeds"),
      ]);
    status.value = statusData;
    candidates.value = candidateData.items || [];
    tasks.value = taskData.items || [];
    decisions.value = taskData.decisions || {};
    drafts.value = draftData.items || [];
    feeds.value = feedData.items || [];
  } catch (requestError) {
    error.value = messageOf(requestError);
  } finally {
    loading.value = false;
  }
}

async function runAction(key, action, successMessage) {
  actionLoading.value = key;
  error.value = "";
  try {
    await action();
    notify(successMessage);
    await loadAll();
    emit("action");
    return true;
  } catch (requestError) {
    error.value = messageOf(requestError);
    return false;
  } finally {
    actionLoading.value = "";
  }
}

async function refreshRss() {
  if (!window.confirm("确认现在访问站点并刷新一次 RSS？插件不会自动刷新。")) return;
  await runAction(
    "rss",
    () => client.post("/candidates/refresh"),
    "RSS 候选已刷新",
  );
}

async function downloadManual() {
  await runAction(
    "manual-download",
    async () => {
      await client.post("/downloads/manual", {
        ...importForm,
      });
      importForm.download_reference = "";
      importForm.title = "";
      importForm.poster_url = "";
    },
    "已提交到 MoviePilot 下载器",
  );
}

async function downloadCandidate(candidate) {
  if (!window.confirm(`确认下载“${candidate.title}”？`)) return;
  await runAction(
    candidate.candidate_id,
    () =>
      client.post("/candidates/download", {
        candidate_id: candidate.candidate_id,
        media_type: candidate.media_type,
      }),
    "已提交到 MoviePilot 下载器",
  );
}

async function ignoreCandidate(candidate, ignored) {
  const verb = ignored ? "忽略" : "恢复";
  if (!window.confirm(`确认${verb}“${candidate.title}”？`)) return;
  await runAction(
    `${candidate.candidate_id}:${ignored ? "ignore" : "restore"}`,
    () =>
      client.post("/candidates/ignore", {
        candidate_id: candidate.candidate_id,
        ignored,
      }),
    `候选已${verb}`,
  );
}

function openCandidateEditor(candidate) {
  editingCandidate.value = candidate;
  editForm.title = candidate.title || "";
  editForm.media_type = candidate.media_type || "unknown";
  editDialog.value = true;
}

async function saveCandidateIdentity() {
  const candidate = editingCandidate.value;
  if (!candidate || !editForm.title.trim()) return;
  const saved = await runAction(
    "candidate-update",
    () =>
      client.post("/candidates/update", {
        candidate_id: candidate.candidate_id,
        title: editForm.title,
        media_type: editForm.media_type,
      }),
    "候选身份已更新",
  );
  if (!saved) return;
  editDialog.value = false;
  editingCandidate.value = null;
}

async function scanPayload() {
  await runAction(
    "scan",
    async () => {
      await client.post("/scan", { ...scanForm });
      scanForm.relative_path = "";
      scanForm.site_item_id = "";
      scanForm.download_hash = "";
      tab.value = "tasks";
    },
    "内容包扫描完成",
  );
}

async function retryTask(task) {
  const prompt =
    task.state === "READY_TO_TRANSFER"
      ? `确认按识别预览整理“${taskTitle(task)}”？`
      : `确认重试任务“${taskTitle(task)}”？`;
  if (!window.confirm(prompt)) return;
  await runAction(
    `${task.task_id}:retry`,
    () => client.post("/tasks/retry", { task_id: task.task_id }),
    "任务操作已提交",
  );
}

async function reviewTask(task, action) {
  const approved = action === "approve";
  const prompt = approved
    ? "批准后任务会进入待整理状态，但不会立即移动文件。确认批准？"
    : "忽略后该任务将终止，识别证据仍保留供审计。确认忽略？";
  if (!window.confirm(prompt)) return;
  await runAction(
    `${task.task_id}:${action}`,
    () =>
      client.post("/tasks/review", {
        task_id: task.task_id,
        action,
      }),
    approved ? "识别已批准，请再次确认后执行整理" : "任务已忽略",
  );
}

function decisionFor(task) {
  return decisions.value[task.task_id];
}
function sourceEntries(decision) {
  return Object.entries(decision?.item?.source_fields || {});
}
function completedCount(task) {
  return task.transfer_completed_files?.length || 0;
}
function expectedCount(task) {
  return task.transfer_expected_files?.length || 0;
}
function retryCount(task) {
  return (task.history || []).filter((entry) =>
    String(entry.reason || "").includes("user_retry"),
  ).length;
}
function taskTitle(task) {
  return (
    decisionFor(task)?.item?.title ||
    task.relative_source_path ||
    task.candidate_id ||
    task.task_id
  );
}
function mediaTypeLabel(value) {
  return (
    { movie: "电影", tv: "剧集", collection: "合集", unknown: "待判断" }[
      value
    ] || value
  );
}
function statusLabel(value) {
  return (
    {
      discovered: "待选择",
      selected: "已选择",
      downloading: "下载中",
      completed: "已完成",
      failed: "失败",
      ignored: "已忽略",
    }[value] || value
  );
}
function candidateColor(value) {
  return (
    {
      discovered: "info",
      selected: "primary",
      downloading: "warning",
      completed: "success",
      failed: "error",
      ignored: "secondary",
    }[value] || "default"
  );
}
function taskStateLabel(value) {
  return (
    {
      DISCOVERED: "已发现",
      SELECTED: "已选择",
      DOWNLOAD_SUBMITTED: "已提交",
      DOWNLOADING: "下载中",
      DOWNLOADED: "已下载",
      SCANNING: "扫描中",
      MATCHING: "匹配中",
      READY_TO_TRANSFER: "待整理",
      TRANSFERRING: "整理中",
      LIBRARY_REFRESHING: "刷新媒体库",
      COMPLETED: "已完成",
      RETRYABLE_FAILED: "可重试失败",
      NEEDS_REVIEW: "待人工审核",
      IGNORED: "已忽略",
    }[value] || value
  );
}
function taskColor(value) {
  return (
    {
      COMPLETED: "success",
      NEEDS_REVIEW: "warning",
      RETRYABLE_FAILED: "error",
      READY_TO_TRANSFER: "primary",
      IGNORED: "secondary",
      DOWNLOADING: "info",
    }[value] || "default"
  );
}
function confidenceLabel(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}
function hashLabel(value) {
  return maskHash(value);
}
function sizeLabel(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes <= 0) return "大小未知";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const level = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  return `${(bytes / 1024 ** level).toFixed(level > 1 ? 1 : 0)} ${units[level]}`;
}
function dateLabel(value) {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString("zh-CN");
}
function fieldLabel(key) {
  return (
    {
      title: "标题",
      original_title: "原名",
      year: "年份",
      season: "季",
      episode_count: "集数",
      overview: "简介",
      aliases: "别名",
      genres: "类型",
    }[key] || key
  );
}
function valueLabel(value) {
  return Array.isArray(value) ? value.join("、") : String(value ?? "");
}

onMounted(loadAll);
</script>

<style scoped>
.piggo-workbench {
  width: 100%;
}
.metric-card {
  height: 100%;
}
.filter-field {
  min-width: 220px;
  flex: 1 1 320px;
}
.filter-select {
  min-width: 150px;
  flex: 0 1 180px;
}
.candidate-title {
  white-space: normal;
  line-height: 1.4;
}
.candidate-header {
  display: flex;
  align-items: flex-start;
  min-width: 0;
}
.candidate-heading {
  flex: 1 1 auto;
  min-width: 0;
}
.candidate-poster {
  flex: 0 0 92px;
  margin: 12px 0 0 12px;
  border: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
  border-radius: 8px;
  background: rgb(var(--v-theme-surface-variant));
}
.candidate-poster-placeholder {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}
.candidate-summary {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.secret-textarea :deep(textarea) {
  -webkit-text-security: disc;
}
.task-title-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.draft-field {
  display: grid;
  grid-template-columns: minmax(72px, 110px) 1fr;
  gap: 12px;
  margin-bottom: 8px;
}
.task-metadata {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}
.task-metadata > div {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.task-history summary {
  cursor: pointer;
  font-weight: 500;
}
.task-history-entry {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) minmax(120px, 1fr) auto;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid rgba(var(--v-border-color), var(--v-border-opacity));
}
@media (max-width: 600px) {
  .filter-select {
    flex: 1 1 140px;
  }
  .task-title-row {
    gap: 6px;
  }
  .task-metadata {
    grid-template-columns: 1fr;
  }
  .task-history-entry {
    grid-template-columns: 1fr;
    gap: 2px;
  }
}
</style>
