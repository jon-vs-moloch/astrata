const MEMORY_STATE = {
  advertisements: new Map(),
  pendingRequests: new Map(),
  ackedRequests: new Map(),
  results: new Map(),
  sessions: new Map(),
  adapterReceipts: new Map(),
  feedback: new Map(),
  pairingCodes: new Map(),
  oauthClients: new Map(),
  oauthCodes: new Map(),
  oauthTokens: new Map(),
  publicLoginCodes: new Map(),
};

const DEFAULT_TOOLS = [
  "search",
  "fetch",
  "submit_task",
  "submit_feedback",
  "get_task_status",
  "list_capabilities",
  "message_prime",
];

const SIGNED_IN_ACCOUNT_TOOLS = [
  "account_status",
  "device_status",
  "install_local_node",
  "submit_feedback",
];

const PAIRED_LOCAL_NODE_TOOLS = [
  "list_tools",
  "tool_search",
  "message_prime",
  "get_session",
  "get_result",
  "get_task_status",
  "list_capabilities",
  "search_files",
  "read_file",
  "propose_patch",
];

const ELEVATED_SESSION_TOOLS = [
  "apply_patch",
  "run_tests",
  "request_elevation",
];

const REMOTE_HOST_BASH_TOOLS = [
  "run_command",
];

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }

    if (request.method === "GET" && url.pathname === "/health") {
      return json({
        ok: true,
        service: "astrata-hosted-mcp-relay",
        environment: env.ENVIRONMENT || "development",
      });
    }

    if (request.method === "GET" && (url.pathname === "/openapi.json" || url.pathname === "/gpt/openapi.json" || url.pathname === "/gpt/account/openapi.json")) {
      return json(gptActionsOpenApi(url, env));
    }

    if (request.method === "GET" && url.pathname === "/gpt/public/openapi.json") {
      return json(gptPublicActionsOpenApi(url, env));
    }

    if (request.method === "GET" && (url.pathname === "/privacy" || url.pathname === "/privacy-policy")) {
      return privacyPolicyPage(url);
    }

    if (request.method === "GET" && url.pathname === "/gpt/help") {
      return json(gptHelp(url, env));
    }

    if (request.method === "GET" && url.pathname === "/gpt/about") {
      return json(gptAbout(url, env));
    }

    if (request.method === "GET" && url.pathname === "/gpt/public/about") {
      return json(gptPublicAbout(url, env));
    }

    if (request.method === "POST" && url.pathname === "/gpt/public/tool_search") {
      const payload = await request.json();
      return json(handleGptPublicToolSearch(payload, url, env));
    }

    if (request.method === "POST" && url.pathname === "/gpt/public/sign_in") {
      const payload = await request.json();
      return json(await handlePublicSignInStart(url, env, payload));
    }

    if (request.method === "POST" && url.pathname === "/gpt/public/complete_sign_in") {
      const payload = await request.json();
      return json(await handlePublicSignInComplete(payload, env, url));
    }

    if (request.method === "POST" && url.pathname === "/gpt/public/use_tool") {
      const payload = await request.json();
      return json(await handleGptPublicUseTool(payload, env, url));
    }

    if (request.method === "GET" && url.pathname === "/gpt/public/signin") {
      return publicSignInPage(url);
    }

    if (request.method === "POST" && url.pathname === "/gpt/public/signin") {
      return handlePublicSignIn(await request.formData(), env, url);
    }

    if (request.method === "POST" && url.pathname === "/gpt/submit_feedback") {
      const auth = await resolveAdapterAuth(request, env);
      if (!auth.authorized) return oauthChallenge(url);
      const payload = await request.json();
      return json(await handleGptSubmitFeedbackAction(payload, env, url, auth));
    }

    if (request.method === "GET" && url.pathname === "/gpt/list_tools") {
      const auth = await resolveAdapterAuth(request, env);
      if (!auth.authorized) return oauthChallenge(url);
      return json(await handleGptListTools(env, url, auth));
    }

    if (request.method === "POST" && url.pathname === "/gpt/tool_search") {
      const auth = await resolveAdapterAuth(request, env);
      if (!auth.authorized) return oauthChallenge(url);
      const payload = await request.json();
      return json(await handleGptToolSearch(payload, env, url, auth));
    }

    if (request.method === "POST" && url.pathname === "/gpt/use_tool") {
      const auth = await resolveAdapterAuth(request, env);
      if (!auth.authorized) return oauthChallenge(url);
      const payload = await request.json();
      return json(await handleGptUseTool(payload, env, url, auth));
    }

    if (request.method === "GET" && url.pathname === "/.well-known/oauth-protected-resource") {
      return json(oauthProtectedResourceMetadata(url));
    }

    if (request.method === "GET" && url.pathname === "/.well-known/oauth-authorization-server") {
      return json(oauthAuthorizationServerMetadata(url));
    }

    if (request.method === "POST" && url.pathname === "/oauth/register") {
      const payload = await request.json();
      const response = await handleOAuthRegister(payload, env);
      return response instanceof Response ? response : json(response);
    }

    if (request.method === "GET" && url.pathname === "/oauth/authorize") {
      return oauthAuthorizationPage(url);
    }

    if (request.method === "POST" && url.pathname === "/oauth/authorize") {
      return handleOAuthAuthorize(await request.formData(), env);
    }

    if (request.method === "POST" && url.pathname === "/oauth/token") {
      return handleOAuthToken(await request.formData(), env, url);
    }

    if (request.method === "POST" && (url.pathname === "/mcp" || url.pathname === "/adapters/chatgpt/mcp")) {
      const auth = await resolveAdapterAuth(request, env);
      if (!auth.authorized) return oauthChallenge(url);
      const payload = await request.json();
      return handleMcpAdapter(payload, env, { adapter: url.pathname === "/mcp" ? "default" : "chatgpt", url, auth });
    }

    if (request.method === "POST" && url.pathname === "/relay/mcp") {
      if (controlPlaneBaseUrl(env)) {
        return proxyControlPlaneRequest(request, env, "/relay/mcp");
      }
      if (!isAuthorized(request, env)) return json({ error: "unauthorized" }, 401);
      const payload = await request.json();
      return handleRelayMcp(payload, env);
    }

    if (request.method === "POST" && url.pathname === "/relay/local/heartbeat") {
      if (controlPlaneBaseUrl(env)) {
        return proxyControlPlaneRequest(request, env, "/relay/local/heartbeat");
      }
      if (!isAuthorized(request, env)) return json({ error: "unauthorized" }, 401);
      const payload = await request.json();
      return handleLocalHeartbeat(payload, env);
    }

    if (request.method === "POST" && url.pathname === "/relay/local/ack") {
      if (controlPlaneBaseUrl(env)) {
        return proxyControlPlaneRequest(request, env, "/relay/local/ack");
      }
      if (!isAuthorized(request, env)) return json({ error: "unauthorized" }, 401);
      const payload = await request.json();
      return handleLocalAck(payload, env);
    }

    if (request.method === "POST" && url.pathname === "/relay/local/result") {
      if (controlPlaneBaseUrl(env)) {
        return proxyControlPlaneRequest(request, env, "/relay/local/result");
      }
      if (!isAuthorized(request, env)) return json({ error: "unauthorized" }, 401);
      const payload = await request.json();
      return handleLocalResult(payload, env);
    }

    if (request.method === "POST" && url.pathname === "/relay/pairing/create") {
      if (!isAuthorized(request, env)) return json({ error: "unauthorized" }, 401);
      const payload = await request.json();
      return json(await handlePairingCreate(payload, env));
    }

    if (request.method === "GET" && url.pathname === "/relay/debug/state") {
      if (!isAuthorized(request, env)) return json({ error: "unauthorized" }, 401);
      return json(await loadDebugState(env));
    }

    if (request.method === "GET" && url.pathname.startsWith("/relay/result/")) {
      if (controlPlaneBaseUrl(env)) {
        const requestId = decodeURIComponent(url.pathname.slice("/relay/result/".length));
        return proxyControlPlaneRequest(request, env, `/relay/result/${encodeURIComponent(requestId)}`, { query: url.search });
      }
      if (!isAuthorized(request, env)) return json({ error: "unauthorized" }, 401);
      const requestId = decodeURIComponent(url.pathname.slice("/relay/result/".length));
      return json(await lookupResult(env, requestId));
    }

    if (request.method === "POST" && url.pathname === "/relay/session/message") {
      if (controlPlaneBaseUrl(env)) {
        return proxyControlPlaneRequest(request, env, "/relay/session/message");
      }
      if (!isAuthorized(request, env)) return json({ error: "unauthorized" }, 401);
      const payload = await request.json();
      return json(await appendSessionMessage(env, payload, "remote"));
    }

    if (request.method === "GET" && url.pathname.startsWith("/relay/session/")) {
      if (controlPlaneBaseUrl(env)) {
        const sessionId = decodeURIComponent(url.pathname.slice("/relay/session/".length));
        return proxyControlPlaneRequest(request, env, `/relay/session/${encodeURIComponent(sessionId)}`, { query: url.search });
      }
      if (!isAuthorized(request, env)) return json({ error: "unauthorized" }, 401);
      const sessionId = decodeURIComponent(url.pathname.slice("/relay/session/".length));
      const profileId = url.searchParams.get("profile_id") || "";
      const actor = url.searchParams.get("actor") || "remote";
      return json(await readSession(env, { profileId, sessionId, actor }));
    }

    return json({ error: "not_found" }, 404);
  },
};

function gptActionsOpenApi(url, env) {
  const baseUrl = url.origin;
  return {
    openapi: "3.1.0",
    info: {
      title: "Astrata GPT Actions Bridge",
      version: "0.1.0",
      description:
        "Stable Custom GPT action surface for Astrata. OAuth-bound connector access is resolved against Astrata Web and the paired profile/device control plane.",
    },
    servers: [{ url: baseUrl }],
    paths: {
      "/gpt/help": {
        get: {
          operationId: "help",
          summary: "Explain how to use the Astrata GPT Actions bridge.",
          responses: jsonResponseSchema({ $ref: "#/components/schemas/HelpResponse" }),
        },
      },
      "/gpt/about": {
        get: {
          operationId: "about",
          summary: "Describe Astrata and the current bridge posture.",
          responses: jsonResponseSchema({ $ref: "#/components/schemas/AboutResponse" }),
        },
      },
      "/gpt/submit_feedback": {
        post: {
          operationId: "submit_feedback",
          summary: "Submit product feedback about Astrata or the GPT bridge.",
          security: [{ bearerAuth: [] }],
          requestBody: jsonRequestSchema({
            type: "object",
            required: ["message"],
            properties: {
              message: { type: "string", description: "Feedback text." },
              category: { type: "string", description: "Optional feedback category, such as ux, bug, missing_tool, or docs." },
              severity: { type: "string", description: "Optional severity, such as low, normal, high, or urgent." },
              context: {
                type: "object",
                description: "Optional structured context.",
                additionalProperties: true,
              },
            },
          }),
          responses: jsonResponseSchema({ $ref: "#/components/schemas/FeedbackResponse" }),
        },
      },
      "/gpt/tool_search": {
        post: {
          operationId: "tool_search",
          summary: "Search the currently available Astrata tools for this remote operator.",
          security: [{ bearerAuth: [] }],
          requestBody: jsonRequestSchema({
            type: "object",
            properties: {
              query: { type: "string", description: "Optional search text, such as task, memory, browser, status, or prime." },
              profile_id: { type: "string", description: "Optional relay profile override for private-dev token flows. OAuth-bound GPT sessions use their paired profile." },
            },
          }),
          responses: jsonResponseSchema({ $ref: "#/components/schemas/ToolSearchResponse" }),
        },
      },
      "/gpt/list_tools": {
        get: {
          operationId: "list_tools",
          summary: "List the basic currently available Astrata tool shortlist for this remote operator.",
          security: [{ bearerAuth: [] }],
          responses: jsonResponseSchema({ $ref: "#/components/schemas/ToolSearchResponse" }),
        },
      },
      "/gpt/use_tool": {
        post: {
          operationId: "use_tool",
          summary: "Invoke an Astrata tool by name with arbitrary JSON arguments.",
          security: [{ bearerAuth: [] }],
          requestBody: jsonRequestSchema({
            type: "object",
            required: ["tool"],
            properties: {
              tool: {
                type: "string",
                description: "Tool name returned by tool_search, for example message_prime, get_result, or get_session.",
              },
              args: {
                type: "object",
                description: "Tool-specific JSON arguments.",
                additionalProperties: true,
              },
              profile_id: { type: "string", description: "Optional relay profile override for private-dev token flows. OAuth-bound GPT sessions use their paired profile." },
              session_id: { type: "string", description: "Optional dialogue session id for back-and-forth work." },
              idempotency_key: { type: "string", description: "Optional caller-supplied key to dedupe retries." },
            },
          }),
          responses: jsonResponseSchema({ $ref: "#/components/schemas/UseToolResponse" }),
        },
      },
    },
    components: {
      securitySchemes: {
        bearerAuth: {
          type: "http",
          scheme: "bearer",
          description:
            "Development bridge accepts either the private relay token or an OAuth access token bound to a paired relay profile. Production OAuth should replace the shared-token fallback before broader distribution.",
        },
      },
      schemas: {
        HelpResponse: {
          type: "object",
          properties: {
            name: { type: "string" },
            summary: { type: "string" },
            workflow: { type: "array", items: { type: "string" } },
            stable_actions: { type: "array", items: { type: "string" } },
          },
        },
        AboutResponse: {
          type: "object",
          properties: {
            name: { type: "string" },
            kind: { type: "string" },
            summary: { type: "string" },
            posture: { type: "string" },
            privacy_policy_url: { type: "string" },
          },
        },
        ToolSearchResponse: {
          type: "object",
          properties: {
            status: { type: "string" },
            profile_id: { type: "string" },
            tools: { type: "array", items: { $ref: "#/components/schemas/ToolSummary" } },
          },
        },
        ToolSummary: {
          type: "object",
          properties: {
            name: { type: "string" },
            description: { type: "string" },
            requires_polling: { type: "boolean" },
          },
        },
        UseToolResponse: {
          type: "object",
          properties: {
            status: { type: "string" },
            request_id: { type: "string" },
            session_id: { type: "string" },
            message: { type: "string" },
            result: { type: "object", additionalProperties: true },
            queued: { type: "object", additionalProperties: true },
          },
          additionalProperties: true,
        },
        FeedbackResponse: {
          type: "object",
          properties: {
            status: { type: "string" },
            feedback_id: { type: "string" },
            message: { type: "string" },
          },
        },
      },
    },
  };
}

