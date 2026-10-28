//! Installing an update over this app. The daemon checks and builds it (scripts/release, started from Settings), in
//! `~/.crewbots/build`; the app puts it in its own place and then restarts. macOS lets an app replace itself, where
//! the daemon, a launchd service, may not touch an app it didn't make.

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::OnceLock;
use std::{ffi::CString, io, os::unix::{ffi::OsStrExt, fs::MetadataExt}};

#[derive(Debug, serde::Serialize)]
pub struct InstallResult {
    path: String,
    warning: Option<String>,
}

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

/// The pinned app: resolved once, when the app starts (`init`), so the swap and the relaunch use the same path.
fn pinned_bundle() -> &'static Path {
    static BUNDLE: OnceLock<PathBuf> = OnceLock::new();
    BUNDLE.get_or_init(own_bundle)
}

pub fn init() {
    pinned_bundle();
}

/// What the relaunch helper runs, so tests can stand in for the system tools.
struct Relaunch<'a> {
    pid: u32,
    bundle: &'a Path,
    opener: &'a str,
    lister: &'a str,
    killer: &'a str,
    log: &'a Path,
    /// Seconds to wait for every old instance to be gone before giving up without opening.
    wait: u32,
    /// Seconds to wait for the opened app's process to appear.
    verify: u32,
}

/// Where the helper writes how the last relaunch went, for the next start to read.
fn relaunch_log() -> PathBuf {
    PathBuf::from(std::env::var("HOME").unwrap_or_default()).join(".crewbots").join("logs").join("app-relaunch.log")
}

/// Tauri's own restart runs `open -n` while this process is still alive, which starts a second instance and a second
/// Dock icon. This helper instead waits for the exit, sends SIGTERM to any other instance from the bundle, opens the
/// bundle only when none is running (a plain `open`, so Launch Services reuses the pinned app), checks that it
/// started (once more if not), and writes the outcome to the log.
const RELAUNCH: &str = r#"
pid=$1 bundle=$2 opener=$3 lister=$4 killer=$5 log=$6 limit=$7 verify=$8
note() { mkdir -p "$(dirname "$log")" && printf '%s\n' "$1" > "$log"; }
# The pids of processes whose executable is in the bundle: `ps -axo pid=,command=` lines, matched literally.
pids() { "$lister" -axo pid=,command= 2>/dev/null | awk -v p="$bundle/Contents/MacOS/" '{ l=$0; sub(/^ +/,"",l); n=index(l," "); if (n && index(substr(l,n+1),p)==1) print substr(l,1,n-1) }'; }
running() { [ -n "$(pids)" ]; }
deadline=$(( $(date +%s) + limit ))
while kill -0 "$pid" 2>/dev/null; do
  [ "$(date +%s)" -ge "$deadline" ] && { note "gave-up: crewbots (pid $pid) was still running"; exit 0; }
  sleep 0.1
done
asked=0
while running; do
  now=$(date +%s)
  if [ "$now" -ge "$asked" ]; then
    "$killer" -TERM $(pids) >/dev/null 2>&1
    asked=$((now + 10))
  fi
  [ "$now" -ge "$deadline" ] && { note "gave-up: an old crewbots instance was still running"; exit 0; }
  sleep 0.1
done
for attempt in 1 2; do
  "$opener" "$bundle" >/dev/null 2>&1
  end=$(( $(date +%s) + verify ))
  while [ "$(date +%s)" -lt "$end" ]; do
    if running; then
      if [ "$attempt" = 1 ]; then note "opened"; else note "retried: opened on the second try"; fi
      exit 0
    fi
    sleep 0.1
  done
done
note "failed: no crewbots process appeared after opening $bundle"
"#;

fn relaunch_command(r: &Relaunch) -> Command {
    let mut command = Command::new("/bin/sh");
    command
        .args(["-c", RELAUNCH, "sh"])
        .arg(r.pid.to_string())
        .args([r.bundle.as_os_str(), r.opener.as_ref(), r.lister.as_ref(), r.killer.as_ref(), r.log.as_os_str()])
        .arg(r.wait.to_string())
        .arg(r.verify.to_string())
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    #[cfg(unix)]
    std::os::unix::process::CommandExt::process_group(&mut command, 0);
    command
}

