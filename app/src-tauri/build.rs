use std::path::Path;
use std::process::Command;

fn main() {
    dictation_helper();
    tauri_build::build()
}

/// Builds crewbots-dictate (dictation/main.swift), which ships beside the app as a sidecar: macOS's speech recognizer
/// is a Swift API, and a small helper is simpler than binding it from Rust.
fn dictation_helper() {
    println!("cargo:rerun-if-changed=dictation/main.swift");
    let target = std::env::var("TARGET").unwrap_or_else(|_| "aarch64-apple-darwin".into());
    let out = Path::new("binaries").join(format!("crewbots-dictate-{target}"));
    std::fs::create_dir_all("binaries").expect("making binaries/");
    let status = Command::new("swiftc")
        .args(["-O", "dictation/main.swift", "-o"])
        .arg(&out)
        .status()
        .expect("swiftc is needed to build the dictation helper (install the Xcode command line tools)");
    assert!(status.success(), "swiftc failed to build the dictation helper");
}