function gptPublicActionsOpenApi(url, env) {
  const baseUrl = url.origin;
  return {
    openapi: "3.1.0",
    info: {
      title: "Astrata Public GPT Actions",
      version: "0.1.0",
      description:
        "No-auth Astrata surface for orientation, onboarding, installation guidance, and account-upgrade prompts. Personalized and local-node tools live in the OAuth action.",
    },
    servers: [{ url: baseUrl }],
    paths: {
      "/gpt/public/about": {
        get: {
          operationId: "public_about",
          summary: "Describe Astrata and explain what is available before sign-in.",
          responses: jsonResponseSchema({ $ref: "#/components/schemas/PublicAboutResponse" }),
        },
      },
      "/gpt/public/tool_search": {
        post: {
          operationId: "public_tool_search",
          summary: "Search the public, no-auth Astrata tools.",
          requestBody: jsonRequestSchema({
            type: "object",
            properties: {
              query: { type: "string", description: "Optional search text, such as sign in, install, onboarding, feedback, local node, or capabilities." },
            },
          }),
          responses: jsonResponseSchema({ $ref: "#/components/schemas/PublicToolSearchResponse" }),
        },
      },
      "/gpt/public/sign_in": {
        post: {
          operationId: "public_sign_in",
          summary: "Start the public Astrata browser sign-in flow.",
          requestBody: jsonRequestSchema({
            type: "object",
            properties: {},
          }),
          responses: jsonResponseSchema({ $ref: "#/components/schemas/PublicUseToolResponse" }),
        },
      },
      "/gpt/public/complete_sign_in": {
        post: {
          operationId: "public_complete_sign_in",
          summary: "Complete the public Astrata browser sign-in flow.",
          requestBody: jsonRequestSchema({
            type: "object",
            properties: {
              request_id: {
                type: "string",
                description: "Login request id returned by public_sign_in.",
              },
              login_request_id: {
                type: "string",
                description: "Alias for request_id.",
              },
              code: {
                type: "string",
                description: "Fallback ASTRATA-LOGIN completion code from the browser page.",
              },
              completion_code: {
                type: "string",
                description: "Alias for code.",
              },
            },
          }),
          responses: jsonResponseSchema({ $ref: "#/components/schemas/PublicUseToolResponse" }),
        },
      },
      "/gpt/public/use_tool": {
        post: {
          operationId: "public_use_tool",
          summary: "Invoke a public Astrata tool that does not require sign-in.",
          requestBody: jsonRequestSchema({
            type: "object",
            required: ["tool"],
            properties: {
              tool: {
                type: "string",
                description: "Public tool name returned by public_tool_search, such as onboarding, sign_in, complete_sign_in, account_status, account_tool_search, account_use_tool, install_local_node, capability_map, or submit_feedback.",
              },
              args: {
                type: "object",
                description: "Tool-specific JSON arguments.",
                additionalProperties: true,
              },
              code: {
                type: "string",
                description: "Fallback completion code for complete_sign_in. Prefer request_id when sign_in returned one.",
              },
              request_id: {
                type: "string",
                description: "Login request id returned by sign_in. Pass this to complete_sign_in after the user says they finished browser sign-in.",
              },
              session_token: {
                type: "string",
                description: "Session token returned by complete_sign_in for account_status, account_tool_search, and account_use_tool.",
              },
              request: {
                type: "object",
                description: "Nested account tool request for account_use_tool.",
                additionalProperties: true,
              },
            },
          }),
          responses: jsonResponseSchema({ $ref: "#/components/schemas/PublicUseToolResponse" }),
        },
      },
    },
    components: {
      schemas: {
        PublicAboutResponse: {
          type: "object",
          properties: {
            name: { type: "string" },
            summary: { type: "string" },
            signed_out_capabilities: { type: "array", items: { type: "string" } },
            signed_in_capabilities: { type: "array", items: { type: "string" } },
            local_node_capabilities: { type: "array", items: { type: "string" } },
          },
          additionalProperties: true,
        },
        PublicToolSearchResponse: {
          type: "object",
          properties: {
            status: { type: "string" },
            tools: { type: "array", items: { $ref: "#/components/schemas/PublicToolSummary" } },
            count: { type: "integer" },
          },
          additionalProperties: true,
        },
        PublicToolSummary: {
          type: "object",
          properties: {
            name: { type: "string" },
            description: { type: "string" },
            requires_sign_in: { type: "boolean" },
            requires_local_node: { type: "boolean" },
          },
          additionalProperties: true,
        },
        PublicUseToolResponse: {
          type: "object",
          properties: {
            status: { type: "string" },
            result: { type: "object", additionalProperties: true },
            message: { type: "string" },
          },
          additionalProperties: true,
        },
      },
    },
  };
}

function jsonRequestSchema(schema) {
  return {
    required: true,
    content: {
      "application/json": {
        schema,
      },
    },
  };
}

function jsonResponseSchema(schema) {
  return {
    "200": {
      description: "JSON response",
      content: {
        "application/json": {
          schema,
        },
      },
    },
  };
}

function gptHelp(url, env) {
  return {
    name: "Astrata GPT Actions Bridge",
    summary:
      "Use about() for orientation, list_tools() for the current shortlist, tool_search() to discover permitted Astrata tools including onboarding, and use_tool(tool, args) to call one. Long-running requests are acknowledged immediately; poll get_result or get_session through use_tool for follow-up.",
    stable_actions: ["help", "about", "submit_feedback", "list_tools", "tool_search", "use_tool"],
    workflow: [
      "Call about to understand Astrata's role and security posture.",
      "Call list_tools for the basic shortlist, or tool_search with a short query to discover allowed tools.",
      "Call use_tool(tool='onboarding', args={}) on first load or when instructions seem stale.",
      "Call use_tool with the chosen tool name and JSON args.",
      "If the response is queued, call use_tool with tool=get_session or tool=get_result to check progress.",
    ],
    privacy_policy_url: `${url.origin}/privacy`,
    default_profile_id: defaultProfileId(env, url),
  };
}

function gptOnboarding(url, env) {
  return {
    role: "You are a Custom GPT acting as a remote operator bridge for Astrata.",
    what_astrata_is:
      "Astrata is a local-first AI operating system and agent constellation. It is not intended to be just a chatbot; the product direction is that the user's computer becomes agentically alive, secure, reachable, and able to make things happen across local tools, durable agents, memory, and remote clients.",
    development_status:
      "This bridge is a development path toward v0. It is useful for private testing, but not ready for broad distribution until Astrata Web provides real account auth, per-user relay routing, durable queues, and production OAuth.",
    how_this_gpt_relates:
      "The Custom GPT is a remote operator/client. It talks to Astrata through a small stable Actions schema, then discovers live tools through tool_search and calls them through use_tool. It should not assume it is Prime, local Astrata, or the user's whole system unless the authenticated posture explicitly says so.",
    first_message_guidance:
      "Do not infodump this onboarding text to the user. If the user seems new, give a one- or two-sentence overview of Astrata and offer to help. If the user already knows what is happening, proceed directly with their request.",
    access_boundary:
      "Treat public onboarding and install help as open. Escalate to sign-in only when the user asks for hosted account, device, relay, or local-node work. Creating a new hosted bridge account may require an invite code.",
    access_policy: gptAccessPolicy(url),
    hosted_bridge_eligibility: publicHostedBridgeEligibility(),
    onboarding_state: {
      current_behavior:
        "No durable per-user onboarded flag exists in the development relay yet. Treat onboarding as current guidance for yourself, not as something to repeat at the user.",
      future_behavior:
        "Once Astrata Web owns accounts and routing, record a user-level GPT onboarding flag such as gpt_onboarded_at and support richer curricula by user, role, and disclosure tier.",
    },
    boot_sequence: [
      "Call about() if you do not already know the active Astrata posture.",
      "Call list_tools() to see the basic tool shortlist.",
      "Use tool_search(query) when the user asks for a capability that is not in the shortlist.",
      "Call use_tool(tool, args) with the selected tool and JSON arguments.",
      "For queued work, preserve request_id and session_id and poll with get_session or get_result through use_tool.",
    ],
    operating_rules: [
      "Do not invent Astrata tools; discover tools from list_tools or tool_search.",
      "Prefer connector-safe summaries and status over raw local data.",
      "Use submit_feedback when the user reports confusing behavior, bugs, missing tools, product friction, or when you notice actionable bridge feedback.",
      "If Astrata reports that local Prime/local desktop is unavailable, explain the degraded state and offer to queue the request.",
      "Treat this bridge as development-only until Astrata Web has real account auth and per-user routing.",
    ],
    polling_guidance:
      "Most write-like calls return fast acknowledgements. Tell the user the request id and session id, then poll periodically with use_tool(tool='get_session', args={session_id}) or use_tool(tool='get_result', args={request_id}).",
    security_guidance:
      "Do not request local-only, enclave-only, secret, or PII data unless the user explicitly authorizes a secure disclosure workflow. Never assume this hosted relay can see private local state.",
    default_profile_id: defaultProfileId(env, url),
    privacy_policy_url: `${url.origin}/privacy`,
  };
}

function gptAbout(url, env) {
  return {
    name: "Astrata",
    kind: "local-first AI operating system and agent constellation",
    summary:
      "Astrata is intended to be the user's computer becoming agentically alive: local-first, security-conscious, always reachable, and capable of coordinating durable agents, tools, memory, and remote operators.",
    posture:
      "This Custom GPT action adapter is a remote bridge. Connector-safe data may pass through the hosted relay. Local-only or enclave-only data should remain on the user's machine unless deliberately transformed and approved.",
    bridge:
      "The GPT Actions schema is intentionally tiny. Astrata controls the live tool catalog and permissions behind tool_search and use_tool so the GPT definition does not need frequent schema edits.",
    access_boundary:
      "Astrata keeps public download/install and local-first onboarding open. Hosted bridge activation and remote queue usage are the gated cloud boundary.",
    access_policy: gptAccessPolicy(url),
    hosted_bridge_eligibility: publicHostedBridgeEligibility(),
    default_profile_id: defaultProfileId(env, url),
    privacy_policy_url: `${url.origin}/privacy`,
  };
}

function gptAccessPolicy(url) {
  return {
    public_access: {
      download: true,
      desktop_install: true,
      local_onboarding: true,
      local_runtime_bootstrap: true,
      local_model_downloads: true,
    },
    invite_gated_access: {
      hosted_account_activation: true,
      gpt_bridge_sign_in: true,
      relay_profile_activation: true,
      remote_queue_usage: true,
      hosted_control_plane_features: true,
    },
    billing_boundary: "cloud_access_layer",
    policy_rule: "download/install is public; hosted bridge activation is invite-gated until monetization exists",
    privacy_policy_url: `${url.origin}/privacy`,
  };
}

function publicHostedBridgeEligibility() {
  return {
    status: "invite_required",
    reason:
      "Public onboarding and download/install are available without sign-in. Hosted bridge activation requires an existing Astrata account or an invite-backed new account.",
    invite_required: true,
  };
}

function activeHostedBridgeEligibility(context = null) {
  const pairedLocalNode = Boolean(context?.paired_local_node);
  return {
    status: pairedLocalNode ? "active" : "eligible",
    reason: pairedLocalNode
      ? "This account is signed in and has a paired local node, so hosted bridge workflows may route to the user's desktop."
      : "This account is signed in and may use hosted bridge features. Pairing a local node unlocks higher-trust local workflows.",
    invite_required: false,
  };
}

function publicToolDefinitions(url) {
  return [
    {
      name: "about",
      description: "Explain what Astrata is and what this GPT can do before sign-in.",
      requires_sign_in: false,
      requires_local_node: false,
    },
    {
      name: "onboarding",
      description: "Get concise guidance for introducing Astrata to a new user.",
      requires_sign_in: false,
      requires_local_node: false,
    },
    {
      name: "capability_map",
      description: "Show the capability ladder from public GPT use to signed-in account to paired local node.",
      requires_sign_in: false,
      requires_local_node: false,
    },
    {
      name: "sign_in",
      description: "Start a browser sign-in flow. Existing accounts can sign in directly; creating a new hosted bridge account may require an invite code.",
      requires_sign_in: false,
      requires_local_node: false,
    },
    {
      name: "complete_sign_in",
      description: "Exchange a short sign-in completion code for a scoped Astrata session token.",
      requires_sign_in: false,
      requires_local_node: false,
    },
    {
      name: "account_status",
      description: "Check the signed-in Astrata account/session represented by a session token.",
      requires_sign_in: true,
      requires_local_node: false,
    },
    {
      name: "account_tool_search",
      description: "Search account-bound Astrata tools after tool-level sign-in.",
      requires_sign_in: true,
      requires_local_node: false,
    },
    {
      name: "account_use_tool",
      description: "Invoke an account-bound Astrata tool after tool-level sign-in.",
      requires_sign_in: true,
      requires_local_node: false,
    },
    {
      name: "install_local_node",
      description: "Explain why and how to install/pair an Astrata local node. Download/install guidance remains public even when hosted bridge activation is invite-gated.",
      requires_sign_in: false,
      requires_local_node: false,
    },
    {
      name: "submit_feedback",
      description: "Send product feedback without requiring an Astrata account.",
      requires_sign_in: false,
      requires_local_node: false,
    },
  ].map((tool) => ({ ...tool, privacy_policy_url: `${url.origin}/privacy` }));
}

