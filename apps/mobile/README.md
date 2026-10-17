# Aditya BioNova Smart HMS — mobile app (M4)

Offline-first Android app for doctors and field staff, sharing the same
FastAPI backend as the web app. One binary serves both roles: the **Staff
app** (register → queue → billing) and the **Doctor app** (queue → Fast Rx),
switched by the role the server reports on `GET /auth/me`.

## What "offline-first" means here

| Concern | Implementation |
| --- | --- |
| Local storage | One AES-256-GCM sealed file (`EncryptedFileStore`) holding named sections; the AES key lives in the Android Keystore (`flutter_secure_storage`), never in the file |
| Session | Keystore-backed token store (`SecureTokenStore`) |
| Mutations | Every change is an `OutboxOp` with a client-generated UUID used as the server `Idempotency-Key` — a replay can never double-apply |
| Duplicate prevention | One open op per `dedupeKey` (same patient into today's queue twice stays ONE op), plus the server's idempotency store as the backstop |
| Offline-created entities | A patient registered offline gets a device id; later ops refer to it as `$ref:<local>` and the sync engine swaps in the server id before pushing. A reference that has not synced yet DEFERS the dependent op instead of pushing a bogus id |
| Conflicts | 409/412 → parked as `conflicted`, resolved by a human ("Keep local (re-send)" / "Discard"). **No last-write-wins on clinical data.** |
| Explicit sync states | Saved on device · Pending sync / Syncing… / Synced / Conflict — shown per queued item in the Sync centre |
| Restart recovery | Outbox, directory cache, id map and Rx drafts are persisted on every change; `AppGraph.open()` restores them at start-up |
| Signing a prescription | Online-only on purpose: the API's blocking allergy check and the `prescription.sign` permission are server-side. An offline draft can never become a signed prescription |

## Layout

```
lib/
  main.dart              AppGraph (session + store + outbox + engine) & app entry
  api/                   transport (ApiTransport) + HmsClient
  auth/session.dart      TokenStore abstraction (memory on tests, Keystore on device)
  store/local_store.dart LocalStore, MemoryLocalStore, EncryptedFileStore (AES-GCM)
  store/platform_stores.dart  SecureTokenStore, SecureKeyProvider (Keystore)
  data/directory.dart    cached patients / branches / drug catalog
  data/rx_drafts.dart    prescription drafts written on this device
  sync/outbox.dart       offline queue (dedupe, statuses, serialize/restore)
  sync/idmap.dart        local → server id substitution
  sync/engine.dart       push, classify, park conflicts, persist
  sync/conflict.dart     conflict model (no silent resolution)
  screens/               shell + Queue / Register / Rx / Billing / Sync tabs
test/                    40 tests: outbox, engine, id map, encrypted store,
                         restart recovery, and the offline workflow slices
```

## Running

```bash
flutter pub get
flutter analyze
flutter test

# against a local API (the Android emulator reaches the host via 10.0.2.2)
flutter run --dart-define=API_BASE=http://10.0.2.2:8000
```

## Release signing (keys never committed)

`android/app/build.gradle.kts` reads `android/key.properties`; that file and
any `*.keystore` / `*.jks` are excluded by `android/.gitignore`, so key
material cannot enter the repository. Without the file the release build
falls back to the debug key — fine for local testing, not distributable.

One-time setup on the release machine:

```bash
keytool -genkeypair -v -keystore ~/keys/aditya-bionova-release.jks \
    -keyalg RSA -keysize 2048 -validity 10000 -alias bionova

cat > android/key.properties <<'EOF'
storeFile=/absolute/path/to/aditya-bionova-release.jks
storePassword=…
keyAlias=bionova
keyPassword=…
EOF

flutter build apk --release
```

## Not yet done (labelled, not claimed)

- On-device/emulator runs: airplane-mode matrix, restart recovery and
  duplicate prevention have been proven in the Dart test harness against a
  fake transport, **not** yet on entry-level physical hardware (M4 device QA).
- Notifications, vitals/device capture and the encounter workspace are not
  in the app yet; they ride on the same outbox/engine and are listed in
  `TASKS.md`.
