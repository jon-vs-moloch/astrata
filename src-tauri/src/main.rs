use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, RunEvent, State, WebviewUrl, WebviewWindowBuilder, WindowEvent};
use url::form_urlencoded::byte_serialize;
use url::Url;

#[derive(Serialize, Deserialize)]
struct DesktopSessionState {
    ui_port: u16,
    backend_url: String,
    started_by_desktop_shell: bool,
    backend_pid: Option<u32>,
    frontend_deliberately_closed: bool,
    backend_deliberately_stopped: bool,
    last_action: String,
    started_at_unix_ms: u128,
}

struct BackendChildState(Mutex<Option<Child>>);

struct CloseGuardState(Mutex<bool>);

struct DesktopRuntimeState {
    root_dir: PathBuf,
    ui_port: u16,
}

#[derive(Serialize)]
struct DesktopBackendStatus {
    backend_running: bool,
    backend_url: String,
    started_by_desktop_shell: bool,
    backend_pid: Option<u32>,
    frontend_deliberately_closed: bool,
    backend_deliberately_stopped: bool,
    recovery_needed: bool,
    stop_supported: bool,
    ui_port: u16,
    last_action: String,
}

#[derive(Serialize)]
struct PreflightCheck {
    name: String,
    ok: bool,
    status: String,
    detail: String,
}

#[derive(Serialize)]
struct StartupPreflightReport {
    phase: String,
    ok: bool,
    project_root: String,
    runtime_dir: String,
    selected_python: Option<String>,
    checks: Vec<PreflightCheck>,
    issues: Vec<String>,
    launcher_log_path: String,
    startup_error_path: String,
}

fn runtime_dir(root_dir: &Path) -> PathBuf {
    root_dir.join(".astrata")
}

fn launcher_log_path(root_dir: &Path) -> PathBuf {
    runtime_dir(root_dir).join("desktop-launcher.log")
}

fn startup_error_path(root_dir: &Path) -> PathBuf {
    runtime_dir(root_dir).join("desktop-startup-error.json")
}

fn startup_preflight_path(root_dir: &Path) -> PathBuf {
    runtime_dir(root_dir).join("startup-preflight.json")
}

fn desktop_session_path(root_dir: &Path) -> PathBuf {
    runtime_dir(root_dir).join("desktop-session.json")
}

fn append_launcher_log(root_dir: &Path, message: &str) {
    let _ = std::fs::create_dir_all(runtime_dir(root_dir));
    if let Ok(mut handle) = OpenOptions::new()
        .create(true)
        .append(true)
        .open(launcher_log_path(root_dir))
    {
        let _ = writeln!(handle, "{message}");
    }
}

fn clear_startup_error(root_dir: &Path) {
    let _ = std::fs::remove_file(startup_error_path(root_dir));
}

fn write_startup_error(root_dir: &Path, error: &str) {
    let payload = format!(
        "{{\"error\":{error:?},\"log_path\":{:?}}}",
        launcher_log_path(root_dir).display().to_string()
    );
    let _ = std::fs::write(startup_error_path(root_dir), payload);
}

fn write_preflight_report(root_dir: &Path, report: &StartupPreflightReport) {
    let _ = std::fs::create_dir_all(runtime_dir(root_dir));
    if let Ok(payload) = serde_json::to_string_pretty(report) {
        let _ = std::fs::write(startup_preflight_path(root_dir), payload);
    }
}

fn write_desktop_session(root_dir: &Path, session: &DesktopSessionState) {
    let _ = std::fs::create_dir_all(runtime_dir(root_dir));
    if let Ok(payload) = serde_json::to_string_pretty(session) {
        let _ = std::fs::write(desktop_session_path(root_dir), payload);
    }
}

fn clear_desktop_session(root_dir: &Path) {
    let _ = std::fs::remove_file(desktop_session_path(root_dir));
}

fn read_desktop_session(root_dir: &Path) -> Option<DesktopSessionState> {
    let payload = std::fs::read_to_string(desktop_session_path(root_dir)).ok()?;
    serde_json::from_str::<DesktopSessionState>(&payload).ok()
}

