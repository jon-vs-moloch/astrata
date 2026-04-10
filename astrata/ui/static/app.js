/* ═══════════════════════════════════════════════════════
   Astrata UI — app.js  v2
   – Real Markdown via marked.js
   – Session management (localStorage)
   – Message grouping
   – Generating / pending-response indicator
   – Recursive task tree
   – Sidebar telemetry footer
   – Composer mode toggle (Agent / Ephemeral)
   – Editable Settings / registry
   ═══════════════════════════════════════════════════════ */

/* ─── SESSION STORAGE HELPERS ───────────────────────── */

function loadSessions(lane) {
  try { return JSON.parse(localStorage.getItem(`astrata_sessions_${lane}`) || '[]'); }
  catch { return []; }
}
function saveSessions(lane, sessions) {
  localStorage.setItem(`astrata_sessions_${lane}`, JSON.stringify(sessions));
}
function getActiveSessionId(lane) {
  return localStorage.getItem(`astrata_active_session_${lane}`) || 'default';
}
function persistActiveSession(lane, id) {
  localStorage.setItem(`astrata_active_session_${lane}`, id);
}

function createNewSession(lane) {
  const id = 'sess-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
  const now = new Date().toISOString();
  const name = new Date().toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  const session = { id, name, created_at: now, preview: '' };
  const sessions = loadSessions(lane);
  sessions.unshift(session);
  // Keep up to 10 sessions
  saveSessions(lane, sessions.slice(0, 10));
  persistActiveSession(lane, id);
  APP.sessions[lane] = sessions.slice(0, 10);
  APP.activeSession[lane] = id;
  return session;
}

/* ─── APP STATE ─────────────────────────────────────── */

const APP = {
  summary: null,
  currentView: 'chat-prime',
  selectedTaskId: null,
  pollInterval: null,

  // Per-lane chat state
  pendingResponse: { prime: false, local: false },
  lastSentAt:      { prime: null,  local: null },
  composerMode:    { prime: 'agent', local: 'agent' },

  // Sessions
  sessions:      { prime: loadSessions('prime'), local: loadSessions('local') },
  activeSession: { prime: getActiveSessionId('prime'), local: getActiveSessionId('local') },

  // Settings (cached from last fetch)
  registryConfig: null,
  generalSettings: null,
};

/* ─── API HELPERS ───────────────────────────────────── */

async function api(url, options = {}) {
  const resp = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

/* ─── FORMATTING ────────────────────────────────────── */

function parseISODate(iso) {
  if (!iso) return null;
  const raw = String(iso).trim();
  const hasZ = /(?:Z|[+-]\d{2}:\d{2})$/.test(raw);
  const d = new Date(hasZ ? raw : `${raw}Z`);
  return isNaN(d.getTime()) ? null : d;
}

function formatTime(iso) {
  const d = parseISODate(iso);
  if (!d) return '—';
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function relativeTime(iso) {
  const d = parseISODate(iso);
  if (!d) return '';
  const mins = Math.max(0, Math.floor((Date.now() - d.getTime()) / 60000));
  if (mins < 1)  return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24)  return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function humanLane(label) {
  const v = String(label || '').trim().toLowerCase();
  if (!v) return 'unknown';
  if (v === 'astrata') return 'System';
  if (v === 'principal') return 'You';
  return v.charAt(0).toUpperCase() + v.slice(1);
}

function pillClass(label) {
  const lo = String(label || '').toLowerCase();
  if (['complete','succeeded','delivered','pass','ok','nominal','good','started','running'].includes(lo)) return 'success';
  if (['failed','broken','critical','severe'].includes(lo)) return 'danger';
  if (['pending','working','blocked','deferred_for_thermal','degraded','draft'].includes(lo)) return 'warning';
  return 'neutral';
}

function truncate(str, len = 120) {
  const s = String(str || '');
  return s.length > len ? s.slice(0, len) + '…' : s;
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

/* ─── MARKDOWN ──────────────────────────────────────── */

function parseMarkdown(text) {
  if (typeof marked !== 'undefined') {
    return marked.parse(String(text || ''), { breaks: true, gfm: true });
  }
  // Minimal fallback
  return escapeHtml(text).replace(/\n/g, '<br>');
}

/* ─── DOM HELPERS ───────────────────────────────────── */

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  Object.entries(attrs).forEach(([k, v]) => {
    if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'html') node.innerHTML = v;
    else if (k === 'hidden') node.hidden = Boolean(v);
    else if (k === 'disabled') node.disabled = Boolean(v);
    else if (k === 'className') node.className = v;
    else node.setAttribute(k, v);
  });
  children.flat().forEach(child => {
    if (child == null) return;
    node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
  });
  return node;
}

function pill(label, tone = 'neutral') {
  return el('span', { className: `pill pill-${tone}` }, String(label));
}

function routePill(label, value, tone = 'neutral') {
  return el('span', { className: `pill pill-${tone}` },
    el('span', { className: 'pill-label', style: { marginRight: '4px' } }, label), String(value));
}

function metricTile(value, label) {
  return el('div', { className: 'metric-tile' },
    el('div', { className: 'metric-tile-value' }, String(value ?? '—')),
    el('div', { className: 'metric-tile-label' }, label));
}

function clearAndAppend(container, nodes) {
  container.innerHTML = '';
  const frag = document.createDocumentFragment();
  (Array.isArray(nodes) ? nodes : [nodes]).forEach(n => { if (n) frag.appendChild(n); });
  container.appendChild(frag);
}

function autoResizeTextarea(ta) {
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 200) + 'px';
}

/* ─── NAVIGATION ────────────────────────────────────── */

const VIEW_MAP = {
  'chat-prime': 'viewChatPrime',
  'chat-local': 'viewChatLocal',
  tasks:        'viewTasks',
  attempts:     'viewAttempts',
  artifacts:    'viewArtifacts',
  history:      'viewHistory',
  models:       'viewModels',
  settings:     'viewSettings',
  startup:      'viewStartup',
  'task-detail':'viewTaskDetail',
};

const CHAT_VIEWS = new Set(['chat-prime', 'chat-local']);

function switchView(viewId) {
  APP.currentView = viewId;

  // Hide all main views
  Object.values(VIEW_MAP).forEach(id => {
    const e = document.getElementById(id);
    if (e) e.hidden = true;
  });

  // Show target
  const target = document.getElementById(VIEW_MAP[viewId]);
  if (target) target.hidden = false;

  // Show/hide task rail for chat views
  const isChat = CHAT_VIEWS.has(viewId);
  const shell  = document.getElementById('appShell');
  const rail   = document.getElementById('taskRail');
  if (shell) shell.classList.toggle('show-task-rail', isChat);
  if (rail)  rail.hidden = !isChat;

  // Update icon rail active state
  document.querySelectorAll('.rail-btn[data-view]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.view === viewId);
  });

  // Update context panel content
  renderContextPanel(viewId);
}

/* ─── CONTEXT PANEL ─────────────────────────────────── */

