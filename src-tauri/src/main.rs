use std::fs::OpenOptions;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use url::form_urlencoded::byte_serialize;
use url::Url;

struct BackendChild(Mutex<Option<Child>>);

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
    let mut response = String::new();
    if std::io::Read::read_to_string(&mut stream, &mut response).is_err() {
        return false;
    }
    response.starts_with("HTTP/1.1 200") || response.starts_with("HTTP/1.0 200")
}

fn wait_for_ui(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if ui_health_ok(port) {
            return true;
        }
        thread::sleep(Duration::from_millis(750));
    }
    false
}

fn start_backend(root_dir: &Path, port: u16) -> Result<Option<Child>, String> {
    if ui_health_ok(port) {
        append_launcher_log(root_dir, &format!("reusing existing ui backend on port {port}"));
        write_preflight_report(root_dir, &build_preflight_report(root_dir, None, None));
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

    let child = Command::new(&python)
        .current_dir(root_dir)
        .arg("-m")
        .arg("astrata.ui.server")
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(port.to_string())
        .arg("--no-open")
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr))
        .spawn()
        .map_err(|err| {
            let message = format!(
                "Failed to launch Astrata UI backend with {:?}: {err}",
                python
            );
            write_preflight_report(root_dir, &build_preflight_report(root_dir, Some(&python), Some(&message)));
            message
        })?;
    append_launcher_log(
        root_dir,
        &format!(
            "spawned ui backend with {:?} on http://127.0.0.1:{port}, pid={}",
            python,
            child.id()
        ),
    );

    if !wait_for_ui(port, Duration::from_secs(90)) {
        let message = format!(
            "Astrata UI backend did not become healthy on http://127.0.0.1:{port} within 90 seconds. Check {:?} for startup logs.",
            log_path
        );
        write_preflight_report(root_dir, &build_preflight_report(root_dir, Some(&python), Some(&message)));
        return Err(message);
    }

    write_preflight_report(root_dir, &build_preflight_report(root_dir, Some(&python), None));

    Ok(Some(child))
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
        .manage(BackendChild(Mutex::new(child)))
        .setup(move |app| {
            WebviewWindowBuilder::new(app, "main", target_url.clone())
                .title(window_title)
                .inner_size(1440.0, 960.0)
                .min_inner_size(1100.0, 760.0)
                .resizable(true)
                .build()
                .map_err(|err| format!("Failed to create Astrata desktop window: {err}"))?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Astrata desktop shell")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                {
                    let state = app.state::<BackendChild>();
                    if let Ok(mut maybe_child) = state.0.lock() {
                        if let Some(child) = maybe_child.as_mut() {
                            let _ = child.kill();
                        }
                    };
                }
            }
        });
}