function gptPublicAbout(url, env) {
  return {
    name: "Astrata",
    summary:
      "Astrata is a local-first AI operating system and agent constellation. This public GPT surface can orient new users, explain the capability ladder, collect feedback, and help them decide when to sign in or install a local node.",
    posture: "signed_out_public",
    signed_out_capabilities: [
      "Understand what Astrata is.",
      "Explore the capability ladder.",
      "Ask onboarding and product-fit questions.",
      "Submit feedback.",
      "Get sign-in and local-node setup guidance.",
    ],
    signed_in_capabilities: [
      "Use account-bound Astrata tools.",
      "See user-owned relay/device status.",
      "Route requests to the user's Astrata profile.",
    ],
    local_node_capabilities: [
      "Pair a desktop client.",
      "Reach local Prime through the relay.",
      "Unlock higher-trust local workflows and durable task routing.",
    ],
    access_boundary:
      "Download/install and local-first onboarding stay public. Hosted bridge activation is the cloud-cost boundary and may require an invite for new tester accounts.",
    access_policy: gptAccessPolicy(url),
    hosted_bridge_eligibility: publicHostedBridgeEligibility(),
    upgrade_guidance:
      "Use the OAuth action only when the user asks for personalized account/device work. Use local-node pairing only when the user wants their own desktop Astrata involved.",
    public_tools: publicToolDefinitions(url),
    account_action_schema_url: `${url.origin}/gpt/account/openapi.json`,
    public_action_schema_url: `${url.origin}/gpt/public/openapi.json`,
    privacy_policy_url: `${url.origin}/privacy`,
    default_profile_id: defaultProfileId(env, url),
  };
}

function handleGptPublicToolSearch(payload, url, env) {
  const query = String(payload?.query || "").trim().toLowerCase();
  const tools = publicToolDefinitions(url);
  const filtered = query
    ? tools.filter((tool) => `${tool.name} ${tool.description}`.toLowerCase().includes(query))
    : tools;
  return {
    status: "ok",
    posture: "signed_out_public",
    access_policy: gptAccessPolicy(url),
    hosted_bridge_eligibility: publicHostedBridgeEligibility(),
    tools: filtered,
    count: filtered.length,
    total_available: tools.length,
    account_action_schema_url: `${url.origin}/gpt/account/openapi.json`,
  };
}

async function handleGptPublicUseTool(payload, env, url) {
  const toolName = String(payload?.tool || payload?.name || "").trim();
  const args = payload?.args && typeof payload.args === "object"
    ? payload.args
    : Object.fromEntries(Object.entries(payload || {}).filter(([key]) => !["tool", "name"].includes(key)));
  if (!toolName) return { status: "failed", error: "tool_required" };
  if (toolName === "help" || toolName === "about") return { status: "ok", result: gptPublicAbout(url, env) };
  if (toolName === "onboarding") {
    return {
      status: "ok",
      result: {
        role_guidance:
          "Introduce Astrata in one or two sentences, then ask what the user wants to try. Do not require sign-in unless the user asks for personalized account, device, or local-node work.",
        suggested_intro:
          "Astrata is a local-first AI operating system that makes your own computer reachable by durable agents and remote clients. You can explore the concept here, then sign in or install a local node when you want deeper capability.",
        access_boundary:
          "Downloading and local onboarding are public. Hosted bridge activation is a separate, invite-gated cloud step for new tester accounts.",
        access_policy: gptAccessPolicy(url),
        hosted_bridge_eligibility: publicHostedBridgeEligibility(),
        next_actions: ["capability_map", "sign_in", "install_local_node", "submit_feedback"],
      },
    };
  }
  if (toolName === "capability_map") {
    return {
      status: "ok",
      result: {
        signed_out: {
          label: "Public GPT",
          capabilities: ["orientation", "capability discovery", "feedback", "setup guidance"],
          boundary: "No personalized Astrata account, relay queue, or local desktop access. Download/install and local onboarding are still public.",
        },
        signed_in: {
          label: "Astrata Account",
          capabilities: ["account-bound profile", "device selection", "personalized relay routing"],
          boundary: "Hosted bridge access is active here. New account creation may have required an invite; local-only workflows still need a paired desktop node.",
        },
        local_node: {
          label: "Paired Desktop",
          capabilities: ["local Prime routing", "durable local task queue", "higher-trust tools"],
          boundary: "Local-only and enclave-only data should still require explicit user approval.",
        },
        access_policy: gptAccessPolicy(url),
        hosted_bridge_eligibility: publicHostedBridgeEligibility(),
      },
    };
  }
  if (toolName === "sign_in") {
    return handlePublicSignInStart(url, env, payload);
  }
  if (toolName === "complete_sign_in") {
    return handlePublicSignInComplete(args, env, url);
  }
  if (toolName === "account_status") {
    const auth = await publicSessionAuth(env, args.session_token);
    if (!auth.authorized) return publicSignInRequired(url, auth.error);
    const context = await accountSecurityContext(env, auth);
    return {
      status: "ok",
      result: {
        signed_in: true,
        user_id: auth.user_id,
        profile_id: auth.profile_id,
        device_id: auth.device_id || "",
        auth_kind: auth.auth_kind,
        capability_tier: context.capability_tier,
        scopes: context.scopes,
        paired_local_node: context.paired_local_node,
        elevation: context.elevation,
        available_tools: context.available_tools,
      },
    };
  }
  if (toolName === "account_tool_search") {
    const auth = await publicSessionAuth(env, args.session_token);
    if (!auth.authorized) return publicSignInRequired(url, auth.error);
    return handleSecurityScopedToolSearch(args, env, url, auth);
  }
  if (toolName === "account_use_tool") {
    const auth = await publicSessionAuth(env, args.session_token);
    if (!auth.authorized) return publicSignInRequired(url, auth.error);
    const nested = args.request && typeof args.request === "object" ? args.request : args;
    return handleSecurityScopedUseTool(nested, env, url, auth);
  }
  if (toolName === "install_local_node") {
    return {
      status: "ok",
      result: {
        message:
          "A local node is the desktop Astrata client that turns the GPT from an explainer into a remote operator for the user's own machine.",
        steps: [
          "Install and open Astrata Desktop.",
          "Sign in to the same Astrata account.",
          "Link the desktop to the account/profile.",
          "Use the account OAuth action when requesting personalized or local-node work.",
        ],
        access_policy: gptAccessPolicy(url),
        hosted_bridge_eligibility: publicHostedBridgeEligibility(),
        current_v0_note:
          "The development bridge still uses quick account/device primitives; the product target is hosted Astrata Web login plus user-owned device selection.",
      },
    };
  }
  if (toolName === "submit_feedback") {
    return handleGptSubmitFeedback({
      env,
      profileId: `public:${defaultProfileId(env, url) || "anonymous"}`,
      args: {
        ...args,
        context: {
          ...(args.context && typeof args.context === "object" ? args.context : {}),
          public_no_auth: true,
        },
      },
    });
  }
  return {
    status: "failed",
    error: "unknown_public_tool",
    available_tools: publicToolDefinitions(url).map((tool) => tool.name),
  };
}

function publicSignInRequired(url, reason = "sign_in_required") {
  return {
    status: "sign_in_required",
    error: reason,
    result: {
      message: "Call the sign_in tool before using account-bound Astrata tools. It will return a request-scoped sign-in URL and request_id.",
      access_boundary:
        "Public onboarding and install help do not require sign-in. Hosted bridge access is the gated boundary, and new hosted accounts may require an invite code.",
      access_policy: gptAccessPolicy(url),
      hosted_bridge_eligibility: publicHostedBridgeEligibility(),
      next_tool_call: {
        tool: "sign_in",
        args: {},
      },
    },
  };
}

async function handlePublicSignInStart(url, env, payload = {}) {
  const requestId = `astrata-login-request-${crypto.randomUUID()}`;
  const request = {
    request_id: requestId,
    status: "pending",
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 15 * 60 * 1000).toISOString(),
  };
  await setPublicLoginRequest(env, requestId, request);
  const signInUrl = `${url.origin}/gpt/public/signin?request_id=${encodeURIComponent(requestId)}`;
  return {
    status: "ok",
    result: {
      message:
        "Ask the user to open the sign-in URL, then call complete_sign_in with request_id after the user says they finished. Existing accounts can sign in directly; creating a new hosted account may require an invite code. If the page gives them a completion code, complete_sign_in also accepts that code.",
      sign_in_url: signInUrl,
      request_id: requestId,
      access_policy: gptAccessPolicy(url),
      hosted_bridge_eligibility: publicHostedBridgeEligibility(),
      next_tool_call: {
        tool: "complete_sign_in",
        args: { request_id: requestId },
      },
      preferred_action: {
        operation: "public_complete_sign_in",
        request_id: requestId,
      },
      note:
        "This is a tool-level development flow so the public GPT action can remain unauthenticated. Treat the returned session token as scoped conversation state.",
    },
  };
}

async function handlePublicSignInComplete(payload, env, url) {
  const requestId = String(payload?.request_id || payload?.login_request_id || "").trim();
  if (requestId) {
    const request = await getPublicLoginRequest(env, requestId);
    if (!request) return { status: "failed", error: "unknown_sign_in_request" };
    if (Date.parse(request.expires_at || "") <= Date.now()) {
      await deletePublicLoginRequest(env, requestId);
      return { status: "failed", error: "expired_sign_in_request" };
    }
    if (request.status !== "complete") {
      return {
        status: "pending",
        result: {
          message: "Sign-in has not completed yet. Ask the user to finish the browser sign-in, then call complete_sign_in with the same request_id again.",
          request_id: requestId,
          sign_in_url: `${url.origin}/gpt/public/signin?request_id=${encodeURIComponent(requestId)}`,
        },
      };
    }
    await deletePublicLoginRequest(env, requestId);
    return {
      status: "ok",
      result: {
        message: "Sign-in completed. Use this session token only for follow-up account_* public tools in this conversation.",
        session_token: request.session_token,
        user_id: request.user_id,
        email: request.email,
        profile_id: request.profile_id,
        expires_at: request.session_expires_at || request.expires_at,
        access_policy: gptAccessPolicy(url),
        hosted_bridge_eligibility: activeHostedBridgeEligibility(),
        next_tools: ["account_status", "account_tool_search", "account_use_tool"],
      },
    };
  }
  const code = String(payload?.code || payload?.completion_code || "").trim().toUpperCase();
  const record = await getPublicLoginCode(env, code);
  if (!record) return { status: "failed", error: "unknown_or_expired_sign_in_code" };
  if (Date.parse(record.expires_at || "") <= Date.now()) {
    await deletePublicLoginCode(env, code);
    return { status: "failed", error: "expired_sign_in_code" };
  }
  await deletePublicLoginCode(env, code);
  return {
    status: "ok",
    result: {
      message: "Sign-in completed. Use this session token only for follow-up account_* public tools in this conversation.",
      session_token: record.session_token,
      user_id: record.user_id,
      email: record.email,
      profile_id: record.profile_id,
      expires_at: record.expires_at,
      access_policy: gptAccessPolicy(url),
      hosted_bridge_eligibility: activeHostedBridgeEligibility(),
      next_tools: ["account_status", "account_tool_search", "account_use_tool"],
    },
  };
}

async function publicSessionAuth(env, sessionToken) {
  const token = String(sessionToken || "").trim();
  if (!token) return { authorized: false, error: "session_token_required" };
  const record = await getOAuthToken(env, token);
  if (!record) return { authorized: false, error: "session_not_found" };
  if (String(record.status || "active") !== "active") return { authorized: false, error: "session_not_active" };
  if (Date.parse(record.expires_at || "") <= Date.now()) return { authorized: false, error: "session_expired" };
  return {
    authorized: true,
    auth_kind: "public_tool_session",
    access_token: token,
    user_id: String(record.user_id || ""),
    profile_id: String(record.profile_id || "").trim(),
    device_id: String(record.device_id || "").trim(),
  };
}

async function accountSecurityContext(env, auth) {
  const profileId = String(auth?.profile_id || "").trim();
  const userId = String(auth?.user_id || "").trim();
  const profile = await getRelayProfileRecord(env, profileId);
  const advertisementRecord = await getAdvertisement(env, profileId);
  const advertisement = advertisementRecord?.advertisement || advertisementRecord || {};
  const activeLinks = await getActiveDeviceLinks(env, profileId);
  const pairedLocalNode = activeLinks.length > 0 || Boolean(auth?.device_id);
  const advertisedRemoteHostBash = advertisement?.remote_host_bash || {};
  const remoteHostBash = {
    enabled: Boolean(
      advertisedRemoteHostBash?.enabled ||
      profile?.allow_remote_host_bash ||
      profile?.remote_host_bash_acknowledged_at
    ),
    acknowledged_at:
      advertisedRemoteHostBash?.acknowledged_at ||
      profile?.remote_host_bash_acknowledged_at ||
      null,
    warning:
      String(advertisedRemoteHostBash?.warning || profile?.remote_host_bash_warning || "").trim() ||
      "This allows any GPT session authenticated to this Astrata profile to execute arbitrary host shell commands on any connected computer for that profile.",
    requires_special_acknowledgement: true,
  };
  const elevation = {
    active: false,
    level: "none",
    reason: "2fa_elevation_not_implemented",
  };
  const scopes = ["signed_in"];
  if (pairedLocalNode) scopes.push("paired_local_node");
  if (pairedLocalNode && remoteHostBash.enabled) scopes.push("remote_host_bash");
  if (elevation.active) scopes.push("elevated_session");
  const toolNames = [
    ...SIGNED_IN_ACCOUNT_TOOLS,
    ...(pairedLocalNode ? PAIRED_LOCAL_NODE_TOOLS : []),
    ...(pairedLocalNode && remoteHostBash.enabled ? REMOTE_HOST_BASH_TOOLS : []),
    ...(elevation.active ? ELEVATED_SESSION_TOOLS : []),
  ];
  return {
    user_id: userId,
    profile_id: profileId,
    capability_tier: elevation.active ? "elevated_session" : pairedLocalNode ? "paired_local_node" : "signed_in",
    scopes,
    paired_local_node: pairedLocalNode,
    remote_host_bash: remoteHostBash,
    elevation,
    profile,
    active_device_links: activeLinks,
    available_tools: Array.from(new Set(toolNames)),
  };
}

async function handleSecurityScopedToolSearch(payload, env, url, auth) {
  const context = await accountSecurityContext(env, auth);
  const query = String(payload?.query || "").trim().toLowerCase();
  const tools = summarizeSecurityScopedTools(context.available_tools, context);
  const filtered = query
    ? tools.filter((tool) => `${tool.name} ${tool.description} ${tool.security_scope}`.toLowerCase().includes(query))
    : tools;
  return {
    status: "ok",
    profile_id: context.profile_id,
    capability_tier: context.capability_tier,
    scopes: context.scopes,
    paired_local_node: context.paired_local_node,
    remote_host_bash: context.remote_host_bash,
    elevation: context.elevation,
    tools: filtered,
    count: filtered.length,
    total_available: tools.length,
    withheld: withheldSecurityScopedTools(context),
  };
}

