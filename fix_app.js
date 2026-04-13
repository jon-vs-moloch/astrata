const fs = require('fs');
let code = fs.readFileSync('astrata/ui/static/app.js', 'utf8');

// 1. APP state changes
code = code.replace("APP.currentView = 'chat-prime'", "APP.currentView = 'chat'");
code = code.replace("currentView: 'chat-prime',", "currentView: 'chat',\n  activeLane: 'prime', // currently selected agent tab");
code = code.replace("pendingResponse: { prime: false, local: false },", "pendingResponse: {},");
code = code.replace("lastSentAt:      { prime: null,  local: null },", "lastSentAt: {},");
code = code.replace("composerMode:    { prime: 'agent', local: 'agent' },", "composerMode: {},");
code = code.replace("sessions:      { prime: loadSessions('prime'), local: loadSessions('local') },", "sessions: {},");
code = code.replace("activeSession: { prime: getActiveSessionId('prime'), local: getActiveSessionId('local') },", "activeSession: {},");

// Polyfill dynamic keys via a getter or just initialize them
code = code.replace(
  "// Settings (cached from last fetch)",
  `// Settings\n  getSessions(lane) {\n    if (!this.sessions[lane]) this.sessions[lane] = loadSessions(lane);\n    return this.sessions[lane];\n  },\n  getActiveSession(lane) {\n    if (!this.activeSession[lane]) this.activeSession[lane] = getActiveSessionId(lane);\n    return this.activeSession[lane];\n  },\n  getComposerMode(lane) {\n    if (!this.composerMode[lane]) this.composerMode[lane] = 'agent';\n    return this.composerMode[lane];\n  },`
);

fs.writeFileSync('astrata/ui/static/app.js', code);