function renderContextPanel(viewId) {
  const title   = document.getElementById('contextPanelTitle');
  const sub     = document.getElementById('contextPanelSub');
  const body    = document.getElementById('contextPanelBody');
  const actBtn  = document.getElementById('ctxActionBtn');
  if (!title || !body) return;

  body.innerHTML = '';
  actBtn.hidden  = true;

  if (viewId === 'chat-prime') {
    title.textContent = 'Prime';
    sub.textContent   = 'Conversations';
    actBtn.hidden     = false;
    actBtn.title      = 'New Chat';
    actBtn.onclick    = () => { createNewSession('prime'); renderContextPanel('chat-prime'); };
    renderSessionListInto(body, 'prime');

  } else if (viewId === 'chat-local') {
    title.textContent = 'Local';
    sub.textContent   = 'Conversations';
    actBtn.hidden     = false;
    actBtn.title      = 'New Chat';
    actBtn.onclick    = () => { createNewSession('local'); renderContextPanel('chat-local'); };
    renderSessionListInto(body, 'local');

  } else if (viewId === 'tasks' || viewId === 'task-detail' || viewId === 'attempts' || viewId === 'artifacts' || viewId === 'history') {
    title.textContent = 'Workspace';
    sub.textContent   = 'Views';
    renderWorkspaceNavInto(body);

  } else if (viewId === 'models' || viewId === 'settings' || viewId === 'startup') {
    title.textContent = 'System';
    sub.textContent   = 'Configuration';
    renderSystemNavInto(body);

  } else {
    title.textContent = 'Astrata';
    sub.textContent   = 'Navigation';
  }
}

function renderSessionListInto(container, lane) {
  const sessions = APP.sessions[lane];
  const activeId = APP.activeSession[lane];

  sessions.forEach(session => {
    const item = el('div', {
      className: `nav-item${session.id === activeId ? ' active' : ''}`,
      onClick: () => switchSession(lane, session.id),
    },
      el('svg', { className: 'nav-icon', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.8', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', html: '<circle cx="8" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="16" cy="12" r="1"/>'}),
      el('span', { style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: '1' } }, session.name),
    );
    container.appendChild(item);
  });

  if (!sessions.length) {
    container.appendChild(el('div', { style: { padding: '12px 10px', fontSize: '12px', color: 'var(--text-dim)' } }, 'No sessions yet.'));
  }
}

function renderWorkspaceNavInto(container) {
  const items = [
    { view: 'tasks',     icon: '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',    label: 'Tasks' },
    { view: 'attempts',  icon: '<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',             label: 'Attempts' },
    { view: 'artifacts', icon: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>',  label: 'Artifacts' },
    { view: 'history',   icon: '<path d="M3 3v5h5"/><path d="M3.05 13A9 9 0 1 0 6 5.3L3 8"/><path d="M12 7v5l3 3"/>',          label: 'History' },
  ];
  items.forEach(({ view, icon, label }) => {
    const item = el('div', {
      className: `nav-item${APP.currentView === view ? ' active' : ''}`,
      onClick: () => switchView(view),
    },
      el('svg', { className: 'nav-icon', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.8', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', html: icon }),
      label,
    );
    container.appendChild(item);
  });
}

function renderSystemNavInto(container) {
  const items = [
    { view: 'models',   icon: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/>',   label: 'Models & Runtime' },
    { view: 'settings', icon: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82"/>',                label: 'Settings' },
    { view: 'startup',  icon: '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',                                          label: 'Health' },
  ];
  items.forEach(({ view, icon, label }) => {
    const item = el('div', {
      className: `nav-item${APP.currentView === view ? ' active' : ''}`,
      onClick: () => switchView(view),
    },
      el('svg', { className: 'nav-icon', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.8', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', html: icon }),
      label,
    );
    container.appendChild(item);
  });
}

function switchSession(lane, sessionId) {
  persistActiveSession(lane, sessionId);
  APP.activeSession[lane] = sessionId;
  APP.pendingResponse[lane] = false;
  renderContextPanel(`chat-${lane}`);
  switchView(`chat-${lane}`);
  if (APP.summary) {
    const key = lane === 'local' ? 'local_conversation' : 'prime_conversation';
    const messages = APP.summary.communications?.[key] || [];
    renderChatMessages(
      document.getElementById(lane === 'prime' ? 'primeMessages' : 'localMessages'),
      messages, lane
    );
  }
}

/* ─── MODE TOGGLE ───────────────────────────────────── */

function setupModeToggle(lane) {
  const toggleId = lane === 'prime' ? 'primeModeToggle' : 'localModeToggle';
  const labelId  = lane === 'prime' ? 'primeModeLabel'  : 'localModeLabel';
  const toggle   = document.getElementById(toggleId);
  if (!toggle) return;

  toggle.querySelectorAll('.mode-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      toggle.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      APP.composerMode[lane] = btn.dataset.mode;
      const label = document.getElementById(labelId);
      if (label) {
        label.textContent = btn.dataset.mode === 'ephemeral'
          ? `Sends to ${humanLane(lane)} · no task created`
          : `Sends to ${humanLane(lane)} · creates tasks`;
      }
    });
  });
}

/* ─── SENDING MESSAGES ──────────────────────────────── */

async function sendMessage(lane, inputId) {
  const input = document.getElementById(inputId);
  const message = input.value.trim();
  if (!message || APP.pendingResponse[lane]) return;

  const btnId = lane === 'prime' ? 'primeSendBtn' : 'localSendBtn';
  const sendBtn = document.getElementById(btnId);
  sendBtn.disabled = true;
  APP.pendingResponse[lane] = true;
  APP.lastSentAt[lane]      = Date.now();

  // Optimistically render user bubble
  const msgContainer = document.getElementById(lane === 'prime' ? 'primeMessages' : 'localMessages');
  appendUserBubble(msgContainer, message);
  appendGeneratingBubble(msgContainer, lane);
  input.value = '';
  autoResizeTextarea(input);

  const mode = APP.composerMode[lane];
  const sessionId = APP.activeSession[lane];

  try {
    await api('/api/messages', {
      method: 'POST',
      body: JSON.stringify({
        message,
        recipient: lane,
        conversation_id: sessionId === 'default' ? '' : sessionId,
        intent: mode === 'ephemeral' ? 'ephemeral_message' : 'principal_message',
        kind: 'request',
      }),
    });
    await refresh();
  } catch (err) {
    console.error('Send failed:', err);
    APP.pendingResponse[lane] = false;
    removeGeneratingBubble(lane);
  } finally {
    sendBtn.disabled = false;
  }
}

function appendUserBubble(container, text) {
  const row = el('div', { className: 'message-row user' },
    el('div', { className: 'message-avatar user-av' }, 'Y'),
    el('div', {},
      el('div', { className: 'message-bubble' },
        el('div', { className: 'md-body', html: parseMarkdown(text) })
      ),
    ),
  );
  container.appendChild(row);
  container.scrollTop = container.scrollHeight;
}