async function handleSecurityScopedUseTool(payload, env, url, auth) {
  const context = await accountSecurityContext(env, auth);
  const toolName = String(payload?.tool || payload?.name || "").trim();
  if (!toolName) return { status: "failed", error: "tool_required" };
  if (!context.available_tools.includes(toolName)) {
    return {
      status: "blocked",
      error: "tool_not_available_for_security_scope",
      tool: toolName,
      capability_tier: context.capability_tier,
      scopes: context.scopes,
      required: securityRequirementForTool(toolName),
      withheld: withheldSecurityScopedTools(context),
    };
  }
  if (toolName === "account_status") {
    return {
      status: "ok",
      result: {
        signed_in: true,
        user_id: context.user_id,
        profile_id: context.profile_id,
        capability_tier: context.capability_tier,
        scopes: context.scopes,
        paired_local_node: context.paired_local_node,
        remote_host_bash: context.remote_host_bash,
        elevation: context.elevation,
        available_tools: context.available_tools,
        access_policy: gptAccessPolicy(url),
        hosted_bridge_eligibility: activeHostedBridgeEligibility(context),
      },
    };
  }
  if (toolName === "device_status") {
    return {
      status: "ok",
      result: {
        paired_local_node: context.paired_local_node,
        active_device_links: context.active_device_links,
        remote_host_bash: context.remote_host_bash,
        message: context.paired_local_node
          ? "A local node appears to be linked for this profile."
          : "No active local node link is available yet. File, patch, and shell-like tools remain withheld.",
      },
    };
  }
  if (toolName === "install_local_node") {
    return handleGptPublicUseTool({ tool: "install_local_node", args: payload?.args || {} }, env, url);
  }
  if (["search_files", "read_file", "propose_patch", "apply_patch", "run_tests", "run_command", "request_elevation"].includes(toolName)) {
    const args = payload?.args && typeof payload.args === "object" ? payload.args : {};
    return handleGptUseTool({ ...payload, tool: toolName, args }, env, url, auth);
  }
  return handleGptUseTool(payload, env, url, auth);
}

function summarizeSecurityScopedTools(toolNames, context) {
  return toolNames.map((name) => {
    const definition = adapterToolDefinition(name);
    return {
      name,
      description: definition.description,
      requires_polling: !["account_status", "device_status", "install_local_node", "submit_feedback", "get_result", "get_session"].includes(name),
      input_schema: definition.inputSchema,
      security_scope: securityRequirementForTool(name),
      capability_tier: context.capability_tier,
    };
  });
}

function withheldSecurityScopedTools(context) {
  const withheld = [];
  if (!context.paired_local_node) {
    withheld.push({
      scope: "paired_local_node",
      tools: PAIRED_LOCAL_NODE_TOOLS,
      reason: "No active paired local node is available for this account/profile.",
    });
  }
  if (!context.remote_host_bash?.enabled) {
    withheld.push({
      scope: "remote_host_bash_acknowledged",
      tools: REMOTE_HOST_BASH_TOOLS,
      reason:
        "Generic host bash access is withheld until the operator explicitly acknowledges that any logged-in GPT for this profile may control connected computers.",
    });
  }
  if (!context.elevation.active) {
    withheld.push({
      scope: "elevated_session",
      tools: ELEVATED_SESSION_TOOLS,
      reason: "2FA/elevated-session approval is not active.",
    });
  }
  return withheld;
}

function securityRequirementForTool(toolName) {
  if (REMOTE_HOST_BASH_TOOLS.includes(toolName)) return "remote_host_bash_acknowledged";
  if (ELEVATED_SESSION_TOOLS.includes(toolName)) return "elevated_session";
  if (PAIRED_LOCAL_NODE_TOOLS.includes(toolName)) return "paired_local_node";
  if (SIGNED_IN_ACCOUNT_TOOLS.includes(toolName)) return "signed_in";
  return "unknown_or_relay_policy";
}

async function handleGptListTools(env, url, auth) {
  const resolved = resolveProfileId({ requestedProfileId: "", auth, env, url });
  if (resolved.error) return { status: "failed", error: resolved.error, tools: [] };
  const profileId = resolved.profile_id;
  const advertisement = await getAdvertisement(env, profileId);
  const toolNames = adapterToolNames(advertisement);
  const shortlist = preferredToolShortlist(toolNames);
  return {
    status: "ok",
    profile_id: profileId,
    access_policy: gptAccessPolicy(url),
    hosted_bridge_eligibility: auth?.authorized ? activeHostedBridgeEligibility() : publicHostedBridgeEligibility(),
    tools: summarizeTools(shortlist),
    count: shortlist.length,
    total_available: toolNames.length,
    caveat: "This is a basic shortlist. Use tool_search(query) for the full permissioned tool catalog.",
  };
}

async function handleGptToolSearch(payload, env, url, auth) {
  const resolved = resolveProfileId({
    requestedProfileId: String(payload?.profile_id || ""),
    auth,
    env,
    url,
  });
  if (resolved.error) return { status: "failed", error: resolved.error, tools: [] };
  const profileId = resolved.profile_id;
  const query = String(payload?.query || "").trim().toLowerCase();
  const advertisement = await getAdvertisement(env, profileId);
  const toolNames = adapterToolNames(advertisement);
  const tools = summarizeTools(toolNames);
  const filtered = query
    ? tools.filter((tool) => `${tool.name} ${tool.description}`.toLowerCase().includes(query))
    : tools;
  return {
    status: "ok",
    profile_id: profileId,
    access_policy: gptAccessPolicy(url),
    hosted_bridge_eligibility: auth?.authorized ? activeHostedBridgeEligibility() : publicHostedBridgeEligibility(),
    tools: filtered,
    count: filtered.length,
    total_available: tools.length,
  };
}

async function handleGptUseTool(payload, env, url, auth) {
  const resolved = resolveProfileId({
    requestedProfileId: String(payload?.profile_id || ""),
    auth,
    env,
    url,
  });
  if (resolved.error) return { status: "failed", error: resolved.error };
  const profileId = resolved.profile_id;
  const toolName = String(payload?.tool || payload?.name || "").trim();
  if (!toolName) return { status: "failed", error: "tool_required" };
  const args = {
    ...(payload?.args && typeof payload.args === "object" ? payload.args : {}),
  };
  if (payload?.session_id && !args.session_id) args.session_id = String(payload.session_id);
  if (payload?.idempotency_key && !args.idempotency_key) args.idempotency_key = String(payload.idempotency_key);

  if (toolName === "help") return { status: "ok", result: gptHelp(url, env) };
  if (toolName === "about") return { status: "ok", result: gptAbout(url, env) };
  if (toolName === "onboarding") return { status: "ok", result: gptOnboarding(url, env) };
  if (toolName === "list_tools") return handleGptListTools(env, url, auth);
  if (toolName === "tool_search") return handleGptToolSearch(args, env, url, auth);
  if (toolName === "submit_feedback") return handleGptSubmitFeedback({ env, profileId, args });
  if (toolName === "get_result") return { status: "ok", result: await lookupResult(env, String(args.request_id || "")) };
  if (toolName === "get_session") {
    const sessionId = String(args.session_id || `session:${profileId}`);
    return {
      status: "ok",
      session_id: sessionId,
      result: await readSession(env, { profileId, sessionId, actor: "remote" }),
    };
  }

  const relayPayload = {
    jsonrpc: "2.0",
    id: String(payload?.idempotency_key || args.idempotency_key || crypto.randomUUID()),
    method: "tools/call",
    params: {
      profile_id: profileId,
      name: toolName,
      arguments: {
        ...args,
        session_id: String(args.session_id || `session:${profileId}`),
      },
      _meta: {
        connector: "custom_gpt_action",
      },
    },
  };
  const receiptKey = adapterReceiptKey({ adapter: "custom_gpt_action", profileId, toolName, id: relayPayload.id, args });
  if (receiptKey) {
    const previousReceipt = await getAdapterReceipt(env, receiptKey);
    if (previousReceipt) return { ...previousReceipt, replayed: true };
  }
  const queued = await handleRelayMcp(relayPayload, env);
  const body = await queued.json();
  const requestId = body?.result?.request?.request_id || "";
  const receipt = {
    status: "received",
    request_id: requestId,
    session_id: relayPayload.params.arguments.session_id,
    message: "Message received. Check back periodically with use_tool(tool='get_result') or use_tool(tool='get_session') to see status and replies.",
    queued: body?.result || {},
  };
  if (receiptKey) await setAdapterReceipt(env, receiptKey, receipt);
  return receipt;
}

function preferredToolShortlist(toolNames) {
  const preferred = ["message_prime", "get_session", "get_result", "get_task_status", "list_capabilities"];
  const available = new Set(toolNames);
  return preferred.filter((name) => available.has(name));
}

async function handleGptSubmitFeedbackAction(payload, env, url, auth) {
  const resolved = resolveProfileId({
    requestedProfileId: String(payload?.profile_id || ""),
    auth,
    env,
    url,
  });
  if (resolved.error) return { status: "failed", error: resolved.error };
  const profileId = resolved.profile_id;
  return handleGptSubmitFeedback({ env, profileId, args: payload || {} });
}

function summarizeTools(toolNames) {
  return toolNames.map((name) => {
    const definition = adapterToolDefinition(name);
    return {
      name,
      description: definition.description,
      requires_polling: !["get_result", "get_session", "list_capabilities", "get_task_status", "search", "fetch", "submit_feedback"].includes(name),
      input_schema: definition.inputSchema,
    };
  });
}

async function handleGptSubmitFeedback({ env, profileId, args }) {
  const feedback = {
    feedback_id: crypto.randomUUID(),
    profile_id: profileId,
    source: "custom_gpt_action",
    category: String(args.category || "general"),
    severity: String(args.severity || "normal"),
    message: String(args.message || args.feedback || "").trim(),
    context: args.context && typeof args.context === "object" ? args.context : {},
    created_at: new Date().toISOString(),
  };
  if (!feedback.message) return { status: "failed", error: "feedback_message_required" };
  const items = await getFeedback(env, profileId);
  items.push(feedback);
  await setFeedback(env, profileId, items);
  return {
    status: "received",
    feedback_id: feedback.feedback_id,
    message: "Feedback received. Thank you; this will be used to improve Astrata.",
  };
}

async function handleMcpAdapter(payload, env, { adapter, url, auth }) {
  if (Array.isArray(payload)) {
    const responses = [];
    for (const message of payload) {
      const response = await handleMcpAdapterMessage(message, env, { adapter, url, auth });
      if (response !== null) responses.push(response);
    }
    return json(responses);
  }
  const response = await handleMcpAdapterMessage(payload, env, { adapter, url, auth });
  return response === null ? new Response(null, { status: 202, headers: corsHeaders() }) : json(response);
}

async function handleMcpAdapterMessage(payload, env, { adapter, url, auth }) {
  const id = payload?.id ?? null;
  const method = String(payload?.method || "");
  const params = payload?.params || {};
  const resolved = resolveProfileId({
    requestedProfileId: String(params.profile_id || ""),
    auth,
    env,
    url,
  });
  if (resolved.error) return jsonRpcError(id, "No relay profile configured for this MCP adapter.");
  const profileId = resolved.profile_id;

  if (method === "initialize") {
    return {
      jsonrpc: "2.0",
      id,
      result: {
        protocolVersion: payload?.params?.protocolVersion || "2025-03-26",
        capabilities: {
          tools: {},
        },
        serverInfo: {
          name: `astrata-${adapter}-relay`,
          version: "0.1.0",
        },
      },
    };
  }

  if (method === "notifications/initialized") return null;

  if (method === "tools/list") {
    const advertisement = await getAdvertisement(env, profileId);
    const allowedTools = adapterToolNames(advertisement);
    return {
      jsonrpc: "2.0",
      id,
      result: {
        tools: allowedTools.map((name) => adapterToolDefinition(name)),
      },
    };
  }

  if (method === "tools/call") {
    const toolName = String(params.name || "").trim();
    const args = params.arguments || {};
    if (toolName === "get_result") {
      return toolResult(id, await lookupResult(env, String(args.request_id || "")));
    }
    if (toolName === "get_session") {
      const sessionId = String(args.session_id || `session:${profileId}`);
      return toolResult(id, await readSession(env, { profileId, sessionId, actor: "remote" }));
    }
    const relayPayload = {
      jsonrpc: "2.0",
      id,
      method: "tools/call",
      params: {
        profile_id: profileId,
        name: toolName,
        arguments: {
          ...args,
          session_id: String(args.session_id || `session:${profileId}`),
        },
        _meta: {
          connector: adapter,
        },
      },
    };
    const receiptKey = adapterReceiptKey({ adapter, profileId, toolName, id, args });
    if (receiptKey) {
      const previousReceipt = await getAdapterReceipt(env, receiptKey);
      if (previousReceipt) {
        return toolResult(id, { ...previousReceipt, replayed: true });
      }
    }
    const queued = await handleRelayMcp(relayPayload, env);
    const body = await queued.json();
    const requestId = body?.result?.request?.request_id || "";
    const receipt = {
      status: "received",
      request_id: requestId,
      session_id: relayPayload.params.arguments.session_id,
      message: "Message received. Check back periodically with get_result or get_session to see status and replies.",
      queued: body?.result || {},
    };
    if (receiptKey) await setAdapterReceipt(env, receiptKey, receipt);
    return toolResult(id, receipt);
  }

  return jsonRpcError(id, `Unsupported MCP method ${method || "unknown"}`);
}

function adapterReceiptKey({ adapter, profileId, toolName, id, args }) {
  const explicitKey = String(args.idempotency_key || args.client_request_id || "").trim();
  const fallbackKey = id === null || id === undefined ? "" : String(id).trim();
  const key = explicitKey || fallbackKey;
  return key ? `relay:${profileId}:adapter:${adapter}:receipt:${toolName}:${key}` : "";
}

function defaultProfileId(env, url) {
  return (
    String(url.searchParams.get("profile_id") || "").trim() ||
    String(env.RELAY_DEFAULT_PROFILE_ID || "").trim()
  );
}