fn upsert_desktop_session(
    root_dir: &Path,
    ui_port: u16,
    update: impl FnOnce(&mut DesktopSessionState),
) -> DesktopSessionState {
    let mut session = read_desktop_session(root_dir).unwrap_or(DesktopSessionState {
        ui_port,
        backend_url: format!("http://127.0.0.1:{ui_port}/"),
        started_by_desktop_shell: false,
        backend_pid: None,
        frontend_deliberately_closed: false,
        backend_deliberately_stopped: false,
        last_action: "unknown".into(),
        started_at_unix_ms: now_unix_ms(),
    });
    update(&mut session);
    write_desktop_session(root_dir, &session);
    session
}

fn now_unix_ms() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or(0)
}

fn resolve_project_root() -> Result<PathBuf, String> {
    let cwd = std::env::current_dir()
        .map_err(|err| format!("Failed to resolve current working directory: {err}"))?;
    if cwd.join("astrata").is_dir() && cwd.join("pyproject.toml").exists() {
        return Ok(cwd);
    }

    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let candidate = manifest_dir
        .parent()
        .map(Path::to_path_buf)
        .ok_or_else(|| {
            "Failed to resolve project root from Cargo manifest directory.".to_string()
        })?;

    if candidate.join("astrata").is_dir() && candidate.join("pyproject.toml").exists() {
        return Ok(candidate);
    }

    Err(format!(
        "Unable to locate the Astrata project root from cwd {:?} or manifest dir {:?}.",
        cwd, manifest_dir
    ))
}

fn candidate_python_paths(root_dir: &Path) -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(explicit) = std::env::var("ASTRATA_DESKTOP_PYTHON") {
        if !explicit.trim().is_empty() {
            candidates.push(PathBuf::from(explicit));
        }
    }
    candidates.extend([
        root_dir.join(".astrata").join("runtime-venv").join("bin").join("python"),
        root_dir.join(".venv").join("bin").join("python"),
        root_dir.join("venv").join("bin").join("python"),
        PathBuf::from("/opt/homebrew/bin/python3"),
        PathBuf::from("python3"),
        PathBuf::from("python"),
    ]);
    candidates
}