function appendGeneratingBubble(container, lane) {
  const avatarStyle = lane === 'local'
    ? 'background:rgba(0,217,255,0.14);border:1px solid rgba(0,217,255,0.2);color:#9fefff;'
    : '';
  const avatarText = lane === 'local' ? 'L' : 'P';
  const row = el('div', {
    className: 'generating-row',
    id: `generatingBubble-${lane}`,
  },
    el('div', { className: `message-avatar ${lane === 'local' ? '' : 'agent'}`, style: { cssText: avatarStyle } }, avatarText),
    el('div', { className: 'generating-bubble' },
      el('div', { className: 'generating-dot' }),
      el('div', { className: 'generating-dot' }),
      el('div', { className: 'generating-dot' }),
    ),
  );
  container.appendChild(row);
  container.scrollTop = container.scrollHeight;
}

function removeGeneratingBubble(lane) {
  const bubble = document.getElementById(`generatingBubble-${lane}`);
  if (bubble) bubble.remove();
}

/* ─── MESSAGE GROUPING ──────────────────────────────── */

function getSenderKey(msg) {
  const sender = String(msg.sender || '').toLowerCase();
  if (sender === 'principal' || sender === 'user') return 'user';
  return sender;
}

function shouldGroup(current, previous) {
  if (!previous) return false;
  if (getSenderKey(current) !== getSenderKey(previous)) return false;
  const t1 = parseISODate(previous.created_at);
  const t2 = parseISODate(current.created_at);
  if (!t1 || !t2) return false;
  return Math.abs(t2.getTime() - t1.getTime()) <= 60 * 60 * 1000; // 1 hour
}

/* ─── CHAT RENDERING ────────────────────────────────── */

function renderChatMessages(container, messages, lane) {
  // Clear generating bubble
  removeGeneratingBubble(lane);

  if (!messages || messages.length === 0) {
    clearAndAppend(container, el('div', { className: 'chat-empty' },
      el('div', { className: 'chat-empty-icon' }, el('span', {}, '✦')),
      el('h2', {}, `Talk to ${humanLane(lane)}`),
      el('p', {}, `Send a message to start a conversation. Use Agent mode to spawn governed tasks, or Ephemeral for a lightweight one-off chat.`),
    ));
    return;
  }

  const frag = document.createDocumentFragment();
  messages.forEach((msg, i) => {
    const prev = messages[i - 1] || null;
    const grouped = shouldGroup(msg, prev);

    const senderKey = getSenderKey(msg);
    const isUser   = senderKey === 'user' || senderKey === 'principal';
    const isSystem = senderKey === 'astrata' || String(msg.kind || '').toLowerCase() === 'system_notice';

    let rowClass = 'message-row';
    let avatarClass = 'message-avatar';
    let avatarStyle = '';
    let avatarText  = '';

    if (isUser) {
      rowClass    += ' user';
      avatarClass += ' user-av';
      avatarText   = 'Y';
    } else if (isSystem) {
      rowClass    += ' system';
      avatarClass += ' system-av';
      avatarText   = 'S';
    } else {
      avatarClass += ' agent';
      avatarText   = lane === 'local' ? 'L' : 'P';
      if (lane === 'local') avatarStyle = 'background:rgba(0,217,255,0.14);border:1px solid rgba(0,217,255,0.2);color:#9fefff;';
    }

    const body = msg.message || msg.payload?.message || '(no message body)';

    const bubbleContent = el('div', { className: 'message-bubble' },
      el('div', { className: 'md-body', html: parseMarkdown(body) })
    );

    const meta = !grouped ? el('div', { className: 'message-meta' },
      el('span', {}, isUser ? 'You' : humanLane(msg.sender)),
      el('span', {}, '·'),
      el('span', {}, relativeTime(msg.created_at) || formatTime(msg.created_at)),
    ) : null;

    const avatar = !grouped
      ? el('div', { className: avatarClass, style: { cssText: avatarStyle } }, avatarText)
      : el('div', { style: { width: '30px', flexShrink: '0' } }); // spacer keeps alignment

    const row = el('div', {
      className: rowClass,
      style: grouped ? { marginTop: '-8px' } : {},
    },
      avatar,
      el('div', {}, bubbleContent, meta),
    );

    frag.appendChild(row);
  });

  // Check if pending response should still show
  const lastMsg = messages[messages.length - 1];
  const lastSenderKey = lastMsg ? getSenderKey(lastMsg) : null;
  const stillPending = APP.pendingResponse[lane] &&
    lastSenderKey === 'user' &&
    APP.lastSentAt[lane] &&
    (Date.now() - APP.lastSentAt[lane] < 90_000);

  container.innerHTML = '';
  container.appendChild(frag);

  if (stillPending) {
    appendGeneratingBubble(container, lane);
  } else {
    APP.pendingResponse[lane] = false;
  }

  requestAnimationFrame(() => { container.scrollTop = container.scrollHeight; });
}

/* ─── TASK TREE ─────────────────────────────────────── */

function buildTaskTree(tasks) {
  const map = {};
  tasks.forEach(t => { map[t.task_id] = { ...t, _children: [] }; });
  const roots = [];
  tasks.forEach(t => {
    const parentId = t.parent_task_id || t.parent_id;
    if (parentId && map[parentId]) {
      map[parentId]._children.push(map[t.task_id]);
    } else {
      roots.push(map[t.task_id]);
    }
  });
  return roots;
}

