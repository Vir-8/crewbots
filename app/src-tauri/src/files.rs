//! Files the window shows: ones you attached, and ones bots made. The window draws them straight from disk through
//! the asset protocol (no base64 through the API, and video streams), opens them in their own app, or shows them in
//! Finder.

use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};

use tauri::{AppHandle, Manager};
use tauri_plugin_opener::OpenerExt;

/// Kinds of file a click opens in their own app. Anything else, a script or an app a bot wrote among them, is only
/// shown in Finder: opening it could run it with your rights, outside any bot's sandbox.
const OPENS: &[&str] = &[
    "png", "jpg", "jpeg", "gif", "webp", "heic", "heif", "bmp", "tif", "tiff", "svg", // images
    "mp4", "mov", "m4v", "webm", "mkv", "avi", // video
    "mp3", "m4a", "wav", "aac", "flac", "ogg", "aif", "aiff", // audio
    "pdf", "txt", "md", "markdown", "csv", "tsv", "json", "log", "rtf", "html", "htm", // documents
    "docx", "xlsx", "pptx", "pages", "numbers", "key", "odt", "ods", "odp",
];

pub fn opens(path: &Path) -> bool {
    path.extension().and_then(|e| e.to_str()).is_some_and(|e| OPENS.contains(&e.to_ascii_lowercase().as_str()))
}

fn thumbnails(app: &AppHandle) -> Option<PathBuf> {
    app.path().app_cache_dir().ok().map(|dir| dir.join("thumbnails"))
}

/// At start: the window may draw what's in the crew's attachments, and the thumbnails made for it.
pub fn allow(app: &AppHandle, crew_home: &Path) {
    let scope = app.asset_protocol_scope();
    let _ = scope.allow_directory(crew_home.join("attachments"), true);
    if let Some(dir) = thumbnails(app) {
        let _ = std::fs::create_dir_all(&dir);
        let _ = scope.allow_directory(dir, false);
    }
}

/// Files a bot named in its reply, where they are in its folders: the window may draw these too.
#[tauri::command]
pub fn show_files(app: AppHandle, paths: Vec<String>) {
    let scope = app.asset_protocol_scope();
    for path in paths.iter().map(PathBuf::from).filter(|p| p.is_file()) {
        let _ = scope.allow_file(path);
    }
}

#[tauri::command]
pub fn open_file(app: AppHandle, path: String) -> Result<(), String> {
    let path = PathBuf::from(path);
    let opened = if opens(&path) {
        app.opener().open_path(path.to_string_lossy(), None::<&str>)
    } else {
        app.opener().reveal_item_in_dir(&path)
    };
    opened.map_err(|e| e.to_string())
}

/// Quick Look's thumbnail of a file (a PDF's first page, a document's cover, a video's frame), made once for each
/// version of it, as a path the window draws. None when Quick Look has nothing for it.
#[tauri::command]
pub async fn thumbnail(app: AppHandle, path: String, size: u32) -> Option<String> {
    let source = PathBuf::from(&path);
    let meta = std::fs::metadata(&source).ok()?;
    let mut key = DefaultHasher::new();
    (&path, meta.len(), meta.modified().ok(), size).hash(&mut key);
    let dir = thumbnails(&app)?;
    let made = dir.join(format!("{:016x}.png", key.finish()));
    if made.is_file() {
        return Some(made.to_string_lossy().into());
    }
    let out = dir.join(format!("{:016x}", key.finish()));
    std::fs::create_dir_all(&out).ok()?;
    let ran = tokio::process::Command::new("/usr/bin/qlmanage")
        .args(["-t", "-s", &size.to_string(), "-o"])
        .arg(&out)
        .arg(&source)
        .output()
        .await;
    let png = out.join(format!("{}.png", source.file_name()?.to_string_lossy()));
    let moved = ran.is_ok() && std::fs::rename(&png, &made).is_ok();
    let _ = std::fs::remove_dir_all(&out);
    moved.then(|| made.to_string_lossy().into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_documents_and_media_open_in_their_app() {
        for path in ["render.PNG", "cut.mov", "report.pdf", "notes.md", "page.html", "deck.key"] {
            assert!(opens(Path::new(path)), "{path}");
        }
        for path in ["run.command", "setup.sh", "tool.py", "Thing.app", "install.pkg", "x.terminal", "no-extension"] {
            assert!(!opens(Path::new(path)), "{path}");
        }
    }
}
