# crewbots on your phone, over your own network

A design note, written before anything is built. The goal: read and answer your chats, see what needs you, and get
told when something does, from an iPhone on the same Wi-Fi as the Mac. No cloud and no public URL: the phone talks to
the daemon on the Mac and to nothing else.

## Scope

- **In:** the chats and group chats (read, send, attach a photo), threads (read), "Needs you" (answer a bot's question,
  let a held task go ahead), and notifications while the app is open.
- **Out, on purpose:** merging, code review submissions, role cards, settings, MCP servers and adding bots. Those stay
  on the Mac, where you can see what they touch. Nothing a phone can do grants a bot more than it has.

## How it works

1. **A second listener, off by default.** The daemon listens on `127.0.0.1` only (decision 2 in ARCHITECTURE.md). The
   phone needs one on the local network: a separate port (say 8766), opened only while "Phone" is switched on in
   Settings, serving TLS, and accepting only device tokens. It is a deliberate exception, so it's narrow:
   - only the routes in Scope, behind their own router;
   - no `/bot/` routes, no admin token, no `Host` other than the Mac's `.local` name;
   - a per-device rate limit, and every request kept in the agent log's sibling, a device log.
2. **Found by Bonjour.** The daemon advertises `_crewbots._tcp` while the listener is on, through macOS's own
   mDNSResponder (`dns-sd -R`), so it adds no dependency. The phone browses for it; iOS asks once to allow the local
   network (`NSLocalNetworkUsageDescription`, `NSBonjourServices`).
3. **Paired once, by a QR code.** Settings → Phone → Add a device shows a QR code holding the Mac's name and port, the
   SHA-256 fingerprint of the listener's certificate, and a one-time pairing code that expires in two minutes. The
   phone scans it, proves the code, and gets a device token.
4. **A self-signed certificate, pinned.** The daemon makes its own key and certificate with the `openssl` macOS ships,
   once, kept in `~/.crewbots/phone/`. The phone trusts that certificate and no other: the fingerprint came in the QR
   code, never over the network, so nothing on the Wi-Fi can stand in for the Mac.
5. **Revocable device tokens.** Each device has its own token, stored hashed (`devices`: name, token hash, paired,
   last seen, revoked). Settings lists the devices; Remove revokes one at once and closes its live connection.
   Turning Phone off closes the listener and every phone connection.
6. **Live updates and notifications** come over the same `/v1/live` WebSocket the desktop app uses, filtered to the
   chats in scope. While the phone app is open it shows a local notification for "needs you" and for a reply in a
   chat you're not looking at; a closed app gets nothing, since push would need Apple's servers.

## Native app or web app

| | Tauri 2 iOS build | Web app served on the LAN |
|---|---|---|
| UI | the same React, with a phone layout | the same React, with a phone layout |
| Certificate pinning | yes: the shell checks the fingerprint | no: Safari only offers "accept this certificate" warnings, or installing a local CA profile, which trusts that CA for every site |
| Bonjour | yes | no: you type the address, or scan it |
| Local notifications | yes, while open | only with a Home Screen web app, and unreliably |
| Setup | Xcode, and signing the app: a free Apple ID re-signs every 7 days, the paid program doesn't | none on the phone |

**Recommendation: the Tauri 2 iOS build.** Pinning is what makes a LAN listener safe without a CA, and only a native
shell can do it. It reuses the React UI and the bridge (`bridge.ts`), with the shell holding the device token the
way the desktop shell holds the daemon's.

## What it needs from you

- A go-ahead for the second listener: it is the one place crewbots would listen beyond this Mac.
- Signing: an Apple ID in Xcode (free, re-signed weekly) or the Apple Developer Program, and the phone in developer
  mode, to install the build.
- Allowing local network access on the phone the first time it looks for the Mac.