function renderTaskNode(task, depth = 0) {
  const hasChildren = task._children && task._children.length > 0;
  let childrenEl = null;

  const wrapper = el('div', { className: 'task-tree-node' });

  const needsYou = task.pending_question || task.status === 'needs_input';

  const header = el('div', { className: 'task-item', onClick: () => openTaskDetail(task.task_id) },
    el('div', { style: { display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px' } },
      hasChildren ? el('button', {
        className: 'task-expand-btn',
        onClick: (e) => {
          e.stopPropagation();
          if (childrenEl) {
            childrenEl.hidden = !childrenEl.hidden;
            e.currentTarget.textContent = childrenEl.hidden ? '▶' : '▼';
          }
        },
      }, '▼') : null,
      el('div', { className: 'task-item-title' }, task.title || 'Untitled'),
      needsYou ? pill('Needs You', 'warning') : null,
    ),
    task.description ? el('div', { className: 'task-item-desc' }, task.description) : null,
    el('div', { className: 'task-item-meta' },
      pill(task.status, pillClass(task.status)),
      pill(`p${task.priority}`, 'neutral'),
      pill(`u${task.urgency}`, 'neutral'),
      task.risk ? pill(task.risk, 'neutral') : null,
    ),
    el('div', { style: { fontSize: '11px', color: 'var(--text-dim)', marginTop: '4px' } },
      relativeTime(task.updated_at) || formatTime(task.updated_at)),
  );

  wrapper.appendChild(header);

  if (hasChildren) {
    childrenEl = el('div', { className: 'task-tree-children' },
      ...task._children.map(child => renderTaskNode(child, depth + 1))
    );
    wrapper.appendChild(childrenEl);
  }

  return wrapper;
}

/* ─── TASKS VIEW ────────────────────────────────────── */

function renderTasks(summary) {
  const tasks  = summary?.queue?.recent_tasks || [];
  const counts = summary?.queue?.counts || {};

  clearAndAppend(document.getElementById('taskMetrics'), [
    metricTile(counts.working || 0, 'Running'),
    metricTile(counts.pending || 0, 'Queued'),
    metricTile(counts.blocked || 0, 'Blocked'),
    metricTile(counts.complete || 0, 'Complete'),
    metricTile(counts.failed || 0, 'Failed'),
  ]);

  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const badge = document.getElementById('tasksBadge');
  const working = Number(counts.working || counts.Working || 0);
  badge.textContent = total;
  badge.hidden = total === 0;

  const list = document.getElementById('taskList');
  if (!tasks.length) {
    clearAndAppend(list, el('div', { className: 'empty-state' }, 'No tasks yet. Send a message to Prime or Local to generate work.'));
    return;
  }

  const tree = buildTaskTree(tasks);
  clearAndAppend(list, tree.map(task => renderTaskNode(task, 0)));
}

/* ─── ATTEMPTS VIEW ─────────────────────────────────── */

function renderAttempts(summary) {
  const attempts = summary?.attempts?.recent_attempts || [];
  const counts   = summary?.attempts?.counts || {};

  clearAndAppend(document.getElementById('attemptMetrics'), [
    metricTile(counts.succeeded || 0, 'Succeeded'),
    metricTile(counts.failed || 0, 'Failed'),
    metricTile(counts.degraded || 0, 'Degraded'),
    metricTile(counts.cancelled || 0, 'Cancelled'),
  ]);

  const list = document.getElementById('attemptList');
  if (!attempts.length) {
    clearAndAppend(list, el('div', { className: 'empty-state' }, 'No attempts recorded yet.'));
    return;
  }
  clearAndAppend(list, attempts.map(a =>
    el('div', { className: 'transcript-item' },
      el('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } },
        pill(a.outcome || 'unknown', pillClass(a.outcome)),
        pill(a.actor || 'unknown', 'neutral'),
        pill(a.verification_status || 'unverified', 'neutral'),
        a.degraded_reason ? pill(a.degraded_reason, 'warning') : null,
      ),
      el('div', { className: 'transcript-body' }, a.result_summary || a.failure_kind || 'No summary'),
      el('div', { className: 'transcript-meta' }, el('span', {}, formatTime(a.started_at))),
    )
  ));
}

/* ─── ARTIFACTS VIEW ────────────────────────────────── */

function renderArtifacts(summary) {
  const artifacts = summary?.artifacts?.recent || [];
  const counts    = summary?.artifacts?.counts || {};

  clearAndAppend(document.getElementById('artifactMetrics'),
    Object.entries(counts).map(([type, count]) => metricTile(count, type)));

  const list = document.getElementById('artifactList');
  if (!artifacts.length) {
    clearAndAppend(list, el('div', { className: 'empty-state' }, 'No artifacts produced yet.'));
    return;
  }
  clearAndAppend(list, artifacts.map(a =>
    el('div', { className: 'transcript-item' },
      el('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } },
        pill(a.artifact_type, 'accent'),
        pill(a.lifecycle_state || a.status, pillClass(a.status)),
      ),
      el('div', { className: 'task-item-title' }, a.title),
      a.content_summary ? el('div', { style: { fontSize: '12px', color: 'var(--text-muted)', lineHeight: '1.6', marginTop: '4px' } },
        truncate(a.content_summary, 200)) : null,
      el('div', { className: 'transcript-meta' }, el('span', {}, formatTime(a.updated_at))),
    )
  ));
}

/* ─── HISTORY VIEW ──────────────────────────────────── */

function renderHistory(summary) {
  const history = summary?.history || {};
  const overview = history?.overview || {};
  const runtime = history?.runtime || {};
  const bottlenecks = Array.isArray(history?.bottlenecks) ? history.bottlenecks : [];
  const reports = Array.isArray(history?.snapshot_reports) ? history.snapshot_reports : [];
  const events = Array.isArray(history?.recent_events) ? history.recent_events : [];
  const git = history?.git || {};

  clearAndAppend(document.getElementById('historyMetrics'), [
    metricTile(overview.tasks_total ?? 0, 'Tasks'),
    metricTile(overview.attempts_total ?? 0, 'Attempts'),
    metricTile(overview.blocked_tasks ?? 0, 'Blocked'),
    metricTile(overview.prime_attempts ?? 0, 'Prime'),
    metricTile(overview.avoidable_prime_attempts ?? 0, 'Avoidable Prime'),
    metricTile(overview.unjustified_prime_attempts ?? 0, 'Unjustified Prime'),
  ]);

  const sections = [];

  sections.push(el('div', { className: 'panel' },
    el('div', { className: 'panel-header' },
      el('div', { className: 'panel-title' }, `Morning Review Window (${history.window_hours || 24}h)`),
    ),
    el('div', { style: { fontSize: '13px', color: 'var(--text-muted)', lineHeight: '1.7' } },
      'Compact operational summary for overnight review. Use this as the first pass before drilling into tasks, attempts, artifacts, or raw traces.'),
  ));

  sections.push(el('div', { className: 'panel' },
    el('div', { className: 'panel-header' },
      el('div', { className: 'panel-title' }, 'Runtime Status'),
      runtime?.daemon_configured
        ? pill(runtime?.stale ? 'STALE' : 'ACTIVE', runtime?.stale ? 'warning' : 'success')
        : pill('NO DAEMON', 'neutral'),
    ),
    el('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '8px' } },
      routePill('HEARTBEAT', runtime?.latest_heartbeat ? relativeTime(runtime.latest_heartbeat.updated_at) : '—', 'neutral'),
      routePill('LAST SUCCESS', runtime?.last_successful_heartbeat ? relativeTime(runtime.last_successful_heartbeat.updated_at) : '—', 'neutral'),
      routePill('LAST FAILURE', runtime?.last_failed_heartbeat ? relativeTime(runtime.last_failed_heartbeat.updated_at) : 'none', 'neutral'),
    ),
    runtime?.latest_heartbeat_payload?.summary
      ? el('div', { style: { fontSize: '12px', color: 'var(--text-muted)', lineHeight: '1.6' } },
          `Latest cycle: ${runtime.latest_heartbeat_payload.summary.loop0_status || 'unknown'}; `
          + `${runtime.latest_heartbeat_payload.summary.step_count || 0} step(s); `
          + `${runtime.latest_heartbeat_payload.summary.lane_turns || 0} lane turn(s); `
          + `${runtime.latest_heartbeat_payload.summary.inbox_count || 0} inbox item(s).`)
      : el('div', { style: { fontSize: '12px', color: 'var(--text-dim)' } },
          'No daemon heartbeat has been recorded yet.'),
  ));

  sections.push(el('div', { className: 'panel' },
    el('div', { className: 'panel-header' },
      el('div', { className: 'panel-title' }, 'Git State'),
      git?.available ? pill(git?.dirty ? 'DIRTY' : 'CLEAN', git?.dirty ? 'warning' : 'success') : pill('UNAVAILABLE', 'neutral'),
    ),
    el('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '8px' } },
      git?.branch ? routePill('BRANCH', git.branch, 'accent') : routePill('BRANCH', '—', 'neutral'),
      routePill('AHEAD', String(git?.ahead ?? 0), 'neutral'),
      routePill('BEHIND', String(git?.behind ?? 0), 'neutral'),
      routePill('WORKTREES', String((git?.worktrees || []).length), 'neutral'),
    ),
    git?.modified_count
      ? el('div', { style: { fontSize: '12px', color: 'var(--text-muted)', lineHeight: '1.6' } },
          `${git.modified_count} modified path(s): ${truncate((git.modified_paths || []).join(', '), 220)}`)
      : el('div', { style: { fontSize: '12px', color: 'var(--text-dim)' } }, 'No modified paths recorded.'),
    (git?.worktrees || []).length
      ? el('div', { style: { marginTop: '10px', display: 'flex', flexDirection: 'column', gap: '6px' } },
          ...(git.worktrees || []).map(item =>
            el('div', { className: 'transcript-item' },
              el('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } },
                pill((item.branch || 'detached').replace('refs/heads/', ''), 'neutral'),
                pill(item.path || 'unknown', 'neutral'),
              )
            )
          ))
      : null,
  ));

  sections.push(el('div', { className: 'panel' },
    el('div', { className: 'panel-title' }, 'Bottlenecks'),
    bottlenecks.length
      ? bottlenecks.map(item =>
          el('div', { className: 'transcript-item' },
            el('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } },
              pill(item.severity || 'info', pillClass(item.severity || 'neutral')),
            ),
            el('div', { className: 'task-item-title' }, item.title || 'Bottleneck'),
            el('div', { className: 'transcript-body' }, item.summary || 'No summary'),
          )
        )
      : el('div', { className: 'empty-state' }, 'No major bottlenecks summarized right now.'),
  ));

  sections.push(el('div', { className: 'panel' },
    el('div', { className: 'panel-title' }, `Snapshot Reports (${reports.length})`),
    reports.length
      ? reports.map(report =>
          el('div', { className: 'transcript-item' },
            el('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } },
              pill(report.artifact_type || 'artifact', 'accent'),
              pill(report.lifecycle_state || report.status || 'unknown', 'neutral'),
            ),
            el('div', { className: 'task-item-title' }, report.title || 'Untitled report'),
            report.content_summary
              ? el('div', { className: 'transcript-body' }, truncate(report.content_summary, 220))
              : null,
            el('div', { className: 'transcript-meta' }, el('span', {}, formatTime(report.updated_at))),
          )
        )
      : el('div', { className: 'empty-state' }, 'No history-worthy reports yet. Run the system and this will fill in overnight.'),
  ));

  sections.push(el('div', { className: 'panel' },
    el('div', { className: 'panel-title' }, `Recent Events (${events.length})`),
    events.length
      ? events.map(item =>
          el('div', { className: 'transcript-item' },
            el('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } },
              pill(item.event_kind || 'event', 'neutral'),
              item.status ? pill(item.status, pillClass(item.status)) : null,
            ),
            el('div', { className: 'task-item-title' }, item.title || 'Untitled event'),
            item.summary ? el('div', { className: 'transcript-body' }, truncate(item.summary, 240)) : null,
            el('div', { className: 'transcript-meta' }, el('span', {}, relativeTime(item.timestamp) || formatTime(item.timestamp))),
          )
        )
      : el('div', { className: 'empty-state' }, 'No recent events recorded yet.'),
  ));

  clearAndAppend(document.getElementById('historyContent'), sections);
}

/* ─── MODELS & RUNTIME ──────────────────────────────── */

function renderModels(summary) {
  const runtime  = summary?.local_runtime || {};
  const models   = runtime?.models || [];
  const thermal  = runtime?.thermal_state || {};
  const decision = runtime?.thermal_decision || {};
  const rec      = runtime?.recommendation || {};
  const managed  = runtime?.managed_process || {};
  const running  = Boolean(managed?.running);

  clearAndAppend(document.getElementById('runtimeStatus'), el('div', { className: 'runtime-card' },
    el('div', { className: 'runtime-status-row' },
      el('div', { className: `runtime-dot ${running ? 'running' : 'stopped'}` }),
      el('span', { style: { fontWeight: '700', fontSize: '14px' } },
        running ? 'Runtime Running' : 'Runtime Stopped'),
      running && managed?.endpoint ? el('span', { style: { fontSize: '12px', color: 'var(--text-dim)', marginLeft: '8px' } },
        managed.endpoint) : null,
    ),
    el('div', { className: 'metric-grid' },
      metricTile(thermal?.thermal_pressure || 'unknown', 'Thermal'),
      metricTile(decision?.action || '—', 'Decision'),
      metricTile(rec?.model?.display_name || 'None', 'Recommended'),
      metricTile(models.length, 'Models Found'),
    ),
  ));

  // Local runtime indicator in Local chat header
  const indicator = document.getElementById('localRuntimeIndicator');
  if (indicator) {
    indicator.textContent = running ? '● Running' : '○ Stopped';
    indicator.className = `pill pill-${running ? 'success' : 'neutral'}`;
  }

  const grid = document.getElementById('modelGrid');
  if (!models.length) {
    clearAndAppend(grid, el('div', { className: 'empty-state' },
      'No local models discovered. Configure model search paths in Settings.'));
    return;
  }

  clearAndAppend(grid, models.map(model => {
    const isRecommended = rec?.model?.model_id === model.model_id;
    return el('div', { className: 'model-card' },
      el('div', { style: { display: 'flex', gap: '8px', alignItems: 'flex-start' } },
        el('div', { className: 'model-card-name', style: { flex: '1' } }, model.display_name || model.model_id),
        isRecommended ? pill('Recommended', 'success') : null,
      ),
      el('div', { className: 'model-card-meta' },
        model.family ? pill(model.family, 'neutral') : null,
        model.role ? pill(model.role, 'neutral') : null,
        model.quantization ? pill(model.quantization, 'neutral') : null,
      ),
      model.path ? el('div', { className: 'model-card-detail' }, model.path) : null,
      el('div', { className: 'model-card-actions' },
        el('button', {
          className: 'btn btn-secondary btn-sm',
          onClick: async (e) => {
            const btn = e.currentTarget;
            btn.disabled = true;
            btn.textContent = 'Starting…';
            try {
              await api(`/api/local-runtime/start?model_id=${encodeURIComponent(model.model_id)}`, { method: 'POST' });
              await refresh();
            } catch (err) { console.error(err); }
            finally {
              btn.disabled = false;
              btn.textContent = '▶ Load';
            }
          },
        }, '▶ Load'),
      ),
    );
  }));
}

/* ─── SETTINGS / REGISTRY EDITOR ───────────────────── */

async function loadSettings() {
  // Load registry from API if available (Astrata may not have this endpoint yet — graceful fallback)
  try {
    const res = await api('/api/settings');
    APP.generalSettings = res;
  } catch { APP.generalSettings = null; }

  try {
    const res = await api('/api/registry');
    APP.registryConfig = res;
  } catch { APP.registryConfig = null; }
}

function renderSettings(summary) {
  const content = document.getElementById('settingsContent');
  const sections = [];

  // ── Providers (read-only if no registry endpoint) ──
  const providers = summary?.providers || {};
  const route = providers?.default_route;

  sections.push(el('div', { className: 'panel' },
    el('div', { className: 'panel-header' },
      el('div', { className: 'panel-title' }, 'Active Route'),
    ),
    route ? el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } },
      routePill('PROVIDER', route.provider || '—', 'accent'),
      route.cli_tool ? routePill('CLI', route.cli_tool, 'neutral') : null,
      route.model ? routePill('MODEL', route.model, 'neutral') : null,
    ) : el('div', { style: { color: 'var(--text-dim)', fontSize: '13px' } }, 'No default route resolved.'),
  ));

  // ── Inference sources list ──
  if (providers?.inference_sources?.length) {
    sections.push(el('div', { className: 'panel' },
      el('div', { className: 'panel-title' }, `Inference Sources (${providers.inference_sources.length})`),
      ...providers.inference_sources.map(src =>
        el('div', { className: 'transcript-item', style: { gap: '6px' } },
          el('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } },
            pill(src.provider || '—', 'accent'),
            src.transport ? pill(src.transport, 'neutral') : null,
            src.cli_tool ? pill(src.cli_tool, 'neutral') : null,
          ),
          src.default_model ? el('div', { style: { fontSize: '12px', color: 'var(--text-muted)' } },
            `Model: ${src.default_model}`) : null,
          src.endpoint_url ? el('div', { style: { fontSize: '11px', color: 'var(--text-dim)', fontFamily: 'var(--font-mono)' } },
            src.endpoint_url) : null,
        )
      ),
    ));
  }

  // ── Inference telemetry ──
  const inference = summary?.inference;
  if (inference && typeof inference === 'object') {
    const qs = Array.isArray(inference.quota_snapshots) ? inference.quota_snapshots : [];
    if (qs.length) {
      sections.push(el('div', { className: 'panel' },
        el('div', { className: 'panel-title' }, 'Quota Snapshots'),
        ...qs.map(q =>
          el('div', { className: 'transcript-item' },
            el('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } },
              routePill('SOURCE', q.source || q.route?.provider || '—', q.allowed ? 'success' : 'warning'),
              q.remaining != null ? routePill('REMAINING', String(q.remaining), 'neutral') : null,
            ),
          ),
        ),
      ));
    }
  }

  // ── Throttle mode ──
  const modes = [
    { id: 'quiet', label: 'Quiet', desc: 'Conservative pacing, lower annoyance.' },
    { id: 'turbo', label: 'Turbo', desc: 'Greedier throughput, faster turn-taking.' },
  ];

  sections.push(el('div', { className: 'panel' },
    el('div', { className: 'panel-title' }, 'Throttle Mode'),
    el('div', { className: 'toggle-btn-row' },
      ...modes.map(mode => {
        const btn = el('button', { className: 'toggle-btn' },
          el('div', { style: { fontWeight: '700', fontSize: '12px', marginBottom: '2px' } }, mode.label),
          el('div', { style: { fontSize: '11px', color: 'var(--text-dim)' } }, mode.desc),
        );
        btn.addEventListener('click', async () => {
          document.querySelectorAll('#throttleModeRow .toggle-btn').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          const indicator = document.getElementById('savingIndicator');
          if (indicator) indicator.hidden = false;
          try {
            const payload = mode.id === 'turbo'
              ? { inference_throttle_policy: { throttle_mode: 'greedy', operator_comfort: { profile: 'aggressive', ambiguity_bias: 'prefer_action', allow_annoying_if_explicit: true } } }
              : { inference_throttle_policy: { throttle_mode: 'hard', operator_comfort: { profile: 'quiet', ambiguity_bias: 'prefer_quiet', allow_annoying_if_explicit: false } } };
            await api('/api/settings', { method: 'POST', body: JSON.stringify(payload) });
          } catch (err) { console.error('Failed to save throttle mode:', err); }
          finally { if (indicator) indicator.hidden = true; }
        });
        return btn;
      }),
    ),
  ));
  // Add id for targeted manipulation
  sections[sections.length - 1].id = 'throttleModeRow';

  // ── Run loop + purge ──
  sections.push(el('div', { className: 'panel' },
    el('div', { className: 'panel-title' }, 'Actions'),
    el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } },
      el('button', {
        className: 'btn btn-secondary btn-sm',
        onClick: async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true; btn.textContent = 'Running…';
          try { await api('/api/loop0/run?steps=1', { method: 'POST' }); await refresh(); }
          catch (err) { console.error(err); }
          finally { btn.disabled = false; btn.textContent = '⟳ Run Loop Step'; }
        },
      }, '⟳ Run Loop Step'),
    ),
    el('div', { style: { fontSize: '12px', color: 'var(--text-dim)', marginTop: '6px', lineHeight: '1.6' } },
      'Loop steps drive task orchestration: picks up pending work from the queue and runs it through the appropriate lane.'),
  ));

  clearAndAppend(content, sections);
}

