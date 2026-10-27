//! Installing an update over this app. The daemon checks and builds it (scripts/release, started from Settings), in
//! `~/.crewbots/build`; the app puts it in its own place and then restarts. macOS lets an app replace itself, where
//! the daemon, a launchd service, may not touch an app it didn't make.

use std::path::{Path, PathBuf};
use std::process::Command;

/// Where updates are built: only a bundle from there is installed.
fn builds() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_default()).join(".crewbots").join("build")
}

/// This app's own bundle, when it runs from one; a development build installs to /Applications.
fn own_bundle() -> PathBuf {
    let exe = std::env::current_exe().ok();
    let bundle = exe.as_deref().and_then(|e| e.ancestors().nth(3)).map(Path::to_path_buf);
    match bundle {
        Some(b) if b.extension().is_some_and(|x| x == "app") && !b.starts_with(builds()) => b,
        _ => PathBuf::from("/Applications/crewbots.app"),
    }
}

/// Puts the build at `bundle` in this app's place, and says where. The caller restarts.
#[tauri::command]
pub async fn install_update(bundle: String) -> Result<String, String> {
    let from = PathBuf::from(&bundle);
    if !from.starts_with(builds()) || from.file_name().is_none_or(|n| n != "crewbots.app") || !from.join("Contents/MacOS/crewbots").is_file() {
        return Err(format!("{bundle} isn't an update's build"));
    }
    let to = own_bundle();
    tauri::async_runtime::spawn_blocking(move || replace(&from, &to)).await.map_err(|e| e.to_string())?
}

/// The new bundle copied in beside the old (without the build's extended attributes: what this app writes is its
/// own), then swapped in, and the old one removed. The running app carries on from the old files until it restarts.
fn replace(from: &Path, to: &Path) -> Result<String, String> {
    let staged = PathBuf::from(format!("{}.new", to.display()));
    let old = PathBuf::from(format!("{}.old", to.display()));
    let _ = std::fs::remove_dir_all(&staged);
    let _ = std::fs::remove_dir_all(&old);
    let copied = Command::new("/usr/bin/ditto").args(["--noextattr", "--noqtn"]).arg(from).arg(&staged).output();
    match copied {
        Ok(out) if out.status.success() => {}
        Ok(out) => return Err(format!("couldn't copy the new build: {}", String::from_utf8_lossy(&out.stderr).trim())),
        Err(e) => return Err(format!("couldn't copy the new build: {e}")),
    }
    if to.exists() {
        std::fs::rename(to, &old).map_err(|e| format!("couldn't move the old app aside: {e}"))?;
    }
    if let Err(e) = std::fs::rename(&staged, to) {
        let _ = std::fs::rename(&old, to); // put it back
        return Err(format!("couldn't put the new app in place: {e}"));
    }
    let _ = std::fs::remove_dir_all(&old);
    Ok(to.display().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn swaps_the_new_bundle_in_and_keeps_the_old_one_if_it_cannot() {
        let dir = std::env::temp_dir().join(format!("crewbots-update-test-{}", std::process::id()));
        let (from, to) = (dir.join("build/crewbots.app"), dir.join("Applications/crewbots.app"));
        std::fs::create_dir_all(from.join("Contents/MacOS")).unwrap();
        std::fs::write(from.join("Contents/MacOS/crewbots"), "new").unwrap();
        std::fs::create_dir_all(to.join("Contents/MacOS")).unwrap();
        std::fs::write(to.join("Contents/MacOS/crewbots"), "old").unwrap();
        assert_eq!(replace(&from, &to).unwrap(), to.display().to_string());
        assert_eq!(std::fs::read_to_string(to.join("Contents/MacOS/crewbots")).unwrap(), "new");
        assert!(!PathBuf::from(format!("{}.old", to.display())).exists());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
