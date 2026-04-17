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
  APP.getSessions(lane); // ensure initialized
  APP.sessions[lane] = sessions.slice(0, 10);
  APP.activeSession[lane] = id;
  return session;
}

/* ─── APP STATE ─────────────────────────────────────── */

const APP = {
  summary: null,
  currentView: 'chat',
  activeLane: 'prime', // currently selected agent tab
  selectedTaskId: null,
  pollInterval: null,
  refreshInFlight: null,
  desktopBackendStatus: null,
  desktopAvailable: typeof window !== 'undefined' && Boolean(window.__TAURI_INTERNALS__?.invoke),
  desktopOverlayDismissed: false,
  lastBackendRecoveryAt: 0,
  relayPairing: null,
  accountLinkResult: null,
  connectorSetupResult: null,

  // Per-lane chat state
  pendingResponse: {},
  lastSentAt: {},
  composerMode: {},

  // Sessions
  sessions: {},
  activeSession: {},

  // Settings (cached from last fetch)
  registryConfig: null,
  generalSettings: null,

  getSessions(lane) {
    if (!this.sessions[lane]) this.sessions[lane] = loadSessions(lane);
    return this.sessions[lane];
  },
  getActiveSession(lane) {
    if (!this.activeSession[lane]) this.activeSession[lane] = getActiveSessionId(lane);
    return this.activeSession[lane];
  },
  getComposerMode(lane) {
    if (!this.composerMode[lane]) this.composerMode[lane] = 'agent';
    return this.composerMode[lane];
  }
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

async function desktopInvoke(command, args = {}) {
  const invoke = window.__TAURI_INTERNALS__?.invoke;
  if (!invoke) throw new Error('Astrata desktop controls are unavailable.');
  return invoke(command, args);
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
  chat:         'viewChat',
  tasks:        'viewTasks',
  attempts:     'viewAttempts',
  artifacts:    'viewArtifacts',
  history:      'viewHistory',
  models:       'viewModels',
  settings:     'viewSettings',
  startup:      'viewStartup',
  'task-detail':'viewTaskDetail',
};

const CHAT_VIEWS = new Set(['chat']);

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

  if (viewId === 'chat') {
    title.textContent = APP.activeLane === 'prime' ? 'Prime' : (APP.activeLane === 'local' ? 'Local' : humanLane(APP.activeLane));
    sub.textContent   = 'Conversations';
    actBtn.hidden     = false;
    actBtn.title      = 'New Chat';
    actBtn.onclick    = () => { createNewSession(APP.activeLane); renderContextPanel('chat'); };
    renderSessionListInto(body, APP.activeLane);

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
  const sessions = APP.getSessions(lane) || [];
  const activeId = APP.getActiveSession(lane) || 'default';

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
  
  // Set global lane before rendering
  APP.activeLane = lane;
  
  renderContextPanel('chat');
  switchView('chat');
  if (APP.summary) {
    const key = lane === 'local' ? 'local_conversation' : 'prime_conversation';
    const messages = APP.summary.communications?.[key] || [];
    renderChatMessages(
      document.getElementById('chatMessages'),
      messages, lane
    );
  }
}

function switchAgent(lane) {
  APP.activeLane = lane;
  const activeId = APP.getActiveSession(lane);
  switchSession(lane, activeId);
}


/* ─── MODE TOGGLE ───────────────────────────────────── */

function setupModeToggle() {
  const toggle = document.getElementById('chatModeToggle');
  const labelObj = document.getElementById('chatModeLabel');
  if (!toggle) return;

  toggle.querySelectorAll('.mode-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const lane = APP.activeLane;
      toggle.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      APP.composerMode[lane] = btn.dataset.mode;
      if (labelObj) {
        labelObj.textContent = btn.dataset.mode === 'ephemeral'
          ? `Sends to ${humanLane(lane)} · no task created`
          : `Sends to ${humanLane(lane)} · creates tasks`;
      }
    });
  });
}

/* ─── SENDING MESSAGES ──────────────────────────────── */