/* ─── STARTUP & HEALTH ──────────────────────────────── */

function renderStartup(summary) {
  const startup = summary?.startup || {};
  const content = document.getElementById('startupContent');
  const sections = [];

  const preflight = startup?.preflight;
  if (preflight) {
    const issues = preflight.issues || [];
    sections.push(el('div', { className: 'panel' },
      el('div', { className: 'panel-header' },
        el('div', { className: 'panel-title' }, 'Pre-Inference Preflight'),
        pill(preflight.ok ? 'PASS' : 'ISSUES', preflight.ok ? 'success' : 'warning'),
      ),
      el('div', { style: { fontSize: '13px', color: 'var(--text-muted)', lineHeight: '1.7' } },
        issues.length
          ? issues.map(i => i.kind || i.summary || JSON.stringify(i)).join(', ')
          : 'Managed runtime and core imports look sane.',
      ),
      preflight.selected_python ? el('div', { style: { fontSize: '11px', color: 'var(--text-dim)', marginTop: '6px' } },
        `Python: ${preflight.selected_python}`) : null,
    ));
  }

  const runtime = startup?.runtime;
  if (runtime) {
    const issues = runtime.issues || [];
    sections.push(el('div', { className: 'panel' },
      el('div', { className: 'panel-header' },
        el('div', { className: 'panel-title' }, 'Post-Boot Self-Reflection'),
        pill(runtime.ok ? 'PASS' : 'ISSUES', runtime.ok ? 'success' : 'warning'),
      ),
      el('div', { style: { fontSize: '13px', color: 'var(--text-muted)', lineHeight: '1.7' } },
        runtime.summary || 'No runtime reflection yet.',
      ),
      issues.length ? el('div', { style: { marginTop: '10px', display: 'flex', flexDirection: 'column', gap: '6px' } },
        ...issues.map(issue =>
          el('div', { className: 'transcript-item' },
            el('div', { className: 'transcript-body' }, issue.kind || issue.summary || JSON.stringify(issue))
          )
        )
      ) : null,
    ));
  }

  // Communication lanes
  const comms = summary?.communications;
  if (comms?.lane_counts) {
    sections.push(el('div', { className: 'panel' },
      el('div', { className: 'panel-title' }, 'Communication Lanes'),
      el('div', { className: 'metric-grid' },
        ...Object.entries(comms.lane_counts).map(([lane, count]) => metricTile(count, humanLane(lane))),
      ),
    ));
  }

  const astrataInbox = comms?.astrata_inbox || [];
  if (astrataInbox.length) {
    sections.push(el('div', { className: 'panel' },
      el('div', { className: 'panel-title' }, `System Inbox (${astrataInbox.length})`),
      ...astrataInbox.map(m =>
        el('div', { className: 'transcript-item' },
          el('div', { className: `transcript-sender sender-${String(m.sender).toLowerCase()}` }, humanLane(m.sender)),
          el('div', { className: 'transcript-body' }, truncate(m.message || '(no body)', 200)),
          el('div', { className: 'transcript-meta' },
            el('span', {}, m.intent || m.kind),
            el('span', {}, '·'),
            el('span', {}, relativeTime(m.created_at)),
          ),
        )
      ),
    ));
  }

  if (!sections.length) {
    sections.push(el('div', { className: 'empty-state' }, 'No health data yet.'));
  }
  clearAndAppend(content, sections);
}