function resolveProfileId({ requestedProfileId, auth, env, url }) {
  const requested = String(requestedProfileId || "").trim();
  const authorizedProfileId = String(auth?.profile_id || "").trim();
  if (authorizedProfileId) {
    if (requested && requested !== authorizedProfileId) {
      return { error: "profile_override_not_allowed" };
    }
    return { profile_id: authorizedProfileId };
  }
  const fallback = requested || defaultProfileId(env, url);
  return fallback ? { profile_id: fallback } : { error: "profile_id_required" };
}

async function resolveAdapterAuth(request, env) {
  const url = new URL(request.url);
  const expected = String(env.RELAY_SHARED_TOKEN || "");
  const bearer = String(request.headers.get("authorization") || "");
  const token = String(url.searchParams.get("token") || "");
  if (!!expected && (bearer === `Bearer ${expected}` || token === expected)) {
    return {
      authorized: true,
      auth_kind: "shared_token",
      profile_id: defaultProfileId(env, url),
    };
  }
  const accessToken = extractBearerToken(bearer);
  if (!accessToken) return { authorized: false };
  if (controlPlaneBaseUrl(env)) {
    const result = await callControlPlaneJson(env, "/oauth/introspect", {
      access_token: accessToken,
    });
    if (!result.ok) return { authorized: false };
    return {
      authorized: true,
      auth_kind: "oauth",
      access_token: accessToken,
      user_id: String(result.payload?.user_id || ""),
      profile_id: String(result.payload?.profile_id || "").trim(),
      device_id: String(result.payload?.device_id || "").trim(),
    };
  }
  const record = await getOAuthToken(env, accessToken);
  if (!record) return { authorized: false };
  if (Date.parse(record.expires_at || "") <= Date.now()) return { authorized: false };
  if (String(record.resource || "") !== resourceIdentifier(url)) return { authorized: false };
  return {
    authorized: true,
    auth_kind: "oauth",
    access_token: accessToken,
    user_id: String(record.user_id || ""),
    profile_id: String(record.profile_id || "").trim(),
    device_id: String(record.device_id || "").trim(),
  };
}

function controlPlaneBaseUrl(env) {
  return String(env.ASTRATA_CONTROL_PLANE_URL || "").trim().replace(/\/+$/, "");
}

async function proxyControlPlaneRequest(request, env, path, options = {}) {
  const base = controlPlaneBaseUrl(env);
  if (!base) {
    return json({ error: "control_plane_not_configured" }, 500);
  }
  const query = String(options.query || "");
  const headers = new Headers();
  const requestContentType = request.headers.get("content-type");
  const requestAccept = request.headers.get("accept");
  const requestAuthorization = request.headers.get("authorization");
  if (requestContentType) headers.set("content-type", requestContentType);
  if (requestAccept) headers.set("accept", requestAccept);
  if (requestAuthorization) headers.set("authorization", requestAuthorization);
  const bearer = String(env.ASTRATA_CONTROL_PLANE_BEARER_TOKEN || "").trim();
  if (bearer) headers.set("x-astrata-control-plane-bearer", bearer);
  const init = {
    method: request.method,
    headers,
    redirect: "manual",
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.clone().text();
  }
  try {
    return await fetch(`${base}${path}${query}`, init);
  } catch (error) {
    return json(
      {
        error: "control_plane_unreachable",
        detail: String(error && error.message ? error.message : error),
      },
      502,
    );
  }
}

async function callControlPlaneJson(env, path, payload) {
  const base = controlPlaneBaseUrl(env);
  if (!base) return { ok: false, error: "Astrata control plane URL is not configured." };
  const headers = {
    "content-type": "application/json",
    "accept": "application/json",
  };
  const bearer = String(env.ASTRATA_CONTROL_PLANE_BEARER_TOKEN || "").trim();
  if (bearer) headers.authorization = `Bearer ${bearer}`;
  try {
    const response = await fetch(`${base}${path}`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload || {}),
    });
    const data = await response.json().catch(() => ({}));
    return response.ok ? { ok: true, payload: data } : { ok: false, payload: data, status: response.status };
  } catch (error) {
    return { ok: false, error: String(error?.message || error || "Astrata control plane request failed.") };
  }
}

function controlPlaneErrorMessage(result, fallback) {
  if (result?.payload?.detail?.message) return String(result.payload.detail.message);
  if (result?.payload?.detail?.status) return String(result.payload.detail.status);
  if (result?.payload?.error_description) return String(result.payload.error_description);
  if (result?.error) return String(result.error);
  return fallback;
}

function adapterToolNames(advertisement) {
  const allowed = advertisement?.advertisement?.allowed_tools || DEFAULT_TOOLS;
  return Array.from(new Set([...allowed, "onboarding", "submit_feedback", "get_result", "get_session"]));
}

function adapterToolDefinition(name) {
  const descriptions = {
    search: "Queue a connector-safe Astrata search request.",
    fetch: "Queue a connector-safe Astrata fetch request.",
    submit_task: "Submit governed work to Astrata.",
    get_task_status: "Queue a connector-safe task-status request.",
    list_capabilities: "Queue a connector-safe capability request.",
    onboarding: "Fetch current onboarding guidance for a Custom GPT acting as an Astrata remote operator.",
    submit_feedback: "Submit product feedback about Astrata, the GPT bridge, missing tools, bugs, or confusing behavior.",
    message_prime: "Send a message to Astrata Prime through the hosted relay.",
    delegate_subtasks: "Request governed task decomposition under the active relay posture.",
    handoff_to_controller: "Request a governed handoff to an Astrata controller.",
    request_browser_action: "Request a governed browser action.",
    get_result: "Fetch a hosted relay result by request id.",
    get_session: "Fetch a hosted relay dialogue session with read receipts.",
    account_status: "Show the current signed-in Astrata account capability tier and available scopes.",
    device_status: "Show whether this account/profile has an active paired local node.",
    install_local_node: "Explain how to install and pair an Astrata local node.",
    search_files: "Search files inside approved local-node workspace roots.",
    read_file: "Read a file inside approved local-node workspace roots with size limits.",
    propose_patch: "Propose a patch for files inside approved local-node workspace roots.",
    apply_patch: "Apply an approved patch inside approved local-node workspace roots. Requires elevated session.",
    run_tests: "Run approved test commands for the selected workspace. Requires elevated session.",
    run_command: "Run a generic host bash command through the local node. Requires a paired local node plus a special acknowledgement that any logged-in GPT for this profile may control connected computers.",
    request_elevation: "Request 2FA/elevated-session approval for higher-risk local-node tools.",
  };
  const schemas = {
    get_result: {
      type: "object",
      properties: {
        request_id: { type: "string", description: "Hosted relay request id." },
      },
      required: ["request_id"],
    },
    get_session: {
      type: "object",
      properties: {
        session_id: { type: "string", description: "Hosted relay session id." },
      },
    },
    onboarding: {
      type: "object",
      properties: {},
      additionalProperties: false,
    },
    submit_feedback: {
      type: "object",
      required: ["message"],
      properties: {
        message: { type: "string", description: "Feedback text." },
        category: { type: "string", description: "Optional category, such as ux, bug, missing_tool, docs, or bridge." },
        severity: { type: "string", description: "Optional severity, such as low, normal, high, or urgent." },
        context: {
          type: "object",
          description: "Optional structured context for the feedback.",
          additionalProperties: true,
        },
      },
    },
    read_file: {
      type: "object",
      required: ["path"],
      properties: {
        path: { type: "string", description: "Workspace-relative or approved-root file path." },
        max_bytes: { type: "integer", description: "Maximum bytes to return." },
      },
    },
    search_files: {
      type: "object",
      properties: {
        query: { type: "string", description: "Search query." },
        glob: { type: "string", description: "Optional file glob." },
        max_results: { type: "integer", description: "Maximum result count." },
      },
    },
    propose_patch: {
      type: "object",
      required: ["summary", "patch"],
      properties: {
        summary: { type: "string", description: "Human-readable patch summary." },
        patch: { type: "string", description: "Unified diff patch proposal." },
      },
    },
    run_command: {
      type: "object",
      required: ["command"],
      properties: {
        command: { type: "string", description: "Command to run subject to local policy." },
        cwd: { type: "string", description: "Workspace directory." },
        reason: { type: "string", description: "Why this command is needed." },
      },
    },
  };
  return {
    name,
    description: descriptions[name] || `Astrata hosted relay tool: ${name}`,
    inputSchema: schemas[name] || {
      type: "object",
      properties: {
        session_id: { type: "string", description: "Optional relay dialogue session id." },
      },
      additionalProperties: true,
    },
  };
}

function toolResult(id, payload) {
  return {
    jsonrpc: "2.0",
    id,
    result: {
      content: [
        {
          type: "text",
          text: JSON.stringify(payload, null, 2),
        },
      ],
      structuredContent: payload,
    },
  };
}

async function handleRelayMcp(payload, env) {
  const params = payload?.params || {};
  const meta = params?._meta || {};
  const profileId = String(params.profile_id || meta.profile_id || "").trim();
  if (!profileId) {
    return json(jsonRpcError(payload?.id, "profile_id is required"), 400);
  }

  if (payload?.method === "tools/list") {
    const advertisement = await getAdvertisement(env, profileId);
    const allowedTools = advertisement?.advertisement?.allowed_tools || DEFAULT_TOOLS;
    return json({
      jsonrpc: "2.0",
      id: payload?.id ?? null,
      result: {
        tools: allowedTools.map((name) => ({
          name,
          description: `Astrata hosted relay tool: ${name}`,
          inputSchema: { type: "object" },
        })),
      },
    });
  }

  if (payload?.method !== "tools/call") {
    return json(jsonRpcError(payload?.id, `Unsupported method ${payload?.method || "unknown"}`), 400);
  }

  const toolName = String(params.name || "").trim();
  const argumentsPayload = params.arguments || {};
  const sessionId = String(argumentsPayload.session_id || params.session_id || `session:${profileId}`).trim();
  const requestRecord = {
    request_id: crypto.randomUUID(),
    profile_id: profileId,
    tool_name: toolName,
    arguments: argumentsPayload,
    source_connector: String(meta.connector || "remote_connector"),
    target_controller: String(argumentsPayload.target_controller || "prime"),
    task_id: String(argumentsPayload.task_id || `relay:${profileId}:${toolName || "request"}`),
    session_id: sessionId,
    status: "queued",
    created_at: new Date().toISOString(),
  };

  const queue = await getPendingQueue(env, profileId);
  queue.push(requestRecord);
  await setPendingQueue(env, profileId, queue);
  await appendSessionMessage(
    env,
    {
      profile_id: profileId,
      session_id: sessionId,
      request_id: requestRecord.request_id,
      kind: "tool_call",
      content: {
        tool_name: toolName,
        arguments: argumentsPayload,
      },
    },
    "remote",
  );

  return json({
    jsonrpc: "2.0",
    id: payload?.id ?? null,
    result: {
      delivery: "queued",
      request: requestRecord,
      handoff: null,
      content: [
        {
          type: "text",
          text: `Queued ${toolName || "request"} for Astrata local delivery.`,
        },
      ],
    },
  });
}

async function handleLocalHeartbeat(payload, env) {
  const profileId = String(payload?.profile_id || "").trim();
  if (!profileId) return json({ error: "profile_id_required" }, 400);

  const advertisementWrite = await maybeSetAdvertisement(env, profileId, payload);
  const pendingRequests = await getPendingQueue(env, profileId);
  if (pendingRequests.length > 0) await markSessionsSeen(env, profileId, "local");

  return json({
    ok: true,
    accepted: true,
    kind: "local_heartbeat",
    payload,
    advertisement_write: advertisementWrite,
    pending_requests: pendingRequests,
  });
}

async function handleLocalAck(payload, env) {
  const profileId = String(payload?.profile_id || "").trim();
  const requestIds = Array.isArray(payload?.request_ids) ? payload.request_ids.map(String) : [];
  if (!profileId) return json({ error: "profile_id_required" }, 400);

  const queue = await getPendingQueue(env, profileId);
  const remaining = [];
  const acked = [];
  for (const request of queue) {
    if (requestIds.includes(String(request.request_id))) {
      acked.push({ ...request, status: "acknowledged", acknowledged_at: new Date().toISOString() });
    } else {
      remaining.push(request);
    }
  }
  await setPendingQueue(env, profileId, remaining);
  if (acked.length) {
    const current = await getAckedRequests(env, profileId);
    await setAckedRequests(env, profileId, current.concat(acked));
  }

  return json({
    ok: true,
    accepted: true,
    acknowledged_request_ids: requestIds,
    remaining_queue_depth: remaining.length,
  });
}

async function handleLocalResult(payload, env) {
  const profileId = String(payload?.profile_id || "").trim();
  const requestId = String(payload?.request_id || "").trim();
  if (!profileId || !requestId) return json({ error: "profile_id_and_request_id_required" }, 400);

  const results = await getResults(env, profileId);
  results.push({
    request_id: requestId,
    result: payload?.result || {},
    created_at: new Date().toISOString(),
  });
  await setResults(env, profileId, results);
  await appendSessionMessage(
    env,
    {
      profile_id: profileId,
      session_id: payload?.session_id || `session:${profileId}`,
      request_id: requestId,
      kind: "tool_result",
      content: payload?.result || {},
    },
    "local",
  );

  return json({
    ok: true,
    accepted: true,
    request_id: requestId,
  });
}

async function handlePairingCreate(payload, env) {
  const profileId = String(payload?.profile_id || "").trim();
  if (!profileId) return { error: "profile_id_required" };
  const ttlMinutes = Math.max(1, Math.min(60, Number(payload?.ttl_minutes || 15) || 15));
  const pairing = {
    code: generatePairingCode(),
    profile_id: profileId,
    user_id: String(payload?.user_id || "").trim(),
    device_id: String(payload?.device_id || "").trim(),
    label: String(payload?.label || "Astrata Desktop").trim(),
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + ttlMinutes * 60 * 1000).toISOString(),
  };
  await setPairingCode(env, pairing.code, pairing);
  return {
    status: "ok",
    pairing_code: pairing.code,
    profile_id: pairing.profile_id,
    user_id: pairing.user_id,
    device_id: pairing.device_id,
    label: pairing.label,
    expires_at: pairing.expires_at,
  };
}

function generatePairingCode() {
  const raw = crypto.randomUUID().replace(/-/g, "").toUpperCase();
  return `ASTRATA-${raw.slice(0, 4)}-${raw.slice(4, 8)}`;
}