async function sendMessage() {
  const lane = APP.activeLane;
  const input = document.getElementById('chatInput');
  const message = input.value.trim();
  if (!message || APP.pendingResponse[lane]) return;

  const sendBtn = document.getElementById('chatSendBtn');
  sendBtn.disabled = true;
  APP.pendingResponse[lane] = true;
  APP.lastSentAt[lane]      = Date.now();

  // Optimistically render user bubble
  const msgContainer = document.getElementById('chatMessages');
  if (msgContainer) {
    appendUserBubble(msgContainer, message);
    appendGeneratingBubble(msgContainer, lane);
  }
  input.value = '';
  autoResizeTextarea(input);

  const mode = APP.getComposerMode(lane);
  const sessionId = APP.getActiveSession(lane);

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
    if (container.dataset.lastMsgIds === 'empty') return;
    container.dataset.lastMsgIds = 'empty';
    clearAndAppend(container, el('div', { className: 'chat-empty' },
      el('div', { className: 'chat-empty-icon' }, el('span', {}, '✦')),
      el('h2', {}, `Talk to ${humanLane(lane)}`),
      el('p', {}, `Send a message to start a conversation. Use Agent mode to spawn governed tasks, or Ephemeral for a lightweight one-off chat.`),
    ));
    return;
  }

  const msgIds = messages.map(m => m.communication_id || m.created_at || '').join(',');
  const isPending = APP.pendingResponse[lane] || false;
  const sig = `${lane}-${msgIds}-pending:${isPending}`;
  
  if (container.dataset.lastMsgIds === sig) {
      return; // Skip re-rendering if nothing changed
  }
  container.dataset.lastMsgIds = sig;

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
  const metrics = document.getElementById('taskMetrics');
  const badge = document.getElementById('tasksBadge');
  const list = document.getElementById('taskList');

  if (!metrics || !list) return;

  clearAndAppend(metrics, [
    metricTile(counts.working || 0, 'Running'),
    metricTile(counts.pending || 0, 'Queued'),
    metricTile(counts.blocked || 0, 'Blocked'),
    metricTile(counts.complete || 0, 'Complete'),
    metricTile(counts.failed || 0, 'Failed'),
  ]);

  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const working = Number(counts.working || counts.Working || 0);
  if (badge) {
    badge.textContent = total;
    badge.hidden = total === 0;
  }

  if (!tasks.length) {
    if (list.dataset.lastSig === 'empty') return;
    list.dataset.lastSig = 'empty';
    clearAndAppend(list, el('div', { className: 'empty-state' }, 'No tasks yet. Send a message to Prime or Local to generate work.'));
    return;
  }

  const sig = tasks.map(t => `${t.task_id}-${t.status}-${t.updated_at}`).join(',');
  if (list.dataset.lastSig === sig) return;
  list.dataset.lastSig = sig;

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
    if (list.dataset.lastSig === 'empty') return;
    list.dataset.lastSig = 'empty';
    clearAndAppend(list, el('div', { className: 'empty-state' }, 'No attempts recorded yet.'));
    return;
  }

  const sig = attempts.map(a => `${a.attempt_id || a.started_at}-${a.outcome}`).join(',');
  if (list.dataset.lastSig === sig) return;
  list.dataset.lastSig = sig;

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
    if (list.dataset.lastSig === 'empty') return;
    list.dataset.lastSig = 'empty';
    clearAndAppend(list, el('div', { className: 'empty-state' }, 'No artifacts produced yet.'));
    return;
  }

  const sig = artifacts.map(a => `${a.artifact_id || a.updated_at}-${a.status}`).join(',');
  if (list.dataset.lastSig === sig) return;
  list.dataset.lastSig = sig;

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
  const desktop  = APP.desktopBackendStatus || summary?.desktop_backend || {};
  const models   = runtime?.models || [];
  const thermal  = runtime?.thermal_state || {};
  const decision = runtime?.thermal_decision || {};
  const rec      = runtime?.recommendation || {};
  const managed  = runtime?.managed_process || {};
  const running  = Boolean(managed?.running);
  const backendRunning = Boolean(desktop?.backend_running) || Boolean(summary);
  const backendStopped = Boolean(desktop?.backend_deliberately_stopped) && !backendRunning;

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
    APP.desktopAvailable ? el('div', { style: { marginTop: '12px', fontSize: '12px', color: 'var(--text-muted)' } },
      `Desktop backend: ${backendRunning ? 'running' : (backendStopped ? 'stopped deliberately' : 'recovering')}.`) : null,
  ));

  const desktopIndicator = document.getElementById('desktopBackendIndicator');
  if (desktopIndicator) {
    const runningText = backendRunning ? 'backend: running'
      : (backendStopped ? 'backend: stopped' : 'backend: recovering');
    desktopIndicator.textContent = runningText;
    desktopIndicator.className = `pill pill-${backendRunning ? 'success' : (backendStopped ? 'warning' : 'neutral')}`;
  }

  const stopBackendBtn = document.getElementById('stopAppBackendBtn');
  const resumeBackendBtn = document.getElementById('resumeAppBackendBtn');
  if (stopBackendBtn) stopBackendBtn.disabled = !APP.desktopAvailable || !backendRunning;
  if (resumeBackendBtn) resumeBackendBtn.disabled = !APP.desktopAvailable || backendRunning;

  renderConnectorStatus(summary);

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

function setDesktopOverlay({ title, body, dismissible = true } = {}) {
  const overlay = document.getElementById('desktopBackendOverlay');
  if (!overlay) return;
  const titleNode = document.getElementById('desktopBackendOverlayTitle');
  const bodyNode = document.getElementById('desktopBackendOverlayBody');
  const dismissBtn = document.getElementById('desktopOverlayDismissBtn');
  if (titleNode) titleNode.textContent = title || 'Backend offline';
  if (bodyNode) bodyNode.textContent = body || 'Astrata desktop backend is offline.';
  if (dismissBtn) dismissBtn.hidden = !dismissible;
  overlay.hidden = false;
}