/* ─── TASK RAIL (right side) ───────────────────────────── */

function renderTaskRail(summary) {
  const body   = document.getElementById('taskRailBody');
  const counts = summary?.queue?.counts || {};
  const tasks  = summary?.queue?.recent_tasks || [];

  // Telemetry numbers
  const setTRT = (id, value, tone = '') => {
    const e = document.getElementById(id);
    if (!e) return;
    e.textContent = value;
    e.className = `task-rail-tel-val${tone ? ' ' + tone : ''}`;
  };
  const running  = Number(counts.working  || 0);
  const queued   = Number(counts.pending  || 0);
  const blocked  = Number(counts.blocked  || 0);
  const complete = Number(counts.complete || 0);
  setTRT('trt-running',  running  || '—', running  > 0 ? 'good' : '');
  setTRT('trt-queued',   queued   || '—', queued   > 0 ? 'warn' : '');
  setTRT('trt-blocked',  blocked  || '—', blocked  > 0 ? 'bad'  : '');
  setTRT('trt-complete', complete || '—');

  if (!body) return;

  if (!tasks.length) {
    clearAndAppend(body, el('div', { className: 'task-rail-empty' },
      'No tasks yet.\nSend a message to Prime or Local to create work.'));
    return;
  }

  const frag = document.createDocumentFragment();
  tasks.slice(0, 20).forEach(task => {
    const isWorking = task.status === 'working';
    const item = el('div', {
      className: `task-rail-item${isWorking ? ' working' : ''}`,
      onClick: () => openTaskDetail(task.task_id),
    },
      el('div', { className: 'task-rail-item-title' }, task.title || 'Untitled'),
      el('div', { className: 'task-rail-item-meta' },
        pill(task.status, pillClass(task.status)),
        pill(`p${task.priority}`, 'neutral'),
      ),
    );
    frag.appendChild(item);
  });
  body.innerHTML = '';
  body.appendChild(frag);
}

