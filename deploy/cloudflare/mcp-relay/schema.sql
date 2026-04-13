DROP TABLE IF EXISTS users;
CREATE TABLE users (
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    password_hash TEXT,
    display_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    invite_code_used TEXT,
    default_profile_id TEXT,
    gpt_onboarded_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

DROP TABLE IF EXISTS invite_codes;
CREATE TABLE invite_codes (
    code_id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL DEFAULT 'one_time', -- 'one_time' or 'durable'
    status TEXT NOT NULL DEFAULT 'active', -- 'active' or 'revoked'
    max_uses INTEGER, -- NULL means unlimited or relies on type
    current_uses INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

DROP TABLE IF EXISTS account_sessions;
CREATE TABLE account_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    auth_method TEXT NOT NULL DEFAULT 'magic_link',
    status TEXT NOT NULL DEFAULT 'active',
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

DROP TABLE IF EXISTS relay_profiles;
CREATE TABLE relay_profiles (
    profile_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    label TEXT NOT NULL,
    control_posture TEXT NOT NULL DEFAULT 'true_remote_prime',
    disclosure_tier TEXT NOT NULL DEFAULT 'trusted_remote',
    allow_remote_host_bash INTEGER NOT NULL DEFAULT 0,
    remote_host_bash_acknowledged_at TEXT,
    remote_host_bash_warning TEXT NOT NULL DEFAULT '',
    default_device_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

DROP TABLE IF EXISTS devices;
CREATE TABLE devices (
    device_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    label TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'desktop',
    status TEXT NOT NULL DEFAULT 'active',
    last_seen_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

DROP TABLE IF EXISTS device_links;
CREATE TABLE device_links (
    link_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    relay_endpoint TEXT NOT NULL DEFAULT '',
    link_token_hash TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    last_heartbeat_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

DROP TABLE IF EXISTS oauth_clients;
CREATE TABLE oauth_clients (
    client_id TEXT PRIMARY KEY,
    client_name TEXT NOT NULL,
    redirect_uris TEXT NOT NULL, -- Stored as JSON string array
    token_endpoint_auth_method TEXT NOT NULL DEFAULT 'none',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

DROP TABLE IF EXISTS oauth_authorization_codes;
CREATE TABLE oauth_authorization_codes (
    code_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL DEFAULT '',
    code_challenge TEXT NOT NULL DEFAULT '',
    code_challenge_method TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    device_id TEXT,
    resource TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT 'astrata:read astrata:write',
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

DROP TABLE IF EXISTS oauth_access_tokens;
CREATE TABLE oauth_access_tokens (
    token_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    device_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

DROP TABLE IF EXISTS gpt_connections;
CREATE TABLE gpt_connections (
    connection_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    oauth_client_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    last_used_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