function hideDesktopOverlay() {
  const overlay = document.getElementById('desktopBackendOverlay');
  if (overlay) overlay.hidden = true;
}

function renderConnectorStatus(summary) {
  const container = document.getElementById('connectorStatus');
  if (!container) return;
  const relay = summary?.relay || {};
  const account = summary?.account_auth || {};
  const accessPolicy = account?.access_policy || {};
  const hostedEligibility = account?.hosted_bridge_eligibility || {};
  const selected = relay?.selected_profile || null;
  const urls = relay?.connector_urls || {};
  const pairing = APP.relayPairing;
  const connectorSetup = APP.connectorSetupResult;
  const relayQueue = relay?.queue_state || {};
  const queueCounts = relayQueue?.counts || {};
  const oauth = account?.oauth || {};
  const accountEmailInput = document.getElementById('accountEmailInput');
  const displayNameInput = document.getElementById('accountDisplayNameInput');
  const deviceLabelInput = document.getElementById('accountDeviceLabelInput');
  const inviteCodeInput = document.getElementById('accountInviteCodeInput');
  const relayEndpointInput = document.getElementById('relayEndpointInput');

  if (accountEmailInput && !accountEmailInput.value && account?.user?.email) accountEmailInput.value = account.user.email;
  if (displayNameInput && !displayNameInput.value && account?.user?.display_name) displayNameInput.value = account.user.display_name;
  if (deviceLabelInput && !deviceLabelInput.value && account?.device_label_suggestion) deviceLabelInput.value = account.device_label_suggestion;
  if (inviteCodeInput && APP.accountLinkResult?.invite?.code && !inviteCodeInput.value) inviteCodeInput.value = APP.accountLinkResult.invite.code;
  if (relayEndpointInput && !relayEndpointInput.value && account?.device_link?.relay_endpoint) relayEndpointInput.value = account.device_link.relay_endpoint;

  const blocks = [];

  const eligibilityStatus = String(hostedEligibility?.status || '').trim() || 'invite_required';
  const eligibilityTone = eligibilityStatus === 'active' || eligibilityStatus === 'eligible'
    ? 'success'
    : eligibilityStatus === 'disabled'
      ? 'danger'
      : 'warning';

  blocks.push(
    el('div', { className: 'runtime-card', style: { marginBottom: '12px' } },
      el('div', { className: 'runtime-status-row' },
        el('span', { style: { fontWeight: '700', fontSize: '14px' } }, 'Access State'),
        pill(eligibilityStatus.replaceAll('_', ' '), eligibilityTone),
      ),
      el('div', { style: { fontSize: '13px', color: 'var(--text-muted)', marginTop: '10px', lineHeight: '1.7' } },
        hostedEligibility?.reason
          || 'Astrata should explain which parts of setup are public and which parts are still gated.'),
      el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '12px' } },
        accessPolicy?.public_access?.download ? routePill('public', 'download', 'success') : null,
        accessPolicy?.public_access?.desktop_install ? routePill('public', 'install', 'success') : null,
        accessPolicy?.public_access?.local_onboarding ? routePill('public', 'onboarding', 'success') : null,
        accessPolicy?.invite_gated_access?.gpt_bridge_sign_in ? routePill('invite', 'bridge sign-in', 'warning') : null,
        accessPolicy?.invite_gated_access?.remote_queue_usage ? routePill('invite', 'remote queue', 'warning') : null,
      ),
      el('div', { style: { fontSize: '12px', color: 'var(--text-dim)', marginTop: '10px' } },
        accessPolicy?.policy_rule || 'Download/install is public; hosted bridge activation is gated.'),
    ),
  );

  if (!selected) {
    blocks.push(
      el('div', { className: 'empty-state' },
        'No hosted relay profile is registered yet. Once a ChatGPT relay profile exists locally, this panel can generate pairing codes for it.')
    );
    clearAndAppend(container, blocks);
    return;
  }

  blocks.push(
    el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '10px' } },
      pill(selected.exposure || 'generic', 'accent'),
      pill(selected.control_posture || 'unknown', 'neutral'),
      pill(selected.local_prime_behavior || 'unknown', 'neutral'),
      pill(`pending ${queueCounts.pending || relay.queue_depth || 0}`, (queueCounts.pending || relay.queue_depth) ? 'warning' : 'neutral'),
      pill(`tokens ${oauth?.counts?.active_tokens || 0}`, oauth?.counts?.active_tokens ? 'success' : 'neutral'),
    ),
    el('div', { style: { fontSize: '13px', color: 'var(--text-muted)', lineHeight: '1.7' } },
      `Astrata will pair ChatGPT against ${selected.label || selected.profile_id}. The desktop should be linked to an Astrata account first so new pairing codes carry user and device context.`),
  );

  blocks.push(
    el('div', { className: 'runtime-card', style: { marginTop: '12px' } },
      el('div', { className: 'runtime-status-row' },
        el('span', { style: { fontWeight: '700', fontSize: '14px' } }, 'Relay Queue'),
        pill((queueCounts.pending || 0) > 0 ? 'work waiting' : 'idle', (queueCounts.pending || 0) > 0 ? 'warning' : 'success'),
      ),
      el('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(4,minmax(0,1fr))', gap: '8px', marginTop: '12px' } },
        metricTile(queueCounts.pending || 0, 'Pending'),
        metricTile(queueCounts.acked || 0, 'Acked'),
        metricTile(queueCounts.results || 0, 'Results'),
        metricTile(queueCounts.sessions || 0, 'Sessions'),
      ),
      (relayQueue.pending || []).length
        ? el('div', { style: { marginTop: '12px' } },
            el('div', { className: 'task-item-title' }, 'Waiting for local desktop'),
            ...relayQueue.pending.slice(-3).reverse().map(req =>
              el('div', { className: 'transcript-item', style: { marginTop: '8px' } },
                el('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } },
                  pill(req.tool_name || 'tool', 'accent'),
                  pill(relativeTime(req.created_at), 'neutral'),
                ),
                el('div', { className: 'transcript-body' }, req?.arguments?.task || req.task_id || req.request_id),
              )
            ))
        : el('div', { style: { fontSize: '12px', color: 'var(--text-dim)', marginTop: '10px' } },
            'No connector work is waiting for this desktop.'),
    ),
  );

  if (account?.status === 'linked' || account?.status === 'partial') {
    blocks.push(
      el('div', { className: 'runtime-card', style: { marginTop: '12px' } },
        el('div', { className: 'runtime-status-row' },
          el('span', { style: { fontWeight: '700', fontSize: '14px' } }, 'Desktop Account Link'),
          pill(account.status === 'linked' ? 'linked' : 'partial', account.status === 'linked' ? 'success' : 'warning'),
        ),
        el('div', { style: { fontSize: '13px', color: 'var(--text-muted)', marginTop: '10px', lineHeight: '1.7' } },
          `${account?.user?.display_name || account?.user?.email || 'Astrata user'} is linked to ${account?.device?.label || account?.device_label_suggestion || 'this desktop'} for ${account?.profile?.label || selected.label || selected.profile_id}.`),
        el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '12px' } },
          account?.user?.email ? routePill('account', account.user.email, 'accent') : null,
          account?.device?.label ? routePill('device', account.device.label, 'neutral') : null,
          account?.profile?.profile_id ? routePill('profile', account.profile.profile_id.slice(0, 8), 'neutral') : null,
        ),
        el('div', { style: { fontSize: '12px', color: 'var(--text-dim)', marginTop: '10px' } },
          eligibilityStatus === 'eligible' || eligibilityStatus === 'active'
            ? 'This desktop is linked and the account is eligible for hosted bridge activation.'
            : 'This makes pairing codes device-aware, but hosted bridge activation still needs invite-backed account eligibility.'),
      ),
    );
  } else {
    blocks.push(
      el('div', { className: 'transcript-item', style: { marginTop: '12px' } },
        el('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } },
          pill('account link needed', 'warning'),
        ),
        el('div', { className: 'transcript-body' },
          'Link this desktop to the selected relay profile first. That gives the connector a real user, profile, and device binding instead of a floating pairing code.'),
      ),
    );
  }

  if (APP.accountLinkResult?.status === 'failed') {
    blocks.push(
      el('div', { className: 'transcript-item', style: { marginTop: '12px' } },
        el('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } },
          pill('link failed', 'danger'),
        ),
        el('div', { className: 'transcript-body' }, APP.accountLinkResult.reason || 'Desktop account link failed.'),
      ),
    );
  }

  if (APP.accountLinkResult?.status === 'ok' && APP.accountLinkResult?.invite) {
    blocks.push(
      el('div', { className: 'transcript-item', style: { marginTop: '12px' } },
        el('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } },
          pill('invite redeemed', 'success'),
        ),
        el('div', { className: 'transcript-body' },
          'Hosted bridge access for this account is now eligible. You can keep onboarding locally, then activate the bridge when ready.'),
      ),
    );
  }

  if (connectorSetup?.status === 'ok') {
    const authorizeUrl = connectorSetup.authorize_url || '';
    blocks.push(
      el('div', { className: 'runtime-card', style: { marginTop: '12px' } },
        el('div', { className: 'runtime-status-row' },
          el('span', { style: { fontWeight: '700', fontSize: '14px' } }, 'OAuth Connector Link'),
          pill('ready', 'success'),
        ),
        el('div', { style: { fontSize: '12px', color: 'var(--text-dim)', marginTop: '10px', lineHeight: '1.7' } },
          'Open this URL to authorize the connector against this Astrata account and paired desktop.'),
        authorizeUrl ? el('div', { style: { fontFamily: 'var(--font-mono)', fontSize: '12px', overflowWrap: 'anywhere', marginTop: '10px', color: 'var(--text-muted)' } }, authorizeUrl) : null,
        el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '12px' } },
          authorizeUrl ? el('a', { className: 'btn btn-secondary btn-sm', href: authorizeUrl, target: '_blank', rel: 'noreferrer' }, 'Open Authorize') : null,
          authorizeUrl ? el('button', {
            className: 'btn btn-ghost btn-sm',
            onClick: async () => {
              try { await navigator.clipboard.writeText(authorizeUrl); }
              catch (err) { console.error('Copy authorize URL failed:', err); }
            },
          }, 'Copy URL') : null,
        ),
      ),
    );
  }

  if (pairing?.status === 'ok') {
    const code = pairing?.pairing?.pairing_code || '—';
    const expires = pairing?.pairing?.expires_at ? `${formatTime(pairing.pairing.expires_at)} (${relativeTime(pairing.pairing.expires_at)})` : '—';
    blocks.push(
      el('div', { className: 'runtime-card', style: { marginTop: '12px' } },
        el('div', { className: 'runtime-status-row' },
          el('span', { style: { fontWeight: '700', fontSize: '14px' } }, 'Current Pairing Code'),
          pill('ready', 'success'),
        ),
        el('div', { style: { fontFamily: 'var(--font-mono)', fontSize: '22px', letterSpacing: '0.04em', marginTop: '12px' } }, code),
        el('div', { style: { fontSize: '12px', color: 'var(--text-dim)', marginTop: '8px' } }, `Expires ${expires}`),
        el('div', { style: { fontSize: '12px', color: 'var(--text-dim)', marginTop: '8px' } },
          pairing?.pairing?.user_id && pairing?.pairing?.device_id
            ? 'This code is bound to the linked Astrata user and desktop device.'
            : 'This code is not carrying account/device context yet.'),
        el('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '12px' } },
          el('button', {
            className: 'btn btn-secondary btn-sm',
            onClick: async () => {
              try {
                await navigator.clipboard.writeText(code);
              } catch (err) {
                console.error('Copy pairing code failed:', err);
              }
            },
          }, 'Copy Code'),
          urls.openapi ? el('a', { className: 'btn btn-ghost btn-sm', href: urls.openapi, target: '_blank', rel: 'noreferrer' }, 'Open Schema') : null,
          urls.privacy ? el('a', { className: 'btn btn-ghost btn-sm', href: urls.privacy, target: '_blank', rel: 'noreferrer' }, 'Privacy') : null,
        ),
      ),
    );
  } else if (pairing?.status === 'failed' || pairing?.status === 'unavailable') {
    blocks.push(
      el('div', { className: 'transcript-item', style: { marginTop: '12px' } },
        el('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } },
          pill(pairing.status, pairing.status === 'failed' ? 'danger' : 'warning'),
        ),
        el('div', { className: 'transcript-body' }, pairing.reason || pairing.detail || 'Pairing could not be created.'),
      ),
    );
  }

  if (connectorSetup?.status === 'failed' || connectorSetup?.status === 'missing_callback_url') {
    blocks.push(
      el('div', { className: 'transcript-item', style: { marginTop: '12px' } },
        el('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } },
          pill('oauth setup needed', 'warning'),
        ),
        el('div', { className: 'transcript-body' }, connectorSetup.reason || 'Paste the ChatGPT OAuth callback URL, then create an OAuth link.'),
      ),
    );
  }

  blocks.push(
    el('div', { className: 'transcript-item', style: { marginTop: '12px' } },
      el('div', { className: 'task-item-title' }, 'Next Step'),
      el('div', { style: { fontSize: '12px', color: 'var(--text-dim)', marginTop: '6px', lineHeight: '1.7' } },
        account?.status === 'linked' && (eligibilityStatus === 'eligible' || eligibilityStatus === 'active')
          ? 'This account can activate the hosted bridge. Next: reconnect the GPT with OAuth and use the pairing code as a device selector.'
          : account?.status === 'linked'
            ? 'This desktop is linked, but hosted bridge activation still needs an invite. Redeem one here when you have it.'
            : 'Link this desktop first. Local install and onboarding can continue without an invite; hosted bridge activation comes later.'),
    ),
  );

  if (urls.relay || urls.oauth_authorization_server) {
    blocks.push(
      el('div', { className: 'transcript-item', style: { marginTop: '12px' } },
        el('div', { className: 'task-item-title' }, 'Connector URLs'),
        urls.relay ? el('div', { style: { fontSize: '12px', color: 'var(--text-dim)', marginTop: '6px' } }, `Relay: ${urls.relay}`) : null,
        urls.openapi ? el('div', { style: { fontSize: '12px', color: 'var(--text-dim)', marginTop: '4px' } }, `OpenAPI: ${urls.openapi}`) : null,
        urls.oauth_authorization_server ? el('div', { style: { fontSize: '12px', color: 'var(--text-dim)', marginTop: '4px' } }, `OAuth metadata: ${urls.oauth_authorization_server}`) : null,
      ),
    );
  }

  clearAndAppend(container, blocks);
}