/* ─── TASK DETAIL ───────────────────────────────────── */

async function openTaskDetail(taskId) {
  APP.selectedTaskId = taskId;
  switchView('task-detail');

  const titleEl  = document.getElementById('taskDetailTitle');
  const descEl   = document.getElementById('taskDetailDesc');
  const content  = document.getElementById('taskDetailContent');
  titleEl.textContent = 'Loading…';
  descEl.textContent  = '';
  content.innerHTML   = '<div class="empty-state"><div class="spinner"></div></div>';

  try {
    const detail = await api(`/api/tasks/${taskId}`);
    const task   = detail.task;
    titleEl.textContent = task?.title || 'Task Detail';
    descEl.textContent  = task?.description || '';

    const sections = [];

    sections.push(el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } },
      pill(task.status, pillClass(task.status)),
      routePill('PRIORITY', `p${task.priority}`, 'neutral'),
      routePill('URGENCY',  `u${task.urgency}`, 'neutral'),
      routePill('RISK',     task.risk || 'moderate', 'neutral'),
    ));

    if (detail.blockers?.length) {
      sections.push(el('div', { className: 'panel' },
        el('div', { className: 'panel-title' }, 'Blockers'),
        ...detail.blockers.map(b =>
          el('div', { className: 'transcript-item' },
            el('div', { className: 'transcript-sender sender-system' }, b.kind),
            el('div', { className: 'transcript-body' }, b.summary),
          )
        ),
      ));
    }

    if (detail.attempts?.length) {
      sections.push(el('div', { className: 'panel' },
        el('div', { className: 'panel-title' }, `Attempts (${detail.attempts.length})`),
        ...detail.attempts.map(a =>
          el('div', { className: 'transcript-item' },
            el('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } },
              pill(a.outcome || 'unknown', pillClass(a.outcome)),
              pill(a.actor || 'unknown', 'neutral'),
              pill(a.verification_status || 'unverified', 'neutral'),
            ),
            el('div', { className: 'transcript-body' }, a.result_summary || a.failure_kind || 'No summary'),
            el('div', { className: 'transcript-meta' }, el('span', {}, formatTime(a.started_at))),
          )
        ),
      ));
    }

    if (detail.messages?.length) {
      sections.push(el('div', { className: 'panel' },
        el('div', { className: 'panel-title' }, `Messages (${detail.messages.length})`),
        ...detail.messages.map(m =>
          el('div', { className: 'transcript-item' },
            el('div', { className: `transcript-sender sender-${String(m.sender).toLowerCase()}` },
              `${humanLane(m.sender)} → ${humanLane(m.recipient)}`),
            el('div', { className: 'transcript-body md-body', html: parseMarkdown(truncate(m.message || '', 300)) }),
            el('div', { className: 'transcript-meta' },
              el('span', {}, m.intent || m.kind),
              el('span', {}, '·'),
              el('span', {}, relativeTime(m.created_at)),
            ),
          )
        ),
      ));
    }

    if (detail.artifacts?.length) {
      sections.push(el('div', { className: 'panel' },
        el('div', { className: 'panel-title' }, `Artifacts (${detail.artifacts.length})`),
        ...detail.artifacts.map(a =>
          el('div', { className: 'transcript-item' },
            el('div', { style: { display: 'flex', gap: '6px' } },
              pill(a.artifact_type, 'accent'),
              pill(a.lifecycle_state || a.status, 'neutral'),
            ),
            el('div', { className: 'task-item-title' }, a.title),
            a.content_summary ? el('div', { style: { fontSize: '12px', color: 'var(--text-muted)', marginTop: '4px' } },
              truncate(a.content_summary, 200)) : null,
          )
        ),
      ));
    }

    // Related
    const rels = detail.relationships || {};
    const related = [];
    if (rels.parent) related.push({ label: 'Parent', task: rels.parent });
    (rels.children || []).forEach(t => related.push({ label: 'Child', task: t }));
    (rels.siblings || []).forEach(t => related.push({ label: 'Sibling', task: t }));

    if (related.length) {
      sections.push(el('div', { className: 'panel' },
        el('div', { className: 'panel-title' }, 'Related Tasks'),
        ...related.map(({ label, task: t }) =>
          el('div', { className: 'task-item', style: { cursor: 'pointer' }, onClick: () => openTaskDetail(t.task_id) },
            el('div', { style: { display: 'flex', gap: '6px', marginBottom: '4px' } },
              pill(label, 'neutral'),
              pill(t.status, pillClass(t.status)),
            ),
            el('div', { className: 'task-item-title' }, t.title),
          )
        ),
      ));
    }

    clearAndAppend(content, sections);
  } catch (err) {
    console.error('Task detail error:', err);
    clearAndAppend(content, el('div', { className: 'empty-state' }, 'Failed to load task detail.'));
  }
}