fn python_is_usable(path: &Path) -> bool {
    let version_ok = Command::new(path)
        .arg("--version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false);
    if !version_ok {
        return false;
    }
    Command::new(path)
        .arg("-c")
        .arg("import fastapi, uvicorn")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

fn find_python(root_dir: &Path) -> Option<PathBuf> {
    for path in candidate_python_paths(root_dir) {
        if path.is_absolute() {
            if path.exists() && python_is_usable(&path) {
                return Some(path);
            }
            continue;
        }
        if python_is_usable(&path) {
            return Some(path);
        }
    }
    None
}

fn build_preflight_report(root_dir: &Path, selected_python: Option<&Path>, bootstrap_error: Option<&str>) -> StartupPreflightReport {
    let runtime = runtime_dir(root_dir);
    let manifest_path = runtime.join("install_manifest.json");
    let managed_runtime_python = runtime.join("runtime-venv").join("bin").join("python");
    let mut checks = vec![
        PreflightCheck {
            name: "runtime_dir".into(),
            ok: runtime.exists(),
            status: if runtime.exists() { "pass".into() } else { "fail".into() },
            detail: runtime.display().to_string(),
        },
        PreflightCheck {
            name: "install_manifest".into(),
            ok: manifest_path.exists(),
            status: if manifest_path.exists() { "pass".into() } else { "fail".into() },
            detail: manifest_path.display().to_string(),
        },
        PreflightCheck {
            name: "managed_runtime_python".into(),
            ok: managed_runtime_python.exists(),
            status: if managed_runtime_python.exists() { "pass".into() } else { "fail".into() },
            detail: managed_runtime_python.display().to_string(),
        },
    ];
    if let Some(path) = selected_python {
        let usable = python_is_usable(path);
        checks.push(PreflightCheck {
            name: "selected_python".into(),
            ok: usable,
            status: if usable { "pass".into() } else { "fail".into() },
            detail: path.display().to_string(),
        });
    } else if bootstrap_error.is_none() {
        checks.push(PreflightCheck {
            name: "selected_python".into(),
            ok: true,
            status: "pass".into(),
            detail: "existing healthy backend reused".into(),
        });
    } else {
        checks.push(PreflightCheck {
            name: "selected_python".into(),
            ok: false,
            status: "fail".into(),
            detail: "no usable python selected".into(),
        });
    }
    if let Some(error) = bootstrap_error {
        checks.push(PreflightCheck {
            name: "backend_bootstrap".into(),
            ok: false,
            status: "fail".into(),
            detail: error.to_string(),
        });
    }
    let issues = checks
        .iter()
        .filter(|check| !check.ok)
        .map(|check| format!("{}: {}", check.name, check.detail))
        .collect::<Vec<_>>();
    StartupPreflightReport {
        phase: "pre_inference".into(),
        ok: issues.is_empty(),
        project_root: root_dir.display().to_string(),
        runtime_dir: runtime.display().to_string(),
        selected_python: selected_python.map(|path| path.display().to_string()),
        checks,
        issues,
        launcher_log_path: launcher_log_path(root_dir).display().to_string(),
        startup_error_path: startup_error_path(root_dir).display().to_string(),
    }
}

fn ui_port_open(port: u16) -> bool {
    std::net::TcpStream::connect_timeout(
        &format!("127.0.0.1:{port}")
            .parse()
            .expect("valid localhost socket address"),
        Duration::from_millis(750),
    )
    .is_ok()
}

fn ui_health_ok(port: u16) -> bool {
    let mut stream = match std::net::TcpStream::connect_timeout(
        &format!("127.0.0.1:{port}")
            .parse()
            .expect("valid localhost socket address"),
        Duration::from_millis(750),
    ) {
        Ok(stream) => stream,
        Err(_) => return false,
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(1000)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(1000)));
    let request = b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
    if std::io::Write::write_all(&mut stream, request).is_err() {
        return false;
    }
    let mut response = Vec::new();
    let mut buffer = [0_u8; 1024];
    loop {
        match std::io::Read::read(&mut stream, &mut buffer) {
            Ok(0) => break,
            Ok(count) => {
                response.extend_from_slice(&buffer[..count]);
                if response.windows(12).any(|window| window == b"HTTP/1.1 200")
                    || response.windows(12).any(|window| window == b"HTTP/1.0 200")
                {
                    return true;
                }
            }
            Err(err)
                if err.kind() == std::io::ErrorKind::WouldBlock
                    || err.kind() == std::io::ErrorKind::TimedOut =>
            {
                break;
            }
            Err(_) => return false,
        }
    }
    false
}

fn wait_for_ui(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if ui_port_open(port) || ui_health_ok(port) {
            return true;
        }
        thread::sleep(Duration::from_millis(750));
    }
    false
}