function isAuthorized(request, env) {
  const expected = String(env.RELAY_SHARED_TOKEN || "");
  const actual = String(request.headers.get("authorization") || "");
  return !!expected && actual === `Bearer ${expected}`;
}

function extractBearerToken(value) {
  const header = String(value || "").trim();
  return header.toLowerCase().startsWith("bearer ") ? header.slice("bearer ".length).trim() : "";
}

function oauthChallenge(url) {
  return json(
    { error: "unauthorized", message: "OAuth authorization required." },
    401,
    {
      "www-authenticate": `Bearer resource_metadata="${url.origin}/.well-known/oauth-protected-resource", scope="relay:use"`,
    },
  );
}

function jsonRpcError(id, message) {
  return {
    jsonrpc: "2.0",
    id: id ?? null,
    error: { message },
  };
}

function json(payload, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(payload, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      ...corsHeaders(),
      ...extraHeaders,
    },
  });
}

function corsHeaders() {
  return {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-headers": "content-type, authorization, mcp-session-id",
  };
}

function oauthProtectedResourceMetadata(url) {
  return {
    resource: resourceIdentifier(url),
    authorization_servers: [resourceIdentifier(url)],
    scopes_supported: ["relay:use"],
    bearer_methods_supported: ["header"],
    resource_documentation: `${url.origin}/health`,
  };
}

function oauthAuthorizationServerMetadata(url) {
  return {
    issuer: resourceIdentifier(url),
    authorization_endpoint: `${url.origin}/oauth/authorize`,
    token_endpoint: `${url.origin}/oauth/token`,
    registration_endpoint: `${url.origin}/oauth/register`,
    response_types_supported: ["code"],
    grant_types_supported: ["authorization_code"],
    code_challenge_methods_supported: ["S256"],
    token_endpoint_auth_methods_supported: ["none"],
    scopes_supported: ["relay:use"],
  };
}

function resourceIdentifier(url) {
  return url.origin;
}

async function handleOAuthRegister(payload, env) {
  if (controlPlaneBaseUrl(env)) {
    const result = await callControlPlaneJson(env, "/oauth/register", {
      label: String(payload?.client_name || payload?.label || "ChatGPT Connector"),
      redirect_uris: Array.isArray(payload?.redirect_uris) ? payload.redirect_uris.map(String) : [],
      client_kind: String(payload?.client_kind || "chatgpt_connector"),
      metadata: { worker_registration: true },
    });
    if (!result.ok) return oauthTokenError("server_error", controlPlaneErrorMessage(result, "Unable to register OAuth client."));
    const client = result.payload?.client || {};
    return {
      client_id: String(client.client_id || ""),
      client_id_issued_at: Math.floor(Date.parse(client.created_at || new Date().toISOString()) / 1000),
      client_name: String(client.label || "ChatGPT Connector"),
      redirect_uris: Array.isArray(client.redirect_uris) ? client.redirect_uris.map(String) : [],
      grant_types: ["authorization_code"],
      response_types: ["code"],
      token_endpoint_auth_method: "none",
      created_at: client.created_at || new Date().toISOString(),
    };
  }
  const clientId = `astrata-client-${crypto.randomUUID()}`;
  const now = Math.floor(Date.now() / 1000);
  const client = {
    client_id: clientId,
    client_id_issued_at: now,
    client_name: String(payload?.client_name || "ChatGPT Connector"),
    redirect_uris: Array.isArray(payload?.redirect_uris) ? payload.redirect_uris.map(String) : [],
    grant_types: ["authorization_code"],
    response_types: ["code"],
    token_endpoint_auth_method: "none",
    created_at: new Date().toISOString(),
  };
  await setOAuthClient(env, clientId, client);
  return client;
}

function oauthAuthorizationPage(url, error = "", details = {}) {
  const params = Object.fromEntries(url.searchParams.entries());
  if (!params.resource) params.resource = resourceIdentifier(url);
  if (!params.scope) params.scope = "relay:use";
  const hidden = Object.entries(params)
    .map(([key, value]) => `<input type="hidden" name="${escapeHtml(key)}" value="${escapeHtml(value)}">`)
    .join("\n");
  const diagnosticRows = Object.entries(details || {})
    .filter(([, value]) => String(value || "").trim())
    .map(([key, value]) => `<p class="diagnostic"><strong>${escapeHtml(key)}:</strong> <code>${escapeHtml(String(value))}</code></p>`)
    .join("\n");
  return html(`<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Authorize Astrata Relay</title>
    <style>
      body { font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; min-height: 100vh; display: grid; place-items: center; background: #101820; color: #f8f3e8; }
      main { width: min(520px, calc(100vw - 32px)); background: #172531; border: 1px solid #355063; border-radius: 24px; padding: 32px; box-shadow: 0 24px 80px rgb(0 0 0 / 35%); }
      h1 { margin: 0 0 12px; font-size: 1.6rem; }
      p { line-height: 1.5; color: #d9ccb8; font-size: 14px;}
      label { display: grid; gap: 8px; margin: 16px 0; color: #fff7ea; }
      input { font: inherit; border: 1px solid #4b6c80; border-radius: 12px; padding: 12px 14px; background: #0d151c; color: #fff7ea; }
      button { font: inherit; border: 0; border-radius: 999px; padding: 12px 18px; background: #f6b04a; color: #211505; cursor: pointer; font-weight: 700; width: 100%; margin-top: 10px;}
      .error { color: #ffb4a8; font-weight: 700; background: #401010; padding: 12px; border-radius: 8px; font-size: 14px;}
      code { color: #ffd18a; }
      .diagnostic { overflow-wrap: anywhere; color: #a6bac8; font-size: 12px; margin: 8px 0; }
      .disclaimer { font-size: 0.8rem; color: #849cae; margin-top: 20px;}
    </style>
  </head>
  <body>
    <main>
      <h1>Sign In to Astrata</h1>
      <p>Connect ChatGPT to your remote Astrata instance.</p>
      ${error ? `<div class="error">${escapeHtml(error)}</div>` : ""}
      ${diagnosticRows}
      <form method="post" action="/oauth/authorize">
        ${hidden}
        <label>
          Email address
          <input type="email" name="email" required autofocus>
        </label>
        <label>
          Password
          <input type="password" name="password" required>
        </label>
        <label>
          Invite Code <span style="font-size: 12px; color: #849cae;">(Required if this is a new account)</span>
          <input type="text" name="invite_code" autocomplete="off">
        </label>
        <button type="submit">Sign In & Authorize</button>
      </form>
      <p class="disclaimer">Requested scope: <code>${escapeHtml(params.scope || "relay:use")}</code></p>
    </main>
  </body>
</html>`);
}

function publicSignInPage(url, error = "") {
  const requestId = String(url.searchParams.get("request_id") || "").trim();
  return html(`<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Sign In to Astrata</title>
    <style>
      body { font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; min-height: 100vh; display: grid; place-items: center; background: #101820; color: #f8f3e8; }
      main { width: min(560px, calc(100vw - 32px)); background: #172531; border: 1px solid #355063; border-radius: 24px; padding: 32px; box-shadow: 0 24px 80px rgb(0 0 0 / 35%); }
      h1 { margin: 0 0 12px; font-size: 1.6rem; }
      p { line-height: 1.5; color: #d9ccb8; font-size: 14px;}
      label { display: grid; gap: 8px; margin: 16px 0; color: #fff7ea; }
      input { font: inherit; border: 1px solid #4b6c80; border-radius: 12px; padding: 12px 14px; background: #0d151c; color: #fff7ea; }
      button { font: inherit; border: 0; border-radius: 999px; padding: 12px 18px; background: #f6b04a; color: #211505; cursor: pointer; font-weight: 700; width: 100%; margin-top: 10px;}
      .error { color: #ffb4a8; font-weight: 700; background: #401010; padding: 12px; border-radius: 8px; font-size: 14px;}
      .small { color: #849cae; font-size: 12px; }
    </style>
  </head>
  <body>
    <main>
      <h1>Sign In to Astrata</h1>
      <p>Sign in to unlock account-bound Astrata tools in the GPT. After this step, return to ChatGPT and say you are done.</p>
      ${error ? `<div class="error">${escapeHtml(error)}</div>` : ""}
      <form method="post" action="/gpt/public/signin">
        <input type="hidden" name="request_id" value="${escapeHtml(requestId)}">
        <label>
          Email address
          <input type="email" name="email" required autofocus>
        </label>
        <label>
          Password
          <input type="password" name="password" required>
        </label>
        <label>
          Invite Code <span class="small">(Required if this is a new account)</span>
          <input type="text" name="invite_code" autocomplete="off">
        </label>
        <button type="submit">Sign In</button>
      </form>
    </main>
  </body>
</html>`);
}

function publicSignInCompletePage(record) {
  return html(`<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Astrata Sign-In Complete</title>
    <style>
      html, body { margin: 0; padding: 0; background: white; color: #111; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
      main { max-width: 720px; margin: 0 auto; padding: 24px 18px 48px; }
      h1 { margin: 0 0 12px; font-size: 28px; line-height: 1.15; }
      p { line-height: 1.5; font-size: 16px; margin: 12px 0; }
      .code { display: block; width: 100%; box-sizing: border-box; margin: 18px 0; padding: 16px; border: 2px solid #111; border-radius: 8px; background: #f5f5f5; color: #111; font: 700 22px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; overflow-wrap: anywhere; user-select: all; }
      .small { color: #555; font-size: 13px; }
    </style>
  </head>
  <body>
    <main>
      <h1>Sign-In Complete</h1>
      <p>Return to ChatGPT and say you finished signing in.</p>
      <p>If ChatGPT asks for a completion code, use this fallback code:</p>
      <div class="code">${escapeHtml(record.code)}</div>
      <p>ChatGPT should call <strong>complete_sign_in</strong>${record.request_id ? ` with request id <strong>${escapeHtml(record.request_id)}</strong>` : " with that code"}.</p>
      <p class="small">Expires at ${escapeHtml(record.expires_at)}. The code can be used once.</p>
    </main>
  </body>
</html>`);
}

async function handlePublicSignIn(formData, env, url) {
  const email = String(formData.get("email") || "").trim().toLowerCase();
  const password = String(formData.get("password") || "");
  const inviteCode = String(formData.get("invite_code") || "").trim();
  const requestId = String(formData.get("request_id") || "").trim();
  if (!email || !password) return publicSignInPage(url, "Email and password are required.");
  let user = await getUserByEmail(env, email);
  if (!user) {
    if (!inviteCode) return publicSignInPage(url, "This email is not registered. An invite code is required to create a new account.");
    const invite = await validateInviteCode(env, inviteCode);
    if (!invite) return publicSignInPage(url, "Invalid or expired invite code.");
    const now = new Date().toISOString();
    user = {
      user_id: crypto.randomUUID(),
      email,
      password_hash: await hashPassword(password),
      display_name: email.split("@")[0],
      status: "active",
      invite_code_used: inviteCode,
      default_profile_id: crypto.randomUUID(),
      created_at: now,
      updated_at: now,
    };
    await createUser(env, user);
    await consumeInviteCode(env, invite);
  } else {
    if (!user.password_hash) return publicSignInPage(url, "Account missing password hash.");
    if (!(await verifyPassword(password, user.password_hash))) return publicSignInPage(url, "Incorrect password.");
  }
  const sessionToken = `astrata-public-session-${crypto.randomUUID()}`;
  const expiresAt = new Date(Date.now() + 12 * 60 * 60 * 1000).toISOString();
  await setOAuthToken(env, sessionToken, {
    access_token: sessionToken,
    client_id: "public-tool-flow",
    profile_id: String(user.default_profile_id || "").trim() || String(env.RELAY_DEFAULT_PROFILE_ID || "").trim(),
    user_id: String(user.user_id || "").trim(),
    device_id: "",
    resource: resourceIdentifier(url),
    scope: "relay:use",
    expires_at: expiresAt,
    created_at: new Date().toISOString(),
  });
  const completion = {
    code: generateCompletionCode(),
    request_id: requestId,
    session_token: sessionToken,
    user_id: user.user_id,
    email: user.email,
    profile_id: String(user.default_profile_id || "").trim() || String(env.RELAY_DEFAULT_PROFILE_ID || "").trim(),
    expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
    created_at: new Date().toISOString(),
  };
  await setPublicLoginCode(env, completion.code, completion);
  if (requestId) {
    const request = await getPublicLoginRequest(env, requestId);
    if (request && Date.parse(request.expires_at || "") > Date.now()) {
      await setPublicLoginRequest(env, requestId, {
        ...request,
        status: "complete",
        session_token: sessionToken,
        session_expires_at: expiresAt,
        user_id: user.user_id,
        email: user.email,
        profile_id: completion.profile_id,
        completed_at: new Date().toISOString(),
      });
    }
  }
  return publicSignInCompletePage(completion);
}

function generateCompletionCode() {
  const raw = crypto.randomUUID().replace(/-/g, "").toUpperCase();
  return `ASTRATA-LOGIN-${raw.slice(0, 4)}-${raw.slice(4, 8)}`;
}

function isRegisteredRedirectUri(client, redirectUri) {
  const registered = Array.isArray(client?.redirect_uris) ? client.redirect_uris.map(String) : [];
  if (registered.includes(redirectUri)) return true;
  const requested = parseChatGptCallback(redirectUri);
  if (!requested) return false;
  return registered.some((candidate) => {
    const parsed = parseChatGptCallback(candidate);
    return parsed && parsed.host === requested.host && parsed.gpt_core_id === requested.gpt_core_id;
  });
}

function parseChatGptCallback(value) {
  let parsed;
  try {
    parsed = new URL(String(value || ""));
  } catch (error) {
    return null;
  }
  if (!["chatgpt.com", "chat.openai.com"].includes(parsed.hostname)) return null;
  const match = parsed.pathname.match(/^\/aip\/(g-[A-Za-z0-9]+)(?:-[^/]+)?\/oauth\/callback\/?$/);
  if (!match) return null;
  return { host: parsed.hostname, gpt_core_id: match[1] };
}

