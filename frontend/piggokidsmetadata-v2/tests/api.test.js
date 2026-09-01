import assert from "node:assert/strict";
import test from "node:test";

import { createPluginApi, messageOf } from "../src/api.js";
import {
  filterCandidates,
  filterTasks,
  maskHash,
  posterFallbackTitle,
} from "../src/workflow.js";

test("unwraps MoviePilot response envelopes", async () => {
  const calls = [];
  const api = {
    async get(path) {
      calls.push(["get", path]);
      return { success: true, message: "", data: { enabled: true } };
    },
    async post(path, payload) {
      calls.push(["post", path, payload]);
      return { data: { success: true, message: "", data: { task_id: "t1" } } };
    },
  };
  const client = createPluginApi(api, "PigGoKidsMetadata");

  assert.deepEqual(await client.get("/status"), { enabled: true });
  assert.deepEqual(await client.post("/tasks/retry", { task_id: "t1" }), {
    task_id: "t1",
  });
  assert.deepEqual(calls, [
    ["get", "plugin/PigGoKidsMetadata/status"],
    ["post", "plugin/PigGoKidsMetadata/tasks/retry", { task_id: "t1" }],
  ]);
});

test("accepts direct payloads and surfaces backend messages", async () => {
  const direct = createPluginApi(
    {
      get: async () => ({ items: [1, 2] }),
      post: async () => ({ success: false, message: "任务不存在", data: {} }),
    },
    "PigGoKidsMetadata",
  );

  assert.deepEqual(await direct.get("/tasks"), { items: [1, 2] });
  await assert.rejects(() => direct.post("/tasks/retry", {}), /任务不存在/);
  assert.equal(
    messageOf({ response: { data: { message: "请求错误" } } }),
    "请求错误",
  );
});

test("filters candidates and tasks without exposing full hashes", () => {
  const now = Date.parse("2026-08-31T12:00:00Z");
  const candidates = [
    {
      title: "儿童电影",
      status: "discovered",
      media_type: "movie",
      category: "儿童",
      published_at: "2026-08-31T10:00:00Z",
    },
    {
      title: "儿童剧集",
      status: "completed",
      media_type: "tv",
      category: "动画",
      published_at: "2026-07-01T10:00:00Z",
    },
  ];
  assert.deepEqual(
    filterCandidates(
      candidates,
      {
        query: "电影",
        status: "discovered",
        mediaType: "movie",
        category: "儿童",
        ageDays: "1",
      },
      now,
    ),
    [candidates[0]],
  );
  assert.deepEqual(
    filterTasks(
      [{ state: "DOWNLOADING" }, { state: "NEEDS_REVIEW" }],
      "review",
    ),
    [{ state: "NEEDS_REVIEW" }],
  );
  assert.equal(
    maskHash("0123456789abcdef0123456789abcdef01234567"),
    "01234567…4567",
  );
  assert.equal(
    posterFallbackTitle("海底小纵队 S11 Complete 2026 2160P WEB-DL"),
    "海底小纵队",
  );
  assert.equal(posterFallbackTitle(""), "候选资源");
});