fn start_backend(root_dir: &Path, port: u16) -> Result<Option<Child>, String> {
    if ui_port_open(port) || ui_health_ok(port) {
        append_launcher_log(root_dir, &format!("reusing existing ui backend on port {port}"));
        write_preflight_report(root_dir, &build_preflight_report(root_dir, None, None));
        write_desktop_session(
            root_dir,
            &DesktopSessionState {
                ui_port: port,
                backend_url: format!("http://127.0.0.1:{port}/"),
                started_by_desktop_shell: false,
                backend_pid: None,
                frontend_deliberately_closed: false,
                backend_deliberately_stopped: false,
                last_action: "reuse_backend".into(),
                started_at_unix_ms: now_unix_ms(),
            },
        );
        return Ok(None);
    }

    let python = find_python(root_dir).ok_or_else(|| {
        let error = "No usable Python runtime found for Astrata desktop startup. Astrata requires Python with fastapi and uvicorn installed."
            .to_string();
        write_preflight_report(root_dir, &build_preflight_report(root_dir, None, Some(&error)));
        error
    })?;
    write_preflight_report(root_dir, &build_preflight_report(root_dir, Some(&python), None));
    let runtime_dir = runtime_dir(root_dir);
    std::fs::create_dir_all(&runtime_dir)
        .map_err(|err| format!("Failed to create runtime dir: {err}"))?;
    let log_path = runtime_dir.join("desktop-ui.log");
    let stdout = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|err| format!("Failed to open desktop UI log file: {err}"))?;
    let stderr = stdout
        .try_clone()
        .map_err(|err| format!("Failed to clone desktop UI log file handle: {err}"))?;

    let mut child = Command::new(&python)
        .current_dir(root_dir)
        .arg("-m")
        .arg("astrata.main")
        .arg("supervisor-reconcile")
        .arg("--ui-host")
        .arg("127.0.0.1")
        .arg("--ui-port")
        .arg(port.to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr))
        .spawn()
        .map_err(|err| {
            let message = format!(
                "Failed to launch Astrata supervisor with {:?}: {err}",
                python
            );
            write_preflight_report(root_dir, &build_preflight_report(root_dir, Some(&python), Some(&message)));
            message
        })?;
    append_launcher_log(
        root_dir,
        &format!(
            "ran supervisor-reconcile with {:?} on http://127.0.0.1:{port}, pid={}",
            python,
            child.id()
        ),
    );
    write_desktop_session(
        root_dir,
        &DesktopSessionState {
            ui_port: port,
            backend_url: format!("http://127.0.0.1:{port}/"),
            started_by_desktop_shell: true,
            backend_pid: Some(child.id()),
            frontend_deliberately_closed: false,
            backend_deliberately_stopped: false,
            last_action: "start_backend".into(),
            started_at_unix_ms: now_unix_ms(),
        },
    );

    if !wait_for_ui(port, Duration::from_secs(90)) {
        let message = format!(
            "Astrata UI backend did not become healthy on http://127.0.0.1:{port} within 90 seconds. Check {:?} for startup logs.",
            log_path
        );
        let _ = child.kill();
        clear_desktop_session(root_dir);
        write_preflight_report(root_dir, &build_preflight_report(root_dir, Some(&python), Some(&message)));
        return Err(message);
    }

    write_preflight_report(root_dir, &build_preflight_report(root_dir, Some(&python), None));

    Ok(Some(child))
}

fn desktop_backend_status(root_dir: &Path, ui_port: u16) -> DesktopBackendStatus {
    let session = read_desktop_session(root_dir);
    let backend_running = ui_port_open(ui_port) || ui_health_ok(ui_port);
    let backend_url = session
        .as_ref()
        .map(|item| item.backend_url.clone())
        .unwrap_or_else(|| format!("http://127.0.0.1:{ui_port}/"));
    let started_by_desktop_shell = session
        .as_ref()
        .map(|item| item.started_by_desktop_shell)
        .unwrap_or(false);
    let backend_pid = session.as_ref().and_then(|item| item.backend_pid);
    let frontend_deliberately_closed = session
        .as_ref()
        .map(|item| item.frontend_deliberately_closed)
        .unwrap_or(false);
    let backend_deliberately_stopped = session
        .as_ref()
        .map(|item| item.backend_deliberately_stopped)
        .unwrap_or(false);
    let last_action = session
        .as_ref()
        .map(|item| item.last_action.clone())
        .unwrap_or_else(|| "unknown".into());
    DesktopBackendStatus {
        backend_running,
        backend_url,
        started_by_desktop_shell,
        backend_pid,
        frontend_deliberately_closed,
        backend_deliberately_stopped,
        recovery_needed: !backend_running && !backend_deliberately_stopped,
        stop_supported: backend_pid.is_some(),
        ui_port,
        last_action,
    }
}

