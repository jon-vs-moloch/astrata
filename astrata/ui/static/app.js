const state = {
  summary: null,
  selectedTaskId: null,
  activeTranscriptLane: "prime",
};

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return response.json();
}

function badgeClass(label) {
  const lowered = String(label).toLowerCase();
  if (["complete", "succeeded", "delivered", "pass", "good", "nominal"].includes(lowered)) return "good";
  if (["failed", "broken", "critical", "severe"].includes(lowered)) return "bad";
  return "warn";
}

function renderBadges(container, entries) {
  container.innerHTML = "";
  entries.forEach(([label, value]) => {
    const badge = document.createElement("span");
    badge.className = `badge ${badgeClass(label)}`;
    badge.textContent = `${label}: ${value}`;
    container.appendChild(badge);
  });
}

function renderList(container, items, renderer) {
  container.innerHTML = "";
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "list-item";
    empty.textContent = "Nothing here yet.";
    container.appendChild(empty);
    return;
  }
  items.forEach((item) => container.appendChild(renderer(item)));
}

function humanLane(label) {
  const value = String(label || "").trim().toLowerCase();
  if (!value) return "unknown";
  if (value === "astrata") return "System";
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function inferTaskLane(task) {
  const preferred = task?.completion_policy?.preferred_lane || task?.completion_policy?.route_preferences?.preferred_lane;
  if (preferred) return humanLane(preferred);
  const provenanceSource = String(task?.provenance?.source || "").toLowerCase();
  if (provenanceSource.includes("startup") || provenanceSource.includes("loop0") || provenanceSource.includes("message_intake")) {
    return "System";
  }
  return "Unassigned";
}

function listItem(title, body, meta = [], actions = []) {
  const node = document.createElement("article");
  node.className = "list-item";
  const heading = document.createElement("strong");
  heading.textContent = title;
  node.appendChild(heading);
  if (body) {
    const bodyNode = document.createElement("p");
    bodyNode.textContent = body;
    node.appendChild(bodyNode);
  }
  if (meta.length) {
    const metaRow = document.createElement("div");
    metaRow.className = "list-meta";
    meta.forEach((entry) => {
      const span = document.createElement("span");
      span.textContent = entry;
      metaRow.appendChild(span);
    });
    node.appendChild(metaRow);
  }
  if (actions.length) {
    const row = document.createElement("div");
    row.className = "panel-actions";
    actions.forEach((action) => row.appendChild(action));
    node.appendChild(row);
  }
  return node;
}

function renderSummary(summary) {
  state.summary = summary;
  document.getElementById("defaultRouteValue").textContent =
    summary.providers.default_route
      ? `${summary.providers.default_route.provider}${summary.providers.default_route.cli_tool ? `:${summary.providers.default_route.cli_tool}` : ""}`
      : "Unavailable";
  document.getElementById("thermalStateValue").textContent =
    summary.local_runtime.thermal_state.thermal_pressure || "unknown";
  document.getElementById("queuePressureValue").textContent =
    `${summary.queue.counts.pending || 0} pending`;

  const runtimeSummary = document.getElementById("runtimeSummary");
  runtimeSummary.innerHTML = "";
  const tiles = [
    ["Decision", summary.local_runtime.thermal_decision.action],
    ["Recommended", summary.local_runtime.recommendation.model?.display_name || "No recommendation"],
    ["Process", summary.local_runtime.managed_process?.running ? "Running" : "Stopped"],
  ];
  tiles.forEach(([label, value]) => {
    const tile = document.createElement("div");
    tile.className = "summary-tile";
    tile.innerHTML = `<strong>${label}</strong><span>${value}</span>`;
    runtimeSummary.appendChild(tile);
  });

  renderList(
    document.getElementById("modelList"),
    summary.local_runtime.models || [],
    (model) =>
      listItem(
        model.display_name,
        model.path,
        [model.family || "unknown", model.role || "model"]
      )
  );

  renderBadges(document.getElementById("queueCounts"), Object.entries(summary.queue.counts || {}));

  const startupEntries = [];
  if (summary.startup?.preflight) {
    startupEntries.push(["Preflight", summary.startup.preflight.ok ? "pass" : "fail"]);
  }
  if (summary.startup?.runtime) {
    startupEntries.push(["Reflection", summary.startup.runtime.ok ? "pass" : "degraded"]);
  }
  renderBadges(document.getElementById("startupCounts"), startupEntries);
  renderList(
    document.getElementById("startupList"),
    [
      summary.startup?.preflight
        ? {
            title: "Pre-inference preflight",
            body:
              summary.startup.preflight.issues?.length
                ? summary.startup.preflight.issues.map((issue) => issue.kind).join(", ")
                : "Managed runtime and core imports look sane.",
            meta: [
              summary.startup.preflight.ok ? "ok" : "needs repair",
              summary.startup.preflight.selected_python || "no python selected",
            ],
          }
        : null,
      summary.startup?.runtime
        ? {
            title: "Post-boot self-reflection",
            body: summary.startup.runtime.summary || "No runtime reflection yet.",
            meta: [
              summary.startup.runtime.ok ? "ok" : "issues detected",
              `${summary.startup.runtime.issues?.length || 0} issues`,
            ],
          }
        : null,
    ].filter(Boolean),
    (entry) => listItem(entry.title, entry.body, entry.meta)
  );

  renderList(
    document.getElementById("taskList"),
    summary.queue.recent_tasks || [],
    (task) => {
      const actions = [];
      const button = document.createElement("button");
      button.className = "button button-ghost";
      button.textContent = "Inspect";
      button.onclick = () => selectTask(task.task_id);
      actions.push(button);
      return listItem(
        task.title,
        task.description,
        [
          task.status,
          `p${task.priority}`,
          `u${task.urgency}`,
          task.risk,
          inferTaskLane(task),
        ],
        actions
      );
    }
  );

  renderBadges(document.getElementById("attemptCounts"), Object.entries(summary.attempts.counts || {}));
  renderList(
    document.getElementById("attemptList"),
    summary.attempts.recent_attempts || [],
    (attempt) =>
      listItem(
        `${attempt.actor} · ${attempt.outcome}`,
        attempt.result_summary || attempt.failure_kind || "No summary yet.",
        [attempt.verification_status, attempt.degraded_reason || "clean", attempt.started_at]
      )
  );

  renderList(
    document.getElementById("operatorInbox"),
    summary.communications.operator_inbox || [],
    (message) => {
      const actions = [];
      if (message.status !== "acknowledged") {
        const button = document.createElement("button");
        button.className = "button button-ghost";
        button.textContent = "Acknowledge";
        button.onclick = async () => {
          await api(`/api/messages/${message.communication_id}/ack`, { method: "POST" });
          await refresh();
        };
        actions.push(button);
      }
      return listItem(
        message.intent || message.kind,
        message.message || "(no message body)",
        [message.sender, message.status, message.created_at],
        actions
      );
    }
  );

  renderBadges(document.getElementById("laneCounts"), Object.entries(summary.communications.lane_counts || {}));
  renderList(
    document.getElementById("primeInbox"),
    summary.communications.prime_inbox || [],
    (message) =>
      listItem(
        message.intent || message.kind,
        message.message || "(no message body)",
        [message.sender, message.status, message.created_at]
      )
  );
  renderList(
    document.getElementById("localInbox"),
    summary.communications.local_inbox || [],
    (message) =>
      listItem(
        message.intent || message.kind,
        message.message || "(no message body)",
        [message.sender, message.status, message.created_at]
      )
  );

  const conversationKey = state.activeTranscriptLane === "local" ? "local_conversation" : "prime_conversation";
  renderList(
    document.getElementById("laneTranscript"),
    summary.communications[conversationKey] || [],
    (message) =>
      listItem(
        `${humanLane(message.sender)} -> ${humanLane(message.recipient)}`,
        message.message || "(no message body)",
        [message.intent || message.kind, message.status, message.created_at]
      )
  );

  renderList(
    document.getElementById("astrataInbox"),
    summary.communications.astrata_inbox || [],
    (message) =>
      listItem(
        message.intent || message.kind,
        message.message || "(no message body)",
        [message.sender, message.status, message.created_at]
      )
  );

  renderList(
    document.getElementById("artifactList"),
    summary.artifacts.recent || [],
    (artifact) =>
      listItem(
        `${artifact.artifact_type} · ${artifact.title}`,
        artifact.content_summary || "No summary yet.",
        [artifact.status, artifact.lifecycle_state, artifact.updated_at]
      )
  );

  if (state.selectedTaskId) {
    void selectTask(state.selectedTaskId, { quiet: true });
  }
}

function renderTaskDetail(detail) {
  const empty = document.getElementById("taskDetailEmpty");
  const shell = document.getElementById("taskDetail");
  const summaryNode = document.getElementById("taskDetailSummary");
  if (!detail || detail.status !== "ok") {
    empty.hidden = false;
    shell.hidden = true;
    return;
  }
  empty.hidden = true;
  shell.hidden = false;
  const task = detail.task;
  summaryNode.innerHTML = "";
  const cards = [
    ["Task", task.title],
    ["State", task.status],
    ["Risk", task.risk],
    ["Priority", `p${task.priority} / u${task.urgency}`],
    ["Lane", inferTaskLane(task)],
  ];
  cards.forEach(([label, value]) => {
    const tile = document.createElement("div");
    tile.className = "summary-tile";
    tile.innerHTML = `<strong>${label}</strong><span>${value}</span>`;
    summaryNode.appendChild(tile);
  });

  renderList(
    document.getElementById("taskDetailBlockers"),
    detail.blockers || [],
    (blocker) =>
      listItem(
        blocker.kind,
        blocker.summary || "",
        Array.isArray(blocker.tasks) ? blocker.tasks.map((task) => `${task.title} (${task.status})`) : []
      )
  );
  renderList(
    document.getElementById("taskDetailAttempts"),
    detail.attempts || [],
    (attempt) =>
      listItem(
        `${attempt.actor} · ${attempt.outcome}`,
        attempt.result_summary || attempt.failure_kind || "No summary yet.",
        [attempt.verification_status, attempt.degraded_reason || "clean", attempt.started_at]
      )
  );
  renderList(
    document.getElementById("taskDetailArtifacts"),
    detail.artifacts || [],
    (artifact) =>
      listItem(
        `${artifact.artifact_type} · ${artifact.title}`,
        artifact.content_summary || "No summary yet.",
        [artifact.status, artifact.lifecycle_state, artifact.updated_at]
      )
  );
  renderList(
    document.getElementById("taskDetailMessages"),
    detail.messages || [],
    (message) =>
      listItem(
        `${humanLane(message.recipient)} · ${message.intent || message.kind}`,
        message.message || "(no message body)",
        [message.sender, message.status, message.created_at]
      )
  );
  renderList(
    document.getElementById("taskDetailVerifications"),
    detail.verifications || [],
    (verification) =>
      listItem(
        `${verification.verifier} · ${verification.result}`,
        "",
        [`confidence ${verification.confidence}`, verification.created_at]
      )
  );
  renderList(
    document.getElementById("taskDetailChildren"),
    detail.relationships?.children || [],
    (task) =>
      listItem(
        task.title,
        task.description,
        [task.status, `p${task.priority}`, `u${task.urgency}`, inferTaskLane(task)]
      )
  );
  renderList(
    document.getElementById("taskDetailRelated"),
    [
      ...(detail.relationships?.parent ? [{ ...detail.relationships.parent, relation_label: "Parent" }] : []),
      ...((detail.relationships?.siblings || []).map((task) => ({ ...task, relation_label: "Sibling" }))),
      ...((detail.relationships?.same_source || []).map((task) => ({ ...task, relation_label: "Same source" }))),
    ],
    (task) =>
      listItem(
        `${task.relation_label} · ${task.title}`,
        task.description,
        [task.status, `p${task.priority}`, `u${task.urgency}`, inferTaskLane(task)]
      )
  );
}

async function selectTask(taskId, { quiet = false } = {}) {
  state.selectedTaskId = taskId;
  try {
    const detail = await api(`/api/tasks/${taskId}`);
    renderTaskDetail(detail);
  } catch (error) {
    if (!quiet) {
      console.error(error);
    }
    renderTaskDetail(null);
  }
}

async function refresh() {
  const summary = await api("/api/summary");
  renderSummary(summary);
}

async function postAndRefresh(url, options = {}) {
  await api(url, options);
  await refresh();
}

window.addEventListener("DOMContentLoaded", async () => {
  document.getElementById("refreshButton").onclick = refresh;
  document.getElementById("runLoopButton").onclick = () => postAndRefresh("/api/loop0/run?steps=1", { method: "POST" });
  document.getElementById("startRuntimeButton").onclick = () => postAndRefresh("/api/local-runtime/start", { method: "POST" });
  document.getElementById("stopRuntimeButton").onclick = () => postAndRefresh("/api/local-runtime/stop", { method: "POST" });
  document.getElementById("messageForm").onsubmit = async (event) => {
    event.preventDefault();
    const message = document.getElementById("messageInput").value.trim();
    if (!message) return;
    const recipient = document.getElementById("recipientSelect").value;
    await postAndRefresh("/api/messages", {
      method: "POST",
      body: JSON.stringify({ message, recipient }),
    });
    document.getElementById("messageInput").value = "";
  };
  document.getElementById("showPrimeTranscriptButton").onclick = async () => {
    state.activeTranscriptLane = "prime";
    await refresh();
  };
  document.getElementById("showLocalTranscriptButton").onclick = async () => {
    state.activeTranscriptLane = "local";
    await refresh();
  };
  await refresh();
  window.setInterval(refresh, 15000);
});