/// Starts the helper that reopens the app at its pinned path after this process exits. The caller exits.
pub fn relaunch() -> io::Result<()> {
    let log = relaunch_log();
    let r = Relaunch { pid: std::process::id(), bundle: pinned_bundle(), opener: "/usr/bin/open", lister: "/bin/ps", killer: "/bin/kill", log: &log, wait: 120, verify: 10 };
    relaunch_command(&r).spawn().map(drop)
}

/// What went wrong with the last relaunch, once: the log is cleared when read.
pub fn relaunch_failure() -> Option<String> {
    take_relaunch_failure(&relaunch_log())
}

fn take_relaunch_failure(log: &Path) -> Option<String> {
    let text = std::fs::read_to_string(log).ok()?;
    let _ = std::fs::remove_file(log);
    let text = text.trim();
    let detail = text.strip_prefix("failed: ").or_else(|| text.strip_prefix("gave-up: "))?;
    Some(format!("The last restart didn't finish ({detail}). Quit crewbots and open it again."))
}

/// Puts the build at `bundle` in this app's place, and says where. The caller restarts.
#[tauri::command]
pub async fn install_update(bundle: String) -> Result<InstallResult, String> {
    let from = PathBuf::from(&bundle);
    if !from.starts_with(builds()) || from.file_name().is_none_or(|n| n != "crewbots.app") || !from.join("Contents/MacOS/crewbots").is_file() {
        return Err(format!("{bundle} isn't an update's build"));
    }
    let to = pinned_bundle().to_path_buf();
    tauri::async_runtime::spawn_blocking(move || replace(&from, &to)).await.map_err(|e| e.to_string())?
}

/// Keep the app directory itself: the Dock's pinned item may refer to its file identity.
fn replace(from: &Path, to: &Path) -> Result<InstallResult, String> {
    replace_with(from, to, exchange_contents, remove_quarantine, |path| std::fs::remove_dir_all(path))
}

fn replace_with(
    from: &Path,
    to: &Path,
    exchange: impl FnOnce(&Path, &Path) -> io::Result<()>,
    quarantine: impl FnOnce(&Path) -> io::Result<()>,
    cleanup: impl FnOnce(&Path) -> io::Result<()>,
) -> Result<InstallResult, String> {
    let parent = to.parent().ok_or("the app has no parent directory")?;
    // Never discard a previous backup if the installed app is missing its Contents.
    if !to.join("Contents").is_dir() {
        return Err("the installed app has no Contents; no update was installed".into());
    }
    clean_stale_stages(parent);
    let staged = (0..1000).find_map(|n| {
        let path = parent.join(format!(".crewbots-update-{}-{n}", std::process::id()));
        match std::fs::create_dir(&path) {
            Ok(()) => Some(Ok(path)),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => None,
            Err(e) => Some(Err(e)),
        }
    }).ok_or("couldn't find a staging directory")?.map_err(|e| format!("couldn't create a staging directory: {e}"))?;
    let copied = Command::new("/usr/bin/ditto").args(["--noextattr", "--noqtn"]).arg(from.join("Contents")).arg(staged.join("Contents")).output();
    match copied {
        Ok(out) if out.status.success() => {}
        Ok(out) => {
            let _ = std::fs::remove_dir_all(&staged);
            return Err(format!("couldn't copy the new build: {}", String::from_utf8_lossy(&out.stderr).trim()));
        }
        Err(e) => {
            let _ = std::fs::remove_dir_all(&staged);
            return Err(format!("couldn't copy the new build: {e}"));
        }
    }
    if let Err(e) = exchange(&staged.join("Contents"), &to.join("Contents")) {
        return match std::fs::remove_dir_all(&staged) {
            Ok(()) => Err(format!("couldn't exchange the app contents: {e}")),
            Err(cleanup) => Err(format!("couldn't exchange the app contents: {e}; couldn't remove the stage at {}: {cleanup}", staged.display())),
        };
    }
    // The exchange succeeded: errors from here must say that the new app is already installed.
    let quarantine = quarantine(to);
    let removed = cleanup(&staged);
    let mut problems = Vec::new();
    if quarantine.is_err() || removed.is_err() {
        if let Err(e) = quarantine { problems.push(format!("couldn't remove quarantine: {e}")); }
        if let Err(e) = removed { problems.push(format!("couldn't remove the old contents at {}: {e}", staged.display())); }
    }
    let warning = (!problems.is_empty()).then(|| format!("the update was installed, but {}", problems.join("; ")));
    if let Some(warning) = &warning { eprintln!("crewbots update: {warning}"); }
    Ok(InstallResult { path: to.display().to_string(), warning })
}

