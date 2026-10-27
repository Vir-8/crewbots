use std::path::Path;
use std::process::Command;

fn main() {
    dictation_helper();
    commit();
    tauri_build::build()
}

/// The commit this build is made from, for About crewbots.
fn commit() {
    println!("cargo:rerun-if-changed=../../.git/HEAD");
    println!("cargo:rerun-if-changed=../../.git/refs/heads");
    let out = Command::new("git").args(["rev-parse", "--short=12", "HEAD"]).output();
    let commit = out.ok().filter(|o| o.status.success()).map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string());
    println!("cargo:rustc-env=CREWBOTS_COMMIT={}", commit.unwrap_or_else(|| "a local build".into()));
}

/// Builds crewbots-dictate (dictation/main.swift), which ships beside the app as a sidecar: macOS's speech recognizer
/// is a Swift API, and a small helper is simpler than binding it from Rust.
fn dictation_helper() {
    println!("cargo:rerun-if-changed=dictation/main.swift");
    let target = std::env::var("TARGET").unwrap_or_else(|_| "aarch64-apple-darwin".into());
    let out = Path::new("binaries").join(format!("crewbots-dictate-{target}"));
    std::fs::create_dir_all("binaries").expect("making binaries/");
    // Named outright: a build started from an Intel tool under Rosetta (an x86_64 node, say) would otherwise get an
    // Intel helper inside an Apple silicon app, and macOS warns about Intel code.
    let arch = if target.starts_with("x86_64") { "x86_64" } else { "arm64" };
    let status = Command::new("swiftc")
        .args(["-O", "-target", &format!("{arch}-apple-macos13"), "dictation/main.swift", "-o"])
        .arg(&out)
        .status()
        .expect("swiftc is needed to build the dictation helper (install the Xcode command line tools)");
    assert!(status.success(), "swiftc failed to build the dictation helper");
}