fn stop_backend_process(root_dir: &Path, child_state: &BackendChildState, ui_port: u16) -> Result<(), String> {
    if let Ok(mut maybe_child) = child_state.0.lock() {
        if let Some(mut child) = maybe_child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
    
    if let Some(python) = find_python(root_dir) {
        let _ = Command::new(&python)
            .current_dir(root_dir)
            .arg("-m")
            .arg("astrata.main")
            .arg("supervisor-stop")
            .arg("--ui-port")
            .arg(ui_port.to_string())
            .arg("--include-adopted")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
    
    let mut stopped = true;
    let deadline = Instant::now() + Duration::from_secs(10);
    while Instant::now() < deadline {
        if !ui_port_open(ui_port) && !ui_health_ok(ui_port) {
            break;
        }
        thread::sleep(Duration::from_millis(250));
    }
    upsert_desktop_session(root_dir, ui_port, |session| {
        session.backend_pid = None;
        session.backend_deliberately_stopped = true;
        session.last_action = "stop_backend".into();
    });
    if stopped {
        Ok(())
    } else {
        Err("Desktop shell could not identify a stoppable backend process.".into())
    }
}

fn close_main_window(app: &AppHandle, close_guard: &CloseGuardState) -> Result<(), String> {
    if let Ok(mut allow) = close_guard.0.lock() {
        *allow = true;
    }
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "Astrata desktop window is not available.".to_string())?;
    window.close().map_err(|err| format!("Failed to close Astrata window: {err}"))
}

#[tauri::command]
fn desktop_backend_status_command(runtime: State<DesktopRuntimeState>) -> DesktopBackendStatus {
    desktop_backend_status(&runtime.root_dir, runtime.ui_port)
}

#[tauri::command]
fn desktop_handle_close_decision(
    app: AppHandle,
    runtime: State<DesktopRuntimeState>,
    child_state: State<BackendChildState>,
    close_guard: State<CloseGuardState>,
    stop_backend: bool,
) -> Result<(), String> {
    upsert_desktop_session(&runtime.root_dir, runtime.ui_port, |session| {
        session.frontend_deliberately_closed = true;
        session.backend_deliberately_stopped = stop_backend;
        session.last_action = if stop_backend {
            "close_and_stop_backend".into()
        } else {
            "close_keep_backend_running".into()
        };
    });
    if stop_backend {
        stop_backend_process(&runtime.root_dir, &child_state, runtime.ui_port)?;
    }
    close_main_window(&app, &close_guard)
}

#[tauri::command]
fn desktop_stop_backend(
    runtime: State<DesktopRuntimeState>,
    child_state: State<BackendChildState>,
) -> Result<DesktopBackendStatus, String> {
    upsert_desktop_session(&runtime.root_dir, runtime.ui_port, |session| {
        session.frontend_deliberately_closed = false;
        session.backend_deliberately_stopped = true;
        session.last_action = "stop_backend".into();
    });
    stop_backend_process(&runtime.root_dir, &child_state, runtime.ui_port)?;
    Ok(desktop_backend_status(&runtime.root_dir, runtime.ui_port))
}

#[tauri::command]
fn desktop_resume_backend(
    runtime: State<DesktopRuntimeState>,
    child_state: State<BackendChildState>,
) -> Result<DesktopBackendStatus, String> {
    upsert_desktop_session(&runtime.root_dir, runtime.ui_port, |session| {
        session.frontend_deliberately_closed = false;
        session.backend_deliberately_stopped = false;
        session.last_action = "resume_backend".into();
    });
    let child = start_backend(&runtime.root_dir, runtime.ui_port)?;
    if let Ok(mut slot) = child_state.0.lock() {
        *slot = child;
    }
    Ok(desktop_backend_status(&runtime.root_dir, runtime.ui_port))
}

fn encode_query_value(value: &str) -> String {
    byte_serialize(value.as_bytes()).collect()
}

fn main() {
    let root_dir = match resolve_project_root() {
        Ok(path) => path,
        Err(err) => panic!("{err}"),
    };
    append_launcher_log(&root_dir, "astrata desktop shell starting");
    let ui_port: u16 = 8891;
    let (child, startup_error) = match start_backend(&root_dir, ui_port) {
        Ok(child) => {
            clear_startup_error(&root_dir);
            (child, None)
        }
        Err(err) => {
            append_launcher_log(&root_dir, &format!("backend startup failed: {err}"));
            write_startup_error(&root_dir, &err);
            (None, Some(err))
        }
    };
    let target_url = startup_error
        .as_ref()
        .map(|error| {
            WebviewUrl::App(
                format!(
                    "error.html?error={}&preflight={}&startup_error={}",
                    encode_query_value(error),
                    encode_query_value(&startup_preflight_path(&root_dir).display().to_string()),
                    encode_query_value(&startup_error_path(&root_dir).display().to_string()),
                )
                .into(),
            )
        })
        .unwrap_or_else(|| {
            WebviewUrl::External(
                Url::parse(&format!("http://127.0.0.1:{ui_port}/"))
                    .expect("valid Astrata local UI URL"),
            )
        });
    let window_title = if startup_error.is_some() {
        "Astrata Startup Error"
    } else {
        "Astrata"
    };

    tauri::Builder::default()
        .manage(BackendChildState(Mutex::new(child)))
        .manage(CloseGuardState(Mutex::new(false)))
        .manage(DesktopRuntimeState {
            root_dir: root_dir.clone(),
            ui_port,
        })
        .invoke_handler(tauri::generate_handler![
            desktop_backend_status_command,
            desktop_handle_close_decision,
            desktop_stop_backend,
            desktop_resume_backend
        ])
        .setup(move |app| {
            WebviewWindowBuilder::new(app, "main", target_url.clone())
                .title(window_title)
                .inner_size(1440.0, 960.0)
                .min_inner_size(1100.0, 760.0)
                .resizable(true)
                .build()
                .map_err(|err| format!("Failed to create Astrata desktop window: {err}"))?;
            let app_handle = app.handle().clone();
            let root_dir = root_dir.clone();
            const RECOVERY_FAILURE_THRESHOLD: u8 = 3;
            thread::spawn(move || {
                let mut consecutive_failures: u8 = 0;
                loop {
                    thread::sleep(Duration::from_secs(3));
                    let Some(window) = app_handle.get_webview_window("main") else {
                        break;
                    };
                    let status = desktop_backend_status(&root_dir, ui_port);
                    if status.backend_running || status.backend_deliberately_stopped {
                        consecutive_failures = 0;
                        continue;
                    }
                    consecutive_failures = consecutive_failures.saturating_add(1);
                    if consecutive_failures < RECOVERY_FAILURE_THRESHOLD {
                        continue;
                    }
                    consecutive_failures = 0;
                    append_launcher_log(&root_dir, "desktop monitor detected backend down; attempting recovery");
                    match start_backend(&root_dir, ui_port) {
                        Ok(Some(child)) => {
                            if let Ok(mut slot) = app_handle.state::<BackendChildState>().0.lock() {
                                *slot = Some(child);
                            }
                            let _ = window.eval(&format!(
                                "window.__astrataDesktopHandleBackendRecovered && window.__astrataDesktopHandleBackendRecovered({:?});",
                                format!("http://127.0.0.1:{ui_port}/")
                            ));
                        }
                        Ok(None) => {
                            append_launcher_log(
                                &root_dir,
                                "desktop monitor found backend reachable on retry; skipping recovery overlay",
                            );
                        }
                        Err(err) => {
                            append_launcher_log(&root_dir, &format!("desktop monitor recovery failed: {err}"));
                        }
                    }
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Astrata desktop shell")
        .run(|app, event| {
            match event {
                RunEvent::WindowEvent { label, event, .. } if label == "main" => {
                    if let WindowEvent::CloseRequested { api, .. } = event {
                        let mut allow_close = false;
                        if let Ok(guard) = app.state::<CloseGuardState>().0.lock() {
                            allow_close = *guard;
                        }
                        if !allow_close {
                            api.prevent_close();
                            if let Some(window) = app.get_webview_window("main") {
                                if window
                                    .eval("window.__astrataDesktopHandleCloseRequest && window.__astrataDesktopHandleCloseRequest();")
                                    .is_err()
                                {
                                    let runtime = app.state::<DesktopRuntimeState>();
                                    upsert_desktop_session(&runtime.root_dir, runtime.ui_port, |session| {
                                        session.frontend_deliberately_closed = true;
                                        session.backend_deliberately_stopped = false;
                                        session.last_action = "close_keep_backend_running".into();
                                    });
                                    let _ = close_main_window(&app.app_handle().clone(), &app.state::<CloseGuardState>());
                                }
                            }
                        }
                    }
                }
                RunEvent::Exit => {
                    let runtime = app.state::<DesktopRuntimeState>();
                    append_launcher_log(
                        &runtime.root_dir,
                        "desktop window exited; backend state preserved unless it was deliberately stopped",
                    );
                }
                _ => {}
            }
        });
}