async function handleOAuthAuthorize(formData, env) {
  const email = String(formData.get("email") || "").trim().toLowerCase();
  const password = String(formData.get("password") || "");
  const inviteCode = String(formData.get("invite_code") || "").trim();
  const usingControlPlane = !!controlPlaneBaseUrl(env);
  
  const requestUrl = new URL("https://relay.local/oauth/authorize");
  for (const [key, value] of formData.entries()) {
    if (key !== "email" && key !== "password" && key !== "invite_code") requestUrl.searchParams.set(key, String(value));
  }

  const clientId = String(formData.get("client_id") || "");
  const redirectUri = String(formData.get("redirect_uri") || "");
  const codeChallenge = String(formData.get("code_challenge") || "");
  const codeChallengeMethod = String(formData.get("code_challenge_method") || "");
  const state = String(formData.get("state") || "");
  const resource = String(formData.get("resource") || resourceIdentifier(new URL(requestUrl)));
  const scope = String(formData.get("scope") || "relay:use");
  if (codeChallenge && codeChallengeMethod !== "S256") {
    return oauthAuthorizationPage(requestUrl, "PKCE S256 is required when a code challenge is provided.", {
      client_id: clientId,
      redirect_uri: redirectUri,
      code_challenge_method: codeChallengeMethod,
    });
  }

  if (usingControlPlane) {
    const result = await callControlPlaneJson(env, "/oauth/authorize", {
      client_id: clientId,
      email,
      redirect_uri: redirectUri,
      scope: scope.split(/\s+/).filter(Boolean),
      code_challenge: codeChallenge,
      code_challenge_method: codeChallengeMethod,
    });
    if (!result.ok) {
      return oauthAuthorizationPage(requestUrl, controlPlaneErrorMessage(result, "Unable to authorize Astrata connector."), {
        client_id: clientId,
        redirect_uri: redirectUri,
        email,
      });
    }
    const issuedCode = String(result.payload?.authorization_code?.code || "");
    if (!issuedCode) {
      return oauthAuthorizationPage(requestUrl, "Astrata Web did not return an authorization code.", {
        client_id: clientId,
        redirect_uri: redirectUri,
        email,
      });
    }
    const redirect = new URL(redirectUri);
    redirect.searchParams.set("code", issuedCode);
    if (state) redirect.searchParams.set("state", state);
    return Response.redirect(redirect.toString(), 302);
  }

  let user = await getUserByEmail(env, email);
  
  if (!user) {
    if (!inviteCode) return oauthAuthorizationPage(requestUrl, "This email is not registered. An invite code is required to create a new account.");
    
    const invite = await validateInviteCode(env, inviteCode);
    if (!invite) return oauthAuthorizationPage(requestUrl, "Invalid or expired invite code.");
    
    const passwordHash = await hashPassword(password);
    user = {
      user_id: crypto.randomUUID(),
      email,
      password_hash: passwordHash,
      display_name: email.split("@")[0],
      status: "active",
      invite_code_used: inviteCode,
      default_profile_id: crypto.randomUUID(),
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString()
    };
    await createUser(env, user);
    await consumeInviteCode(env, invite);
  } else {
    if (!user.password_hash) return oauthAuthorizationPage(requestUrl, "Account missing password hash.");
    const valid = await verifyPassword(password, user.password_hash);
    if (!valid) return oauthAuthorizationPage(requestUrl, "Incorrect password.");
  }

  const client = await getOAuthClient(env, clientId);
  if (!client) return oauthAuthorizationPage(requestUrl, "Unknown OAuth client.", { client_id: clientId, redirect_uri: redirectUri });
  if (!isRegisteredRedirectUri(client, redirectUri)) {
    return oauthAuthorizationPage(requestUrl, "Redirect URI is not registered for this client.", {
      client_id: clientId,
      redirect_uri: redirectUri,
      registered_redirect_uris: (client.redirect_uris || []).join(", "),
    });
  }

  const code = `astrata-code-${crypto.randomUUID()}`;
  await setOAuthCode(env, code, {
    code,
    client_id: clientId,
    redirect_uri: redirectUri,
    code_challenge: codeChallenge,
    code_challenge_method: codeChallengeMethod,
    profile_id: user.default_profile_id,
    user_id: user.user_id,
    device_id: "",
    resource,
    scope,
    expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
    created_at: new Date().toISOString(),
  });

  const redirect = new URL(redirectUri);
  redirect.searchParams.set("code", code);
  if (state) redirect.searchParams.set("state", state);
  return Response.redirect(redirect.toString(), 302);
}

async function handleOAuthToken(formData, env, url) {
  const grantType = String(formData.get("grant_type") || "");
  const code = String(formData.get("code") || "");
  const clientId = String(formData.get("client_id") || "");
  const redirectUri = String(formData.get("redirect_uri") || "");
  const codeVerifier = String(formData.get("code_verifier") || "");
  const requestedResource = String(formData.get("resource") || "");
  if (grantType !== "authorization_code") return oauthTokenError("unsupported_grant_type", "Only authorization_code is supported.");
  if (controlPlaneBaseUrl(env)) {
    const result = await callControlPlaneJson(env, "/oauth/token", {
      client_id: clientId,
      code,
      redirect_uri: redirectUri,
      code_verifier: codeVerifier,
    });
    if (!result.ok) return oauthTokenError("invalid_grant", controlPlaneErrorMessage(result, "Authorization code exchange failed."));
    return json({
      access_token: String(result.payload?.access_token || ""),
      token_type: String(result.payload?.token_type || "Bearer"),
      expires_in: Number(result.payload?.expires_in || 3600),
      scope: Array.isArray(result.payload?.scope) ? result.payload.scope.join(" ") : String(result.payload?.scope || "relay:use"),
    });
  }

  const codeRecord = await getOAuthCode(env, code);
  if (!codeRecord) return oauthTokenError("invalid_grant", "Unknown authorization code.");
  const resource = requestedResource || String(codeRecord.resource || "") || resourceIdentifier(url);
  if (Date.parse(codeRecord.expires_at || "") <= Date.now()) return oauthTokenError("invalid_grant", "Authorization code expired.");
  if (codeRecord.client_id !== clientId) {
    return oauthTokenError("invalid_grant", "Authorization code client mismatch.");
  }
  if (!oauthRedirectUrisMatch(String(codeRecord.redirect_uri || ""), redirectUri)) {
    return oauthTokenError("invalid_grant", `Authorization code redirect URI mismatch: expected ${codeRecord.redirect_uri || "(none)"}, received ${redirectUri || "(none)"}.`);
  }
  if (codeRecord.resource !== resource) return oauthTokenError("invalid_target", "Resource mismatch.");
  const challenge = await pkceChallenge(codeVerifier);
  if (codeRecord.code_challenge) {
    if (!codeVerifier) return oauthTokenError("invalid_grant", "PKCE verifier is required for this authorization code.");
    if (challenge !== codeRecord.code_challenge) return oauthTokenError("invalid_grant", "PKCE verifier mismatch.");
  }

  const accessToken = `astrata-token-${crypto.randomUUID()}`;
  const expiresIn = 12 * 60 * 60;
  await setOAuthToken(env, accessToken, {
    access_token: accessToken,
    client_id: clientId,
    profile_id: String(codeRecord.profile_id || "").trim(),
    user_id: String(codeRecord.user_id || "").trim(),
    device_id: String(codeRecord.device_id || "").trim(),
    resource,
    scope: codeRecord.scope,
    expires_at: new Date(Date.now() + expiresIn * 1000).toISOString(),
    created_at: new Date().toISOString(),
  });
  await deleteOAuthCode(env, code);
  return json({
    access_token: accessToken,
    token_type: "Bearer",
    expires_in: expiresIn,
    scope: codeRecord.scope,
  });
}

function oauthTokenError(error, description) {
  return json({ error, error_description: description }, 400);
}

function oauthRedirectUrisMatch(expected, actual) {
  if (String(expected || "") === String(actual || "")) return true;
  const expectedParsed = parseChatGptCallback(expected);
  const actualParsed = parseChatGptCallback(actual);
  return Boolean(
    expectedParsed
    && actualParsed
    && expectedParsed.host === actualParsed.host
    && expectedParsed.gpt_core_id === actualParsed.gpt_core_id
  );
}

async function pkceChallenge(verifier) {
  const bytes = new TextEncoder().encode(verifier);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return base64UrlEncode(new Uint8Array(digest));
}

function base64UrlEncode(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function html(body, status = 200) {
  return new Response(body, {
    status,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
      ...corsHeaders(),
    },
  });
}

