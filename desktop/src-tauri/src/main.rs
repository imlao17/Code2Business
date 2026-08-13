use serde::Serialize;
use std::{
    env,
    fs,
    net::TcpStream,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::{AppHandle, Manager, RunEvent};

const API_HOST: &str = "127.0.0.1";
const API_PORT: u16 = 8123;

struct BackendState(Mutex<Option<Child>>);

#[derive(Serialize)]
struct SourceDirectory {
    path: String,
    name: String,
}

#[derive(Serialize)]
struct DesktopStatus {
    api_url: String,
    data_dir: String,
    backend_ready: bool,
}

fn api_url() -> String {
    format!("http://{API_HOST}:{API_PORT}")
}

fn sqlite_url(db_path: &Path) -> String {
    // An absolute macOS path needs four slashes after sqlite:.
    format!("sqlite:///{}", db_path.display())
}

fn backend_ready() -> bool {
    TcpStream::connect_timeout(
        &format!("{API_HOST}:{API_PORT}").parse().expect("valid loopback address"),
        Duration::from_millis(200),
    )
    .is_ok()
}

fn project_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent().expect("src-tauri has desktop parent")
        .parent().expect("desktop has repository parent")
        .to_path_buf()
}

fn development_python(repo_root: &Path) -> PathBuf {
    env::var_os("C2B_DESKTOP_PYTHON")
        .map(PathBuf::from)
        .unwrap_or_else(|| repo_root.join(".venv").join("bin").join("python"))
}

fn packaged_backend(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .resource_dir()
        .map_err(|error| format!("无法定位应用资源目录: {error}"))
        .map(|dir| dir.join("c2b-api"))
}

fn launch_backend(app: &AppHandle, state: &BackendState) -> Result<(), String> {
    if backend_ready() {
        return Ok(());
    }

    let data_dir = app.path()
        .app_data_dir()
        .map_err(|error| format!("无法定位应用数据目录: {error}"))?;
    let source_dir = data_dir.join("sources");
    let config_dir = data_dir.join("settings");
    fs::create_dir_all(&source_dir).map_err(|error| format!("无法创建本地数据目录: {error}"))?;
    fs::create_dir_all(&config_dir).map_err(|error| format!("无法创建本地设置目录: {error}"))?;

    let mut command = if cfg!(debug_assertions) {
        let root = project_root();
        let python = development_python(&root);
        if !python.is_file() {
            return Err(format!(
                "未找到开发 Python 运行时：{}。请设置 C2B_DESKTOP_PYTHON，或先创建仓库 .venv。",
                python.display()
            ));
        }
        let mut process = Command::new(python);
        process
            .args(["-m", "uvicorn", "c2b.api.main:app", "--host", API_HOST, "--port", &API_PORT.to_string()])
            .env("PYTHONPATH", root.join("core").join("src"));
        process
    } else {
        let binary = packaged_backend(app)?;
        if !binary.is_file() {
            return Err("C2B 本地分析引擎缺失；请重新安装完整的 C2B Desktop.app。".into());
        }
        Command::new(binary)
    };

    command
        .env("C2B_DESKTOP_MODE", "1")
        .env("C2B_DB_URL", sqlite_url(&data_dir.join("c2b.db")))
        .env("C2B_DATA_ROOT", &source_dir)
        .env("C2B_CONFIG_DIR", &config_dir)
        .env("C2B_DESKTOP_PARENT_PID", std::process::id().to_string())
        // The desktop API is loopback-only.  This permits an explicitly
        // chosen local provider such as Ollama without weakening server mode.
        .env("C2B_ALLOW_PRIVATE_LLM", "1")
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    let child = command.spawn().map_err(|error| format!("无法启动本地分析引擎: {error}"))?;
    *state.0.lock().map_err(|_| "本地分析引擎状态锁不可用")? = Some(child);

    let deadline = Instant::now() + Duration::from_secs(20);
    while Instant::now() < deadline {
        if backend_ready() {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(200));
    }
    Err("本地分析引擎在 20 秒内未就绪，请在“帮助与诊断”中查看启动日志。".into())
}

#[tauri::command]
fn choose_source_directory() -> Option<SourceDirectory> {
    rfd::FileDialog::new()
        .set_title("选择要分析的代码目录")
        .pick_folder()
        .map(|path| SourceDirectory {
            name: path.file_name()
                .and_then(|part| part.to_str())
                .unwrap_or("本地代码库")
                .to_owned(),
            path: path.to_string_lossy().into_owned(),
        })
}

#[tauri::command]
fn desktop_status(app: AppHandle) -> Result<DesktopStatus, String> {
    let data_dir = app.path()
        .app_data_dir()
        .map_err(|error| format!("无法定位应用数据目录: {error}"))?;
    Ok(DesktopStatus {
        api_url: api_url(),
        data_dir: data_dir.to_string_lossy().into_owned(),
        backend_ready: backend_ready(),
    })
}

fn stop_backend(state: &BackendState) {
    if let Ok(mut child) = state.0.lock() {
        if let Some(mut process) = child.take() {
            let _ = process.kill();
            let _ = process.wait();
        }
    }
}

fn main() {
    let app = tauri::Builder::default()
        .manage(BackendState(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![choose_source_directory, desktop_status])
        .setup(|app| {
            let state = app.state::<BackendState>();
            launch_backend(&app.handle(), &state)?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while running C2B Desktop");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            let state = app_handle.state::<BackendState>();
            stop_backend(&state);
        }
    });
}