/// Atomically exchange the directories, leaving the previous Contents at the staging path.
#[cfg(target_os = "macos")]
fn exchange_contents(staged: &Path, installed: &Path) -> io::Result<()> {
    let staged = c_path(staged)?;
    let installed = c_path(installed)?;
    if unsafe { libc::renamex_np(staged.as_ptr(), installed.as_ptr(), libc::RENAME_SWAP) } == 0 {
        Ok(())
    } else {
        Err(io::Error::last_os_error())
    }
}

#[cfg(not(target_os = "macos"))]
fn exchange_contents(_: &Path, _: &Path) -> io::Result<()> {
    Err(io::Error::new(io::ErrorKind::Unsupported, "atomic app updates require macOS"))
}

fn c_path(path: &Path) -> io::Result<CString> {
    CString::new(path.as_os_str().as_bytes()).map_err(|e| io::Error::new(io::ErrorKind::InvalidInput, e))
}

#[cfg(target_os = "macos")]
fn remove_quarantine(path: &Path) -> io::Result<()> {
    let path = c_path(path)?;
    if unsafe { libc::removexattr(path.as_ptr(), c"com.apple.quarantine".as_ptr(), 0) } == 0 {
        return Ok(());
    }
    let error = io::Error::last_os_error();
    if error.raw_os_error() == Some(libc::ENOATTR) { Ok(()) } else { Err(error) }
}

#[cfg(not(target_os = "macos"))]
fn remove_quarantine(_: &Path) -> io::Result<()> { Ok(()) }

/// Recognize exactly the names created above, avoiding unrelated directories.
fn stage_pid(name: &str) -> Option<libc::pid_t> {
    let (pid, index) = name.strip_prefix(".crewbots-update-")?.split_once('-')?;
    let pid = pid.parse::<libc::pid_t>().ok()?;
    let index = index.parse::<u32>().ok()?;
    if pid <= 0 || index > 999 || name != format!(".crewbots-update-{pid}-{index}") { return None; }
    Some(pid)
}