async function refreshDesktopBackendStatus() {
  if (!APP.desktopAvailable) return null;
  const status = await desktopInvoke('desktop_backend_status_command');
  APP.desktopBackendStatus = status;
  return status;
}

async function stopDesktopBackend() {
  if (!APP.desktopAvailable) return;
  const confirmed = window.confirm("Stop Astrata's desktop backend now? The app shell will stay open and you can resume it later.");
  if (!confirmed) return;
  const status = await desktopInvoke('desktop_stop_backend');
  APP.desktopBackendStatus = status;
  APP.desktopOverlayDismissed = false;
  setDesktopOverlay({
    title: 'Backend stopped',
    body: "Astrata's desktop backend was stopped deliberately. Resume it when you want the full app surface back.",
    dismissible: true,
  });
  renderModels(APP.summary || {});
}

async function resumeDesktopBackend() {
  if (!APP.desktopAvailable) return;
  const status = await desktopInvoke('desktop_resume_backend');
  APP.desktopBackendStatus = status;
  APP.desktopOverlayDismissed = false;
  hideDesktopOverlay();
  if (status?.backend_url) {
    window.location.replace(status.backend_url);
  }
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

  // ── Update channel ──
  const updateChannelData = summary?.update_channel || {};
  const selectedChannel = updateChannelData.selected || 'tester';
  const availableChannels = Array.isArray(updateChannelData.channels) ? updateChannelData.channels : [];

  const channelDescriptions = {
    edge:    'Every successful build — highest velocity, highest risk.',
    nightly: 'Latest promoted daily build for fast-follow testers.',
    tester:  'Friendly-tester channel — promoted builds before GA.',
    stable:  'General-availability release channel.',
  };
  const channelLabels = {
    edge:    'Edge',
    nightly: 'Nightly',
    tester:  'Tester',
    stable:  'Stable',
  };

  if (availableChannels.length) {
    sections.push(el('div', { className: 'panel', id: 'updateChannelPanel' },
      el('div', { className: 'panel-title' }, 'Update Channel'),
      el('div', { style: { fontSize: '12px', color: 'var(--text-dim)', marginBottom: '10px', lineHeight: '1.6' } },
        'Choose which release channel Astrata checks for updates. Edge and Nightly require an invite for automatic updates.'),
      el('div', { className: 'toggle-btn-row' },
        ...availableChannels.map(ch => {
          const isSelected = ch.channel_id === selectedChannel;
          const btn = el('button', {
            className: `toggle-btn${isSelected ? ' active' : ''}`,
            id: `channelBtn-${ch.channel_id}`,
          },
            el('div', { style: { fontWeight: '700', fontSize: '12px', marginBottom: '2px' } },
              channelLabels[ch.channel_id] || ch.channel_id),
            el('div', { style: { fontSize: '11px', color: 'var(--text-dim)' } },
              channelDescriptions[ch.channel_id] || ch.description || ch.cadence),
            ch.invite_required
              ? el('div', { style: { fontSize: '10px', color: 'var(--accent)', marginTop: '4px', fontWeight: '600' } }, 'invite-gated')
              : null,
          );
          btn.addEventListener('click', async () => {
            // Optimistic UI update
            document.querySelectorAll('#updateChannelPanel .toggle-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const indicator = document.getElementById('savingIndicator');
            if (indicator) indicator.hidden = false;
            try {
              await api('/api/settings', {
                method: 'POST',
                body: JSON.stringify({ update_channel: ch.channel_id }),
              });
              // Sync APP.generalSettings so any re-render picks up the new value
              if (APP.generalSettings) APP.generalSettings.update_channel = ch.channel_id;
            } catch (err) {
              console.error('Failed to save update channel:', err);
            } finally {
              if (indicator) indicator.hidden = true;
            }
          });
          return btn;
        }),
      ),
    ));
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
  if (APP.refreshInFlight) {
    return APP.refreshInFlight;
  }
  APP.refreshInFlight = (async () => {
  if (APP.desktopAvailable) {
    try {
      await refreshDesktopBackendStatus();
    } catch (err) {
      console.error('Desktop backend status failed:', err);
    }
    if (APP.desktopBackendStatus?.backend_deliberately_stopped && !APP.desktopBackendStatus?.backend_running) {
      if (!APP.desktopOverlayDismissed) {
        setDesktopOverlay({
          title: 'Backend stopped',
          body: "Astrata's desktop backend is intentionally offline. Resume it when you want the live local surface back.",
          dismissible: true,
        });
      }
      renderModels(APP.summary || {});
      return;
    }
  }

  try {
    const summary = await api('/api/summary');
    APP.summary   = summary;
    hideDesktopOverlay();

    // Render Agent Tabs
    const agentTabsContainer = document.getElementById('chatAgentTabs');
    if (agentTabsContainer) {
      agentTabsContainer.innerHTML = '';
      const agents = Object.keys(summary?.agents || { prime: {}, local: {} });
      if (!agents.includes('prime')) agents.unshift('prime');
      if (!agents.includes('local') && agents.indexOf('prime') === 0) agents.splice(1, 0, 'local');
      else if (!agents.includes('local')) agents.push('local');
      
      agents.forEach(agentId => {
        const agentDef = summary?.agents?.[agentId] || {};
        const label = agentId === 'prime' ? 'Prime' : (agentId === 'local' ? 'Local' : (agentDef.display_route?.label || humanLane(agentId)));
        
        const tab = el('div', {
          className: `agent-tab${APP.activeLane === agentId ? ' active' : ''}`,
          onClick: () => switchAgent(agentId),
          style: {
            padding: '8px 16px',
            cursor: 'pointer',
            borderBottom: APP.activeLane === agentId ? '2px solid var(--accent)' : '2px solid transparent',
            color: APP.activeLane === agentId ? 'var(--text)' : 'var(--text-dim)',
            fontWeight: APP.activeLane === agentId ? '600' : '400',
            whiteSpace: 'nowrap',
          }
        }, label);
        
        agentTabsContainer.appendChild(tab);
      });
    }

    // Chat rendering for active agent
    const lane = APP.activeLane || 'prime';
    const key = lane + '_conversation';
    const conv = summary?.communications?.[key] || [];
    renderChatMessages(document.getElementById('chatMessages'), conv, lane);

    // Unread badges (icon rail)
    let totalUnread = 0;
    Object.keys(summary?.communications || {}).forEach(k => {
       if (k.endsWith('_inbox')) totalUnread += summary.communications[k].length;
    });
    const railChatBadge = document.getElementById('railChatBadge');
    if (railChatBadge) {
       railChatBadge.hidden = totalUnread === 0;
    }

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
    if (APP.desktopAvailable) {
      try {
        await refreshDesktopBackendStatus();
      } catch (statusErr) {
        console.error('Desktop status retry failed:', statusErr);
      }
      if (!APP.desktopBackendStatus?.backend_running) {
        setDesktopOverlay({
          title: APP.desktopBackendStatus?.backend_deliberately_stopped ? 'Backend stopped' : 'Recovering backend',
          body: APP.desktopBackendStatus?.backend_deliberately_stopped
            ? "Astrata's desktop backend is intentionally offline."
            : "Astrata's desktop backend appears to be down. The shell is trying to recover it.",
          dismissible: Boolean(APP.desktopBackendStatus?.backend_deliberately_stopped),
        });
      }
    }
  } finally {
    APP.refreshInFlight = null;
  }
  })();
  return APP.refreshInFlight;
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

  // Chat composer
  const chatSendBtn = document.getElementById('chatSendBtn');
  const chatInput = document.getElementById('chatInput');
  if (chatSendBtn) chatSendBtn.addEventListener('click', () => sendMessage());
  if (chatInput) {
    chatInput.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
    chatInput.addEventListener('input', e => autoResizeTextarea(e.target));
  }

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
  document.getElementById('linkDesktopBtn').addEventListener('click', async () => {
    const btn = document.getElementById('linkDesktopBtn');
    const email = document.getElementById('accountEmailInput')?.value?.trim() || '';
    const displayName = document.getElementById('accountDisplayNameInput')?.value?.trim() || '';
    const deviceLabel = document.getElementById('accountDeviceLabelInput')?.value?.trim() || '';
    const relayEndpoint = document.getElementById('relayEndpointInput')?.value?.trim() || '';
    if (!email) {
      window.alert('Enter an Astrata account email first.');
      return;
    }
    btn.disabled = true; btn.textContent = 'Linking…';
    try {
      const response = await api('/api/account/device/link', {
        method: 'POST',
        body: JSON.stringify({
          email,
          display_name: displayName,
          label: deviceLabel || undefined,
          relay_endpoint: relayEndpoint,
        }),
      });
      APP.accountLinkResult = response?.detail || null;
      await refresh();
    } catch (err) {
      console.error('Desktop account link failed:', err);
      APP.accountLinkResult = { status: 'failed', reason: String(err?.message || err) };
      renderConnectorStatus(APP.summary || {});
    } finally {
      btn.disabled = false; btn.textContent = 'Link This Desktop';
    }
  });
  document.getElementById('redeemInviteBtn').addEventListener('click', async () => {
    const btn = document.getElementById('redeemInviteBtn');
    const email = document.getElementById('accountEmailInput')?.value?.trim() || '';
    const displayName = document.getElementById('accountDisplayNameInput')?.value?.trim() || '';
    const inviteCode = document.getElementById('accountInviteCodeInput')?.value?.trim() || '';
    if (!email) {
      window.alert('Enter an Astrata account email first.');
      return;
    }
    if (!inviteCode) {
      window.alert('Enter an invite code first.');
      return;
    }
    btn.disabled = true; btn.textContent = 'Redeeming…';
    try {
      const response = await api('/api/account/invite/redeem', {
        method: 'POST',
        body: JSON.stringify({
          email,
          display_name: displayName,
          invite_code: inviteCode,
        }),
      });
      APP.accountLinkResult = response?.detail || null;
      await refresh();
    } catch (err) {
      console.error('Invite redemption failed:', err);
      APP.accountLinkResult = { status: 'failed', reason: String(err?.message || err) };
      renderConnectorStatus(APP.summary || {});
    } finally {
      btn.disabled = false; btn.textContent = 'Redeem Invite';
    }
  });
  document.getElementById('generatePairingBtn').addEventListener('click', async () => {
    const btn = document.getElementById('generatePairingBtn');
    const email = document.getElementById('accountEmailInput')?.value?.trim() || '';
    const callbackUrl = document.getElementById('connectorCallbackInput')?.value?.trim() || '';
    const relayEndpoint = document.getElementById('relayEndpointInput')?.value?.trim() || '';
    if (!callbackUrl) {
      APP.connectorSetupResult = { status: 'missing_callback_url' };
      renderConnectorStatus(APP.summary || {});
      return;
    }
    btn.disabled = true; btn.textContent = 'Creating…';
    try {
      const response = await api('/api/connector/oauth/setup', {
        method: 'POST',
        body: JSON.stringify({
          label: 'ChatGPT Connector',
          email,
          callback_url: callbackUrl,
          relay_endpoint: relayEndpoint,
        }),
      });
      APP.connectorSetupResult = response?.detail || null;
      renderConnectorStatus(APP.summary || {});
    } catch (err) {
      console.error('OAuth setup failed:', err);
      APP.connectorSetupResult = { status: 'failed', reason: String(err?.message || err) };
      renderConnectorStatus(APP.summary || {});
    } finally {
      btn.disabled = false; btn.textContent = 'Create OAuth Link';
    }
  });

  const stopAppBackendBtn = document.getElementById('stopAppBackendBtn');
  const resumeAppBackendBtn = document.getElementById('resumeAppBackendBtn');
  const overlayResumeBtn = document.getElementById('desktopOverlayResumeBtn');
  const overlayDismissBtn = document.getElementById('desktopOverlayDismissBtn');

  if (stopAppBackendBtn) stopAppBackendBtn.addEventListener('click', async () => {
    try { await stopDesktopBackend(); }
    catch (err) { console.error('Desktop backend stop failed:', err); }
  });
  if (resumeAppBackendBtn) resumeAppBackendBtn.addEventListener('click', async () => {
    try { await resumeDesktopBackend(); }
    catch (err) { console.error('Desktop backend resume failed:', err); }
  });
  if (overlayResumeBtn) overlayResumeBtn.addEventListener('click', async () => {
    try { await resumeDesktopBackend(); }
    catch (err) { console.error('Desktop backend resume failed:', err); }
  });
  if (overlayDismissBtn) overlayDismissBtn.addEventListener('click', () => {
    APP.desktopOverlayDismissed = true;
    hideDesktopOverlay();
  });

  // Mode toggles
  setupModeToggle();

  // Context panel
  renderContextPanel('chat');

  // Initial load
  await refresh();
});

window.addEventListener('focus', () => { void refresh(); });
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    void refresh();
  }
});

window.__astrataDesktopHandleCloseRequest = async function () {
  if (!APP.desktopAvailable) {
    window.close();
    return;
  }
  const stopBackend = window.confirm(
    "Stop Astrata's backend too?\n\nPress OK to stop the backend and close the app. Press Cancel to keep the backend running and just close the app."
  );
  try {
    await desktopInvoke('desktop_handle_close_decision', { stopBackend });
  } catch (err) {
    console.error('Desktop close handling failed:', err);
  }
};

window.__astrataDesktopHandleBackendRecovered = function (backendUrl) {
  const now = Date.now();
  if (now - APP.lastBackendRecoveryAt < 30000) {
    return;
  }
  APP.lastBackendRecoveryAt = now;
  APP.desktopOverlayDismissed = false;
  hideDesktopOverlay();
  setTimeout(() => {
    window.location.replace(backendUrl || 'http://127.0.0.1:8891/');
  }, 250);
};