/* ─── MAIN REFRESH ──────────────────────────────────── */

async function refresh() {
  try {
    const summary = await api('/api/summary');
    APP.summary   = summary;

    // Chat lanes
    const primeConv = summary?.communications?.prime_conversation || [];
    const localConv = summary?.communications?.local_conversation || [];
    renderChatMessages(document.getElementById('primeMessages'), primeConv, 'prime');
    renderChatMessages(document.getElementById('localMessages'), localConv, 'local');

    // Route label
    const route = summary?.providers?.default_route;
    if (route) {
      document.getElementById('primeRouteLabel').textContent =
        `${route.provider || 'cloud'}${route.cli_tool ? ':' + route.cli_tool : ''} — coordinating intelligence`;
    }

    // Unread badges (icon rail)
    const primeMsgCount = (summary?.communications?.prime_inbox || []).length;
    const localMsgCount = (summary?.communications?.local_inbox || []).length;
    const railPrimeBadge = document.getElementById('railPrimeBadge');
    const railLocalBadge = document.getElementById('railLocalBadge');
    if (railPrimeBadge) railPrimeBadge.hidden = primeMsgCount === 0;
    if (railLocalBadge) railLocalBadge.hidden = localMsgCount === 0;

    // Other views
    renderTasks(summary);
    renderAttempts(summary);
    renderArtifacts(summary);
    renderHistory(summary);
    renderModels(summary);
    renderSettings(summary);
    renderStartup(summary);
    renderTaskRail(summary);
    updateTelemetry(summary);

  } catch (err) {
    console.error('Refresh failed:', err);
  }
}

/* ─── CONTEXT PANEL TELEMETRY ────────────────────────────── */

function updateTelemetry(summary) {
  const counts  = summary?.queue?.counts || {};
  const runtime = summary?.local_runtime || {};
  const route   = summary?.providers?.default_route;
  const managed = runtime?.managed_process;

  const set = (id, value, tone = '') => {
    const e = document.getElementById(id);
    if (!e) return;
    e.textContent = value;
    e.className = `telemetry-row-value${tone ? ' ' + tone : ''}`;
  };

  const running = Number(counts.working || 0);
  const queued  = Number(counts.pending || 0);
  const blocked = Number(counts.blocked || 0);

  set('tel-running', running || '—', running > 0 ? 'good' : '');
  set('tel-queued',  queued  || '—', queued  > 0 ? 'warn' : '');
  set('tel-blocked', blocked || '—', blocked > 0 ? 'bad'  : '');
  set('tel-route',   route ? `${route.provider || '?'}${route.cli_tool ? ':' + route.cli_tool : ''}` : '—');
  set('tel-runtime', managed?.running ? 'live' : 'stopped', managed?.running ? 'good' : '');
}

/* ─── INIT ──────────────────────────────────────────── */

window.addEventListener('DOMContentLoaded', async () => {
  // Icon rail navigation
  document.querySelectorAll('.rail-btn[data-view]').forEach(btn => {
    btn.addEventListener('click', () => switchView(btn.dataset.view));
  });

  // Back from task detail
  document.getElementById('backFromDetail').addEventListener('click', () => switchView('tasks'));

  // Task rail refresh
  document.getElementById('taskRailRefreshBtn').addEventListener('click', refresh);

  // Refresh buttons
  document.getElementById('refreshBtn').addEventListener('click', refresh);
  document.getElementById('refreshTasksBtn').addEventListener('click', refresh);

  // Loop step
  document.getElementById('runLoopBtn').addEventListener('click', async () => {
    const btn = document.getElementById('runLoopBtn');
    btn.disabled = true; btn.textContent = '⟳ Running…';
    try { await api('/api/loop0/run?steps=1', { method: 'POST' }); await refresh(); }
    catch (err) { console.error(err); }
    finally { btn.disabled = false; btn.textContent = '⟳ Loop'; }
  });

  // Prime composer
  document.getElementById('primeSendBtn').addEventListener('click', () => sendMessage('prime', 'primeInput'));
  document.getElementById('primeInput').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage('prime', 'primeInput'); }
  });
  document.getElementById('primeInput').addEventListener('input', e => autoResizeTextarea(e.target));

  // Local composer
  document.getElementById('localSendBtn').addEventListener('click', () => sendMessage('local', 'localInput'));
  document.getElementById('localInput').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage('local', 'localInput'); }
  });
  document.getElementById('localInput').addEventListener('input', e => autoResizeTextarea(e.target));

  // Runtime controls
  document.getElementById('startRuntimeBtn').addEventListener('click', async () => {
    const btn = document.getElementById('startRuntimeBtn');
    btn.disabled = true; btn.textContent = 'Starting…';
    try { await api('/api/local-runtime/start', { method: 'POST' }); await refresh(); }
    catch (err) { console.error(err); }
    finally { btn.disabled = false; btn.textContent = '▶ Start'; }
  });
  document.getElementById('stopRuntimeBtn').addEventListener('click', async () => {
    const btn = document.getElementById('stopRuntimeBtn');
    btn.disabled = true; btn.textContent = 'Stopping…';
    try { await api('/api/local-runtime/stop', { method: 'POST' }); await refresh(); }
    catch (err) { console.error(err); }
    finally { btn.disabled = false; btn.textContent = '■ Stop'; }
  });

  // Mode toggles
  setupModeToggle('prime');
  setupModeToggle('local');

  // Context panel
  renderContextPanel('chat-prime');

  // Initial load
  await refresh();

  // Poll every 12s
  APP.pollInterval = setInterval(refresh, 12000);
});
