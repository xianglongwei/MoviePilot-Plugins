const TASK_GROUPS = {
  downloading: ["SELECTED", "DOWNLOAD_SUBMITTED", "DOWNLOADING", "DOWNLOADED"],
  ready: [
    "SCANNING",
    "MATCHING",
    "READY_TO_TRANSFER",
    "TRANSFERRING",
    "LIBRARY_REFRESHING",
  ],
  review: ["NEEDS_REVIEW"],
  failed: ["RETRYABLE_FAILED"],
  completed: ["COMPLETED"],
  ignored: ["IGNORED"],
};

export function filterCandidates(
  items,
  {
    query = "",
    status = null,
    mediaType = null,
    category = null,
    ageDays = null,
  },
  now = Date.now(),
) {
  const needle = String(query).trim().toLocaleLowerCase();
  const days = Number(ageDays);
  const cutoff =
    Number.isFinite(days) && days > 0 ? now - days * 24 * 60 * 60 * 1000 : null;
  return items.filter((item) => {
    if (
      needle &&
      !String(item.title || "")
        .toLocaleLowerCase()
        .includes(needle)
    )
      return false;
    if (status && item.status !== status) return false;
    if (mediaType && item.media_type !== mediaType) return false;
    if (category && item.category !== category) return false;
    if (cutoff) {
      const published = new Date(
        item.published_at || item.created_at,
      ).getTime();
      if (!Number.isFinite(published) || published < cutoff) return false;
    }
    return true;
  });
}

export function filterTasks(items, filter = "all") {
  if (filter === "all") return items;
  const states = TASK_GROUPS[filter] || [];
  return items.filter((task) => states.includes(task.state));
}

export function maskHash(value) {
  const text = String(value || "");
  if (!text) return "尚未记录";
  if (text.length <= 12) return text;
  return `${text.slice(0, 8)}…${text.slice(-4)}`;
}

export function posterFallbackTitle(value) {
  const title = String(value || "")
    .replace(/\s+/g, " ")
    .trim();
  if (!title) return "候选资源";
  const concise = title.split(
    /\s+(?=S\d{1,2}\b|(?:19|20)\d{2}\b|Complete\b|2160[Pp]\b|1080[Pp]\b)/,
    1,
  )[0];
  return (concise || title).slice(0, 24);
}