/// Best-effort removal of stages from dead updater processes; a live process may still be using its stage.
fn clean_stale_stages(parent: &Path) {
    let entries = match std::fs::read_dir(parent) {
        Ok(entries) => entries,
        Err(e) => { eprintln!("crewbots update: couldn't inspect old stages in {}: {e}", parent.display()); return; }
    };
    for entry in entries {
        let entry = match entry {
            Ok(entry) => entry,
            Err(e) => { eprintln!("crewbots update: couldn't inspect an old stage: {e}"); continue; }
        };
        let name = entry.file_name();
        let Some(pid) = name.to_str().and_then(stage_pid) else { continue };
        let metadata = match std::fs::symlink_metadata(entry.path()) {
            Ok(metadata) => metadata,
            Err(e) => { eprintln!("crewbots update: couldn't inspect {}: {e}", entry.path().display()); continue; }
        };
        if !metadata.is_dir() || metadata.uid() != unsafe { libc::geteuid() } { continue }
        // ESRCH alone proves that no process with this PID exists; permissions errors mean skip it.
        if unsafe { libc::kill(pid, 0) } == 0 || io::Error::last_os_error().raw_os_error() != Some(libc::ESRCH) { continue }
        if let Err(e) = std::fs::remove_dir_all(entry.path()) {
            eprintln!("crewbots update: couldn't remove old stage {}: {e}", entry.path().display());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A helper run with fake tools in a temp folder: `procs` lists the "running" processes, `opener` records its calls.
    struct Rig {
        dir: PathBuf,
    }

    impl Rig {
        fn new(name: &str, opener_body: &str) -> Rig {
            let dir = std::env::temp_dir().join(format!("crewbots-relaunch-{name}-{}", std::process::id()));
            std::fs::create_dir_all(&dir).unwrap();
            let rig = Rig { dir };
            rig.script("ps", &format!("cat {}", rig.path("procs").display()));
            rig.script("kill", &format!("echo \"$@\" >> {}", rig.path("kills").display()));
            rig.script("open", &format!("echo \"$@\" >> {}\n{opener_body}", rig.path("opens").display()));
            std::fs::write(rig.path("procs"), "").unwrap();
            rig
        }
        fn path(&self, name: &str) -> PathBuf { self.dir.join(name) }
        fn script(&self, name: &str, body: &str) {
            let p = self.path(name);
            std::fs::write(&p, format!("#!/bin/sh\n{body}\n")).unwrap();
            std::fs::set_permissions(&p, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
        }
        fn run(&self, pid: u32, wait: u32, verify: u32) -> std::process::Child {
            let (opener, lister, killer) = (self.path("open"), self.path("ps"), self.path("kill"));
            let log = self.path("log");
            let r = Relaunch { pid, bundle: Path::new("/Applications/crewbots.app"), opener: opener.to_str().unwrap(), lister: lister.to_str().unwrap(), killer: killer.to_str().unwrap(), log: &log, wait, verify };
            relaunch_command(&r).spawn().unwrap()
        }
        fn read(&self, name: &str) -> String { std::fs::read_to_string(self.path(name)).unwrap_or_default() }
    }

    impl Drop for Rig {
        fn drop(&mut self) { std::fs::remove_dir_all(&self.dir).ok(); }
    }

    const APP: &str = "4242 /Applications/crewbots.app/Contents/MacOS/crewbots\n";

    /// A fake `open` that starts the app, as Launch Services would.
    fn starting(rig: &Rig) -> String {
        format!("echo '5000 {}' > {}", APP.trim().trim_start_matches("4242 "), rig.path("procs").display())
    }

    #[test]
    fn relaunch_waits_for_a_slow_exit_then_opens_the_bundle_without_n() {
        let rig = Rig::new("slow", "");
        rig.script("open", &format!("echo \"$@\" >> {}; {}", rig.path("opens").display(), starting(&rig)));
        let mut old = Command::new("/bin/sh").args(["-c", "sleep 2"]).spawn().unwrap();
        let mut helper = rig.run(old.id(), 20, 5);
        std::thread::sleep(std::time::Duration::from_millis(800));
        assert_eq!(rig.read("opens"), "", "opened while the old process was still running");
        old.wait().unwrap();
        helper.wait().unwrap();
        assert_eq!(rig.read("opens"), "/Applications/crewbots.app\n");
        assert_eq!(rig.read("log").trim(), "opened");
    }

    #[test]
    fn relaunch_terminates_only_the_old_instances_pid_and_opens_after_it_is_gone() {
        let rig = Rig::new("second", "");
        rig.script("open", &format!("echo \"$@\" >> {}; {}", rig.path("opens").display(), starting(&rig)));
        // A kill that works: it records its arguments and removes those pids from the process list.
        rig.script("kill", &format!("echo \"$@\" >> {0}; shift; for p in \"$@\"; do grep -v \"^ *$p \" {1} > {1}.tmp; mv {1}.tmp {1}; done", rig.path("kills").display(), rig.path("procs").display()));
        std::fs::write(rig.path("procs"), format!("{APP} 77 /bin/zsh -l\n")).unwrap();
        let mut helper = rig.run(0x7fff_fff0, 20, 5);
        helper.wait().unwrap();
        assert_eq!(rig.read("kills"), "-TERM 4242\n");
        assert_eq!(rig.read("opens"), "/Applications/crewbots.app\n");
        assert_eq!(rig.read("log").trim(), "opened");
    }

    #[test]
    fn relaunch_does_not_open_while_an_old_instance_stays_alive() {
        let rig = Rig::new("stuck", "");
        std::fs::write(rig.path("procs"), APP).unwrap();
        let mut helper = rig.run(0x7fff_fff0, 2, 5);
        helper.wait().unwrap();
        assert_eq!(rig.read("opens"), "");
        assert!(rig.read("log").starts_with("gave-up:"), "{}", rig.read("log"));
        assert!(rig.read("kills").starts_with("-TERM 4242"), "kills={:?} log={:?}", rig.read("kills"), rig.read("log"));
    }

    #[test]
    fn relaunch_ignores_processes_outside_the_bundle() {
        let rig = Rig::new("other", "");
        rig.script("open", &format!("echo \"$@\" >> {}; {}", rig.path("opens").display(), starting(&rig)));
        std::fs::write(rig.path("procs"), "9 /Applications/crewbots.app-old/Contents/MacOS/crewbots\n 12 tail /Applications/crewbots.app/Contents/MacOS/crewbots\n").unwrap();
        rig.run(0x7fff_fff0, 5, 5).wait().unwrap();
        assert_eq!(rig.read("kills"), "");
        assert_eq!(rig.read("log").trim(), "opened");
    }

    #[test]
    fn relaunch_retries_open_once_when_the_first_does_not_start_the_app() {
        let rig = Rig::new("retry", "");
        // The first open does nothing; the second starts the app.
        rig.script("open", &format!("echo \"$@\" >> {0}; [ $(wc -l < {0}) -ge 2 ] && {1}", rig.path("opens").display(), starting(&rig)));
        rig.run(0x7fff_fff0, 5, 1).wait().unwrap();
        assert_eq!(rig.read("opens"), "/Applications/crewbots.app\n/Applications/crewbots.app\n");
        assert!(rig.read("log").starts_with("retried:"), "{}", rig.read("log"));
    }

    #[test]
    fn relaunch_reports_failure_after_one_retry() {
        let rig = Rig::new("fail", "");
        rig.run(0x7fff_fff0, 5, 1).wait().unwrap();
        assert_eq!(rig.read("opens").lines().count(), 2);
        assert!(rig.read("log").starts_with("failed:"), "{}", rig.read("log"));
    }

    #[test]
    fn relaunch_passes_only_the_bundle_path_to_open() {
        let rig = Rig::new("args", "");
        let r = Relaunch { pid: 42, bundle: Path::new("/Applications/crewbots.app"), opener: "/usr/bin/open", lister: "/bin/ps", killer: "/bin/kill", log: &rig.path("log"), wait: 120, verify: 10 };
        let c = relaunch_command(&r);
        let args: Vec<_> = c.get_args().map(|a| a.to_string_lossy().into_owned()).collect();
        assert_eq!(&args[3..8], ["42", "/Applications/crewbots.app", "/usr/bin/open", "/bin/ps", "/bin/kill"]);
        assert!(!args.iter().any(|a| a == "-n") && !RELAUNCH.contains("\"$opener\" -n"));
    }

    #[test]
    fn a_failed_relaunch_is_reported_once_and_a_good_one_never() {
        let rig = Rig::new("notice", "");
        let log = rig.path("log");
        std::fs::write(&log, "opened\n").unwrap();
        assert_eq!(take_relaunch_failure(&log), None);
        std::fs::write(&log, "retried: opened on the second try\n").unwrap();
        assert_eq!(take_relaunch_failure(&log), None);
        std::fs::write(&log, "gave-up: an old crewbots instance was still running\n").unwrap();
        assert!(take_relaunch_failure(&log).unwrap().contains("an old crewbots instance was still running"));
        assert_eq!(take_relaunch_failure(&log), None);
    }

    #[test]
    fn replaces_contents_without_replacing_the_bundle_directory() {
        use std::os::unix::fs::MetadataExt;
        let dir = std::env::temp_dir().join(format!("crewbots-update-test-{}", std::process::id()));
        let (from, to) = (dir.join("build/crewbots.app"), dir.join("Applications/crewbots.app"));
        std::fs::create_dir_all(from.join("Contents/MacOS")).unwrap();
        std::fs::write(from.join("Contents/MacOS/crewbots"), "new").unwrap();
        std::fs::create_dir_all(to.join("Contents/MacOS")).unwrap();
        std::fs::write(to.join("Contents/MacOS/crewbots"), "old").unwrap();
        let inode = std::fs::metadata(&to).unwrap().ino();
        let result = replace(&from, &to).unwrap();
        assert_eq!(result.path, to.display().to_string());
        assert!(result.warning.is_none());
        assert_eq!(std::fs::metadata(&to).unwrap().ino(), inode);
        assert_eq!(std::fs::read_to_string(to.join("Contents/MacOS/crewbots")).unwrap(), "new");
        assert_eq!(std::fs::read_dir(to.parent().unwrap()).unwrap().count(), 1);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn failed_exchange_keeps_old_contents_and_removes_stage() {
        let dir = std::env::temp_dir().join(format!("crewbots-update-rollback-test-{}", std::process::id()));
        let to = dir.join("crewbots.app");
        let from = dir.join("build.app");
        std::fs::create_dir_all(to.join("Contents")).unwrap();
        std::fs::write(to.join("Contents/old"), "old").unwrap();
        std::fs::create_dir_all(from.join("Contents")).unwrap();
        std::fs::write(from.join("Contents/new"), "new").unwrap();
        let err = replace_with(&from, &to, |_, _| Err(io::Error::other("exchange failed")), remove_quarantine, |path| std::fs::remove_dir_all(path)).unwrap_err();
        assert!(err.contains("exchange failed"));
        assert_eq!(std::fs::read_to_string(to.join("Contents/old")).unwrap(), "old");
        assert_eq!(std::fs::read_dir(&dir).unwrap().count(), 2);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn failed_cleanup_reports_installed_update_and_keeps_old_contents() {
        let dir = std::env::temp_dir().join(format!("crewbots-update-cleanup-test-{}", std::process::id()));
        let to = dir.join("crewbots.app");
        let from = dir.join("build.app");
        std::fs::create_dir_all(to.join("Contents")).unwrap();
        std::fs::write(to.join("Contents/old"), "old").unwrap();
        std::fs::create_dir_all(from.join("Contents")).unwrap();
        std::fs::write(from.join("Contents/new"), "new").unwrap();
        let result = replace_with(&from, &to, exchange_contents, |_| Err(io::Error::other("quarantine failed")), |_| Err(io::Error::other("cleanup failed"))).unwrap();
        let stage = dir.join(format!(".crewbots-update-{}-0", std::process::id()));
        let warning = result.warning.unwrap();
        assert!(warning.contains("the update was installed") && warning.contains("cleanup failed") && warning.contains("quarantine failed") && warning.contains(stage.to_str().unwrap()));
        assert_eq!(std::fs::read_to_string(to.join("Contents/new")).unwrap(), "new");
        assert_eq!(std::fs::read_to_string(stage.join("Contents/old")).unwrap(), "old");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    #[cfg(target_os = "macos")]
    fn removes_outer_quarantine_after_exchange() {
        let dir = std::env::temp_dir().join(format!("crewbots-update-quarantine-test-{}", std::process::id()));
        let to = dir.join("crewbots.app");
        let from = dir.join("build.app");
        std::fs::create_dir_all(to.join("Contents")).unwrap();
        std::fs::create_dir_all(from.join("Contents")).unwrap();
        let path = c_path(&to).unwrap();
        let value = b"0081;00000000;crewbots;";
        assert_eq!(unsafe { libc::setxattr(path.as_ptr(), c"com.apple.quarantine".as_ptr(), value.as_ptr().cast(), value.len(), 0, 0) }, 0);
        assert!(replace(&from, &to).is_ok());
        assert_eq!(unsafe { libc::getxattr(path.as_ptr(), c"com.apple.quarantine".as_ptr(), std::ptr::null_mut(), 0, 0, 0) }, -1);
        assert_eq!(io::Error::last_os_error().raw_os_error(), Some(libc::ENOATTR));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn removes_dead_stages_but_not_live_or_unrelated_directories() {
        let dir = std::env::temp_dir().join(format!("crewbots-update-stale-test-{}", std::process::id()));
        let dead = dir.join(".crewbots-update-2147483647-0");
        let live = dir.join(format!(".crewbots-update-{}-0", std::process::id()));
        let unrelated = dir.join(".crewbots-update-not-ours");
        let leading_zero = dir.join(".crewbots-update-02147483647-0");
        let plus = dir.join(".crewbots-update-+2147483647-0");
        let high_index = dir.join(".crewbots-update-2147483647-1000");
        for path in [&dead, &live, &unrelated, &leading_zero, &plus, &high_index] { std::fs::create_dir_all(path).unwrap(); }
        clean_stale_stages(&dir);
        assert!(!dead.exists());
        assert!(live.exists() && unrelated.exists() && leading_zero.exists() && plus.exists() && high_index.exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn stage_names_match_only_the_format_the_updater_creates() {
        for name in [".crewbots-update-1-0", ".crewbots-update-2147483647-999"] {
            assert!(stage_pid(name).is_some(), "{name}");
        }
        for name in [".crewbots-update-01-0", ".crewbots-update-+1-0", ".crewbots-update-0-0", ".crewbots-update-1-00", ".crewbots-update-1-+0", ".crewbots-update-1-1000", ".crewbots-update-1-0-extra", ".crewbots-update-2147483648-0"] {
            assert!(stage_pid(name).is_none(), "{name}");
        }
    }

    #[test]
    fn undeletable_stale_stage_does_not_block_a_later_process() {
        use std::os::unix::fs::PermissionsExt;
        const CHILD_DIR: &str = "CREWBOTS_TEST_STALE_STAGE_DIR";
        if let Some(dir) = std::env::var_os(CHILD_DIR) {
            let locked = PathBuf::from(dir).join(format!(".crewbots-update-{}-0/locked", std::process::id()));
            std::fs::create_dir_all(&locked).unwrap();
            std::fs::write(locked.join("old"), "old").unwrap();
            std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o555)).unwrap();
            return;
        }
        let dir = std::env::temp_dir().join(format!("crewbots-update-dead-stage-test-{}", std::process::id()));
        let to = dir.join("crewbots.app");
        let from = dir.join("build.app");
        std::fs::create_dir_all(to.join("Contents")).unwrap();
        std::fs::write(to.join("Contents/old"), "old").unwrap();
        std::fs::create_dir_all(from.join("Contents")).unwrap();
        std::fs::write(from.join("Contents/new"), "new").unwrap();
        let child = Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "update::tests::undeletable_stale_stage_does_not_block_a_later_process"])
            .env(CHILD_DIR, &dir).output().unwrap();
        assert!(child.status.success());
        let stale = std::fs::read_dir(&dir).unwrap().map(|e| e.unwrap().path()).find(|p| p.file_name().unwrap().to_string_lossy().starts_with(".crewbots-update-")).unwrap();
        assert!(std::fs::remove_dir_all(&stale).is_err(), "the stale stage must actually be undeletable for this test");
        let result = replace(&from, &to).unwrap();
        assert!(result.warning.is_none());
        assert_eq!(std::fs::read_to_string(to.join("Contents/new")).unwrap(), "new");
        assert!(stale.join("locked/old").exists());
        std::fs::set_permissions(stale.join("locked"), std::fs::Permissions::from_mode(0o755)).unwrap();
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn missing_installed_contents_does_not_delete_a_previous_backup() {
        let dir = std::env::temp_dir().join(format!("crewbots-update-recovery-test-{}", std::process::id()));
        let to = dir.join("crewbots.app");
        let backup = dir.join(".crewbots-update-2147483647-0/previous");
        std::fs::create_dir_all(&to).unwrap();
        std::fs::create_dir_all(&backup).unwrap();
        std::fs::write(backup.join("old"), "old").unwrap();
        assert!(replace(&dir.join("build.app"), &to).unwrap_err().contains("no Contents"));
        assert_eq!(std::fs::read_to_string(backup.join("old")).unwrap(), "old");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn failed_copy_leaves_old_app_untouched() {
        let dir = std::env::temp_dir().join(format!("crewbots-update-copy-test-{}", std::process::id()));
        let to = dir.join("crewbots.app");
        std::fs::create_dir_all(to.join("Contents")).unwrap();
        std::fs::write(to.join("Contents/old"), "old").unwrap();
        assert!(replace(&dir.join("missing.app"), &to).is_err());
        assert_eq!(std::fs::read_to_string(to.join("Contents/old")).unwrap(), "old");
        assert_eq!(std::fs::read_dir(&dir).unwrap().count(), 1);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