function privacyPolicyPage(url) {
  return html(`<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Astrata Dev Relay Privacy Policy</title>
    <style>
      body { font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; background: #fbf7ee; color: #17212b; }
      main { max-width: 760px; margin: 0 auto; padding: 48px 24px 72px; }
      h1 { font-size: clamp(2rem, 4vw, 3.25rem); line-height: 1; margin: 0 0 16px; }
      h2 { margin-top: 32px; }
      p, li { line-height: 1.6; }
      code { background: #efe4d2; border-radius: 6px; padding: 2px 5px; }
    </style>
  </head>
  <body>
    <main>
      <h1>Astrata Dev Relay Privacy Policy</h1>
      <p><strong>Effective date:</strong> April 11, 2026</p>
      <p>This policy covers the Astrata development relay hosted at <code>${escapeHtml(url.origin)}</code>. The relay exists to let a remote client, such as a Custom GPT, send connector-safe requests to a local Astrata instance and poll for responses.</p>

      <h2>What the relay processes</h2>
      <p>The relay may process request metadata, tool names, tool arguments intentionally sent through the connector, request/session identifiers, timestamps, and connector-safe responses produced by the local Astrata instance.</p>

      <h2>What should not be sent</h2>
      <p>Do not send secrets, passwords, API keys, private documents, PII, local-only memory, or enclave-only data through this development relay unless you have deliberately approved that disclosure. Astrata's intended posture is local-first: sensitive data should remain on the user's machine unless a local process has redacted and approved a safe representation.</p>

      <h2>Storage and retention</h2>
      <p>During development, relay state may be stored in Cloudflare Worker KV or in-memory Worker state, including queued requests, acknowledgements, session messages, and tool results. This is not yet a production data-retention system. The operator may delete or rotate relay state during development.</p>

      <h2>Authentication</h2>
      <p>The current bridge supports a development token and experimental OAuth-style flow. This is intended for single-user development, not broad public distribution. Production OAuth and account controls should replace it before wider release.</p>

      <h2>Contact</h2>
      <p>For now, contact the Astrata operator who provided this connector URL.</p>
    </main>
  </body>
</html>`);
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function loadDebugState(env) {
  if (env.RELAY_STATE) {
    const list = await env.RELAY_STATE.list({ prefix: "relay:" });
    const entries = [];
    for (const key of list.keys) {
      entries.push({
        key: key.name,
        value: await env.RELAY_STATE.get(key.name, "json"),
      });
    }
    return { storage: "kv", entries };
  }
  return {
    storage: "memory",
    advertisements: Object.fromEntries(MEMORY_STATE.advertisements.entries()),
    pendingQueues: Object.fromEntries(MEMORY_STATE.pendingRequests.entries()),
    ackedRequests: Object.fromEntries(MEMORY_STATE.ackedRequests.entries()),
    results: Object.fromEntries(MEMORY_STATE.results.entries()),
    sessions: Object.fromEntries(MEMORY_STATE.sessions.entries()),
    feedback: Object.fromEntries(MEMORY_STATE.feedback.entries()),
    pairingCodes: Object.fromEntries(MEMORY_STATE.pairingCodes.entries()),
  };
}

async function lookupResult(env, requestId) {
  if (!requestId) return { found: false };
  const resultCollections = env.RELAY_STATE
    ? await listEntries(env, "relay:")
    : Object.entries(Object.fromEntries(MEMORY_STATE.results.entries())).map(([key, value]) => ({ key, value }));
  for (const entry of resultCollections) {
    const values = Array.isArray(entry.value) ? entry.value : [];
    for (const item of values) {
      if (String(item.request_id || "") === String(requestId) && Object.prototype.hasOwnProperty.call(item, "result")) {
        return { found: true, request_id: requestId, result: item };
      }
    }
  }
  const collections = env.RELAY_STATE
    ? await listEntries(env, "relay:")
    : Object.entries(Object.fromEntries(MEMORY_STATE.results.entries())).map(([key, value]) => ({ key, value }));
  for (const entry of collections) {
    const values = Array.isArray(entry.value) ? entry.value : [];
    for (const item of values) {
      if (String(item.request_id || "") === String(requestId)) {
        return { found: true, request_id: requestId, result: item };
      }
    }
  }
  return { found: false, request_id: requestId };
}

async function listEntries(env, prefix) {
  const list = await env.RELAY_STATE.list({ prefix });
  const entries = [];
  for (const key of list.keys) {
    entries.push({
      key: key.name,
      value: await env.RELAY_STATE.get(key.name, "json"),
    });
  }
  return entries;
}

async function getAdvertisement(env, profileId) {
  return getState(env, `relay:${profileId}:advertisement`, MEMORY_STATE.advertisements, null);
}

async function setAdvertisement(env, profileId, payload) {
  return setState(env, `relay:${profileId}:advertisement`, MEMORY_STATE.advertisements, payload);
}

async function maybeSetAdvertisement(env, profileId, payload) {
  const current = await getAdvertisement(env, profileId);
  const now = Date.now();
  const minSeconds = Number(env.RELAY_HEARTBEAT_WRITE_MIN_SECONDS || 60);
  const fingerprint = stableJson(payload?.advertisement || payload || {});
  const currentFingerprint = String(current?._fingerprint || "");
  const lastStoredAt = Date.parse(String(current?._stored_at || "")) || 0;
  if (currentFingerprint === fingerprint && now - lastStoredAt < minSeconds * 1000) {
    return {
      status: "skipped",
      reason: "unchanged_within_write_window",
      min_seconds: minSeconds,
      last_stored_at: current?._stored_at || "",
    };
  }
  await setAdvertisement(env, profileId, {
    ...payload,
    _fingerprint: fingerprint,
    _stored_at: new Date(now).toISOString(),
  });
  return {
    status: "stored",
    reason: currentFingerprint === fingerprint ? "write_window_elapsed" : "changed",
    min_seconds: minSeconds,
  };
}

async function getPendingQueue(env, profileId) {
  return getState(env, `relay:${profileId}:pending`, MEMORY_STATE.pendingRequests, []);
}

async function setPendingQueue(env, profileId, payload) {
  return setState(env, `relay:${profileId}:pending`, MEMORY_STATE.pendingRequests, payload);
}

async function getAckedRequests(env, profileId) {
  return getState(env, `relay:${profileId}:acked`, MEMORY_STATE.ackedRequests, []);
}

async function setAckedRequests(env, profileId, payload) {
  return setState(env, `relay:${profileId}:acked`, MEMORY_STATE.ackedRequests, payload);
}

async function getResults(env, profileId) {
  return getState(env, `relay:${profileId}:results`, MEMORY_STATE.results, []);
}

async function setResults(env, profileId, payload) {
  return setState(env, `relay:${profileId}:results`, MEMORY_STATE.results, payload);
}

async function getAdapterReceipt(env, key) {
  return getState(env, key, MEMORY_STATE.adapterReceipts, null);
}

async function setAdapterReceipt(env, key, payload) {
  return setState(env, key, MEMORY_STATE.adapterReceipts, payload);
}

async function getFeedback(env, profileId) {
  return getState(env, `relay:${profileId}:feedback`, MEMORY_STATE.feedback, []);
}

async function setFeedback(env, profileId, payload) {
  return setState(env, `relay:${profileId}:feedback`, MEMORY_STATE.feedback, payload);
}

async function getPairingCode(env, code) {
  return getState(env, `pairing:${String(code || "").trim().toUpperCase()}`, MEMORY_STATE.pairingCodes, null);
}

async function setPairingCode(env, code, payload) {
  return setState(env, `pairing:${String(code || "").trim().toUpperCase()}`, MEMORY_STATE.pairingCodes, payload);
}

async function deletePairingCode(env, code) {
  return deleteState(env, `pairing:${String(code || "").trim().toUpperCase()}`, MEMORY_STATE.pairingCodes);
}

async function getPublicLoginCode(env, code) {
  return getState(env, `public:signin:${String(code || "").trim().toUpperCase()}`, MEMORY_STATE.publicLoginCodes, null);
}

async function setPublicLoginCode(env, code, payload) {
  return setState(env, `public:signin:${String(code || "").trim().toUpperCase()}`, MEMORY_STATE.publicLoginCodes, payload);
}

async function deletePublicLoginCode(env, code) {
  return deleteState(env, `public:signin:${String(code || "").trim().toUpperCase()}`, MEMORY_STATE.publicLoginCodes);
}

async function getPublicLoginRequest(env, requestId) {
  return getState(env, `public:signin-request:${String(requestId || "").trim()}`, MEMORY_STATE.publicLoginCodes, null);
}

async function setPublicLoginRequest(env, requestId, payload) {
  return setState(env, `public:signin-request:${String(requestId || "").trim()}`, MEMORY_STATE.publicLoginCodes, payload);
}

async function deletePublicLoginRequest(env, requestId) {
  return deleteState(env, `public:signin-request:${String(requestId || "").trim()}`, MEMORY_STATE.publicLoginCodes);
}

async function getOAuthClient(env, clientId) {
  if (env.astrata_auth_db) {
    const client = await env.astrata_auth_db.prepare("SELECT * FROM oauth_clients WHERE client_id = ?").bind(clientId).first();
    if (client) {
      if (typeof client.redirect_uris === "string") {
        try { client.redirect_uris = JSON.parse(client.redirect_uris); } catch (e) { client.redirect_uris = [client.redirect_uris]; }
      }
      return client;
    }
    return null;
  }
  return getState(env, `oauth:client:${clientId}`, MEMORY_STATE.oauthClients, null);
}

async function setOAuthClient(env, clientId, payload) {
  if (env.astrata_auth_db) {
    await env.astrata_auth_db.prepare(`
      INSERT INTO oauth_clients (client_id, client_name, redirect_uris, token_endpoint_auth_method, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(client_id) DO UPDATE SET
        client_name = excluded.client_name,
        redirect_uris = excluded.redirect_uris,
        token_endpoint_auth_method = excluded.token_endpoint_auth_method,
        updated_at = excluded.updated_at
    `).bind(
      clientId,
      String(payload.client_name || "ChatGPT Connector"),
      JSON.stringify(Array.isArray(payload.redirect_uris) ? payload.redirect_uris.map(String) : []),
      String(payload.token_endpoint_auth_method || "none"),
      String(payload.created_at || new Date().toISOString()),
      new Date().toISOString(),
    ).run();
    return payload;
  }
  return setState(env, `oauth:client:${clientId}`, MEMORY_STATE.oauthClients, payload);
}

async function getOAuthCode(env, code) {
  if (env.astrata_auth_db) {
    return await env.astrata_auth_db.prepare("SELECT * FROM oauth_authorization_codes WHERE code_id = ?").bind(code).first();
  }
  return getState(env, `oauth:code:${code}`, MEMORY_STATE.oauthCodes, null);
}

async function setOAuthCode(env, code, payload) {
  if (env.astrata_auth_db) {
    await env.astrata_auth_db.prepare(`
      INSERT INTO oauth_authorization_codes (
        code_id,
        client_id,
        redirect_uri,
        code_challenge,
        code_challenge_method,
        user_id,
        profile_id,
        device_id,
        resource,
        scope,
        expires_at,
        created_at,
        updated_at
      )
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).bind(
      payload.code,
      payload.client_id,
      payload.redirect_uri || "",
      payload.code_challenge || "",
      payload.code_challenge_method || "",
      payload.user_id,
      payload.profile_id,
      payload.device_id || null,
      payload.resource || "",
      payload.scope || "relay:use",
      payload.expires_at,
      payload.created_at,
      payload.created_at
    ).run();
    return payload;
  }
  return setState(env, `oauth:code:${code}`, MEMORY_STATE.oauthCodes, payload);
}

async function deleteOAuthCode(env, code) {
  if (env.astrata_auth_db) {
    await env.astrata_auth_db.prepare("DELETE FROM oauth_authorization_codes WHERE code_id = ?").bind(code).run();
    return;
  }
  return deleteState(env, `oauth:code:${code}`, MEMORY_STATE.oauthCodes);
}

async function getOAuthToken(env, token) {
  if (env.astrata_auth_db) {
    return await env.astrata_auth_db.prepare("SELECT * FROM oauth_access_tokens WHERE token_id = ?").bind(token).first();
  }
  return getState(env, `oauth:token:${token}`, MEMORY_STATE.oauthTokens, null);
}

async function getRelayProfileRecord(env, profileId) {
  const id = String(profileId || "").trim();
  if (!id || !env.astrata_auth_db) return null;
  return await env.astrata_auth_db.prepare("SELECT * FROM relay_profiles WHERE profile_id = ?").bind(id).first();
}

async function getActiveDeviceLinks(env, profileId) {
  const id = String(profileId || "").trim();
  if (!id || !env.astrata_auth_db) return [];
  const result = await env.astrata_auth_db.prepare(`
    SELECT dl.*, d.label AS device_label, d.platform AS device_platform, d.status AS device_status, d.last_seen_at AS device_last_seen_at
    FROM device_links dl
    LEFT JOIN devices d ON d.device_id = dl.device_id
    WHERE dl.profile_id = ? AND dl.status = 'active'
  `).bind(id).all();
  return Array.isArray(result?.results) ? result.results : [];
}

async function setOAuthToken(env, token, payload) {
  if (env.astrata_auth_db) {
    await env.astrata_auth_db.prepare(`
      INSERT INTO oauth_access_tokens (token_id, client_id, user_id, profile_id, device_id, status, expires_at, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).bind(
      payload.access_token,
      payload.client_id,
      payload.user_id,
      payload.profile_id,
      payload.device_id || null,
      "active",
      payload.expires_at,
      payload.created_at,
      payload.created_at
    ).run();
    return payload;
  }
  return setState(env, `oauth:token:${token}`, MEMORY_STATE.oauthTokens, payload);
}

async function appendSessionMessage(env, payload, sender) {
  const profileId = String(payload?.profile_id || "").trim();
  const sessionId = String(payload?.session_id || `session:${profileId}`).trim();
  if (!profileId || !sessionId) return { error: "profile_id_and_session_id_required" };
  const session = await getSession(env, profileId, sessionId);
  const now = new Date().toISOString();
  const message = {
    message_id: crypto.randomUUID(),
    request_id: String(payload?.request_id || ""),
    sender,
    kind: String(payload?.kind || "message"),
    content: payload?.content || {},
    created_at: now,
  };
  session.messages.push(message);
  session.updated_at = now;
  if (sender === "remote") session.remote_last_seen_at = now;
  if (sender === "local") session.local_last_seen_at = now;
  await setSession(env, profileId, sessionId, session);
  return { ok: true, session, message };
}

async function readSession(env, { profileId, sessionId, actor }) {
  const session = await getSession(env, profileId, sessionId);
  const now = new Date().toISOString();
  if (actor === "local") session.local_last_seen_at = now;
  if (actor === "remote") session.remote_last_seen_at = now;
  await setSession(env, profileId, sessionId, session);
  return { ok: true, session };
}

async function markSessionsSeen(env, profileId, actor) {
  const entries = await listEntries(env, `relay:${profileId}:session:`);
  const now = new Date().toISOString();
  for (const entry of entries) {
    const session = entry.value || {};
    if (actor === "local") session.local_last_seen_at = now;
    if (actor === "remote") session.remote_last_seen_at = now;
    session.updated_at = now;
    await setState(env, entry.key, MEMORY_STATE.sessions, session);
  }
}

async function getSession(env, profileId, sessionId) {
  return getState(env, sessionKey(profileId, sessionId), MEMORY_STATE.sessions, {
    profile_id: profileId,
    session_id: sessionId,
    messages: [],
    remote_last_seen_at: "",
    local_last_seen_at: "",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  });
}

async function setSession(env, profileId, sessionId, payload) {
  return setState(env, sessionKey(profileId, sessionId), MEMORY_STATE.sessions, payload);
}

function sessionKey(profileId, sessionId) {
  return `relay:${profileId}:session:${sessionId}`;
}

async function getState(env, key, memoryMap, fallback) {
  if (env.RELAY_STATE) {
    const value = await env.RELAY_STATE.get(key, "json");
    return value ?? cloneJson(fallback);
  }
  return memoryMap.has(key) ? cloneJson(memoryMap.get(key)) : cloneJson(fallback);
}

async function setState(env, key, memoryMap, payload) {
  if (env.RELAY_STATE) {
    try {
      await env.RELAY_STATE.put(key, JSON.stringify(payload));
      return payload;
    } catch (error) {
      console.warn(`KV write failed for ${key}; falling back to isolate memory. ${String(error && error.message ? error.message : error)}`);
      memoryMap.set(key, cloneJson(payload));
      if (payload && typeof payload === "object" && !Array.isArray(payload)) {
        return {
          ...payload,
          _storage_warning: "kv_write_failed_fell_back_to_isolate_memory",
        };
      }
      return payload;
    }
  }
  memoryMap.set(key, cloneJson(payload));
  return payload;
}

async function deleteState(env, key, memoryMap) {
  if (env.RELAY_STATE) {
    await env.RELAY_STATE.delete(key);
    return;
  }
  memoryMap.delete(key);
}

function cloneJson(value) {
  if (value === undefined) return undefined;
  return JSON.parse(JSON.stringify(value));
}

function stableJson(value) {
  return JSON.stringify(sortJson(value));
}

function sortJson(value) {
  if (Array.isArray(value)) return value.map(sortJson);
  if (value && typeof value === "object") {
    const sorted = {};
    for (const key of Object.keys(value).sort()) {
      if (key === "last_heartbeat_at" || key === "updated_at" || key === "checked_at") continue;
      sorted[key] = sortJson(value[key]);
    }
    return sorted;
  }
  return value;
}

// --- Auth and DB Helpers ---

async function getUserByEmail(env, email) {
  if (!env.astrata_auth_db) return null;
  return await env.astrata_auth_db.prepare("SELECT * FROM users WHERE email = ?").bind(email).first();
}

async function createUser(env, user) {
  if (!env.astrata_auth_db) return;
  await env.astrata_auth_db.prepare(`
    INSERT INTO users (user_id, email, password_hash, display_name, status, invite_code_used, default_profile_id, created_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).bind(
    user.user_id, user.email, user.password_hash, user.display_name, user.status, user.invite_code_used, user.default_profile_id, user.created_at, user.updated_at
  ).run();
}

async function validateInviteCode(env, code) {
  if (!env.astrata_auth_db) return null;
  const invite = await env.astrata_auth_db.prepare("SELECT * FROM invite_codes WHERE code = ?").bind(code).first();
  if (!invite) return null;
  if (invite.status !== "active") return null;
  if (invite.max_uses !== null && invite.current_uses >= invite.max_uses) return null;
  return invite;
}

async function consumeInviteCode(env, invite) {
  if (!env.astrata_auth_db) return;
  const newUses = invite.current_uses + 1;
  let newStatus = invite.status;
  if (invite.type === "one_time" || (invite.max_uses !== null && newUses >= invite.max_uses)) {
    newStatus = "exhausted";
  }
  await env.astrata_auth_db.prepare("UPDATE invite_codes SET current_uses = ?, status = ?, updated_at = ? WHERE code_id = ?")
    .bind(newUses, newStatus, new Date().toISOString(), invite.code_id).run();
}

async function hashPassword(password, saltHex = null) {
  const enc = new TextEncoder();
  const salt = saltHex ? hexToBuffer(saltHex) : crypto.getRandomValues(new Uint8Array(16));
  const keyMaterial = await crypto.subtle.importKey("raw", enc.encode(password), { name: "PBKDF2" }, false, ["deriveBits"]);
  const hashBuffer = await crypto.subtle.deriveBits(
    { name: "PBKDF2", salt: salt, iterations: 100000, hash: "SHA-256" },
    keyMaterial,
    256
  );
  return bufferToHex(salt) + ":" + bufferToHex(new Uint8Array(hashBuffer));
}

async function verifyPassword(password, hashStr) {
  const [saltHex] = hashStr.split(":");
  const computedHashStr = await hashPassword(password, saltHex);
  return computedHashStr === hashStr;
}

function bufferToHex(buffer) {
  return Array.from(buffer).map(b => b.toString(16).padStart(2, '0')).join('');
}

function hexToBuffer(hex) {
  const bytes = new Uint8Array(Math.ceil(hex.length / 2));
  for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(hex.substr(i * 2, 2), 16);
  return bytes;
}
