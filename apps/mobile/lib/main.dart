/// Aditya BioNova Smart HMS — offline-first mobile app (M4).
///
/// `main()` wires one [AppGraph] and hands it to the UI. The graph owns
/// the session, transport, client, **encrypted local store**, directory
/// cache, outbox and sync engine; on the device the store and the session
/// live in the Android Keystore-backed storage, so an app restart resumes
/// exactly where it stopped (pending ops, cached directory, id map).
///
/// The API base defaults to the Android emulator loopback (`10.0.2.2`) and
/// can be overridden per build:
///
///   flutter run --dart-define=API_BASE=http://192.168.1.50:8000
library;

import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';

import 'api/client.dart';
import 'api/transport.dart';
import 'auth/session.dart';
import 'data/directory.dart';
import 'data/rx_drafts.dart';
import 'screens/login_screen.dart';
import 'store/local_store.dart';
import 'store/platform_stores.dart';
import 'sync/engine.dart';
import 'sync/idmap.dart';
import 'sync/outbox.dart';

/// Everything the app shares. Constructed once in [main] so screens and
/// the sync engine always see the same session, directory and outbox.
class AppGraph {
  AppGraph({
    required this.baseUrl,
    TokenStore? tokenStore,
    ApiTransport? transportOverride,
    LocalStore? store,
  })  : store = store ?? MemoryLocalStore(),
        session = Session(tokenStore ?? MemoryTokenStore()) {
    transport = transportOverride ?? HttpTransport(baseUrl: baseUrl);
    client = HmsClient(transport: transport, session: session);
    directory = DirectoryCache(this.store)..load();
    drafts = RxDraftBook(this.store)..load();
    idMap = _readIdMap(this.store.doc);
    outbox = _readOutbox(this.store.doc);
    outbox.onChanged = _persistOutbox;
    engine = SyncEngine(
      transport: transport,
      session: session,
      outbox: outbox,
      idMap: idMap,
      onPersist: () async {
        await _persistOutbox();
        await this.store.saveSection('idmap', idMap.toJson());
      },
    );
  }

  /// Open [store] (decrypting the offline document) and restore everything
  /// a restart must keep: pending ops, directory cache, id map.
  static Future<AppGraph> open({
    required String baseUrl,
    TokenStore? tokenStore,
    ApiTransport? transportOverride,
    LocalStore? store,
  }) async {
    final resolved = store ?? MemoryLocalStore();
    await resolved.open();
    return AppGraph(
      baseUrl: baseUrl,
      tokenStore: tokenStore,
      transportOverride: transportOverride,
      store: resolved,
    );
  }

  final String baseUrl;
  final Session session;
  final LocalStore store;
  late final DirectoryCache directory;
  late final RxDraftBook drafts;
  late final IdMap idMap;
  late final Outbox outbox;
  late final ApiTransport transport;
  late final HmsClient client;
  late final SyncEngine engine;

  Future<void> _persistOutbox() =>
      store.saveSection('outbox', jsonDecode(outbox.toJson()));

  /// Wait until every queued local write has been sealed to storage —
  /// call before the app can be killed (backgrounding, tests, sign-out).
  Future<void> flush() => store.flush();

  static Outbox _readOutbox(Map<String, dynamic> doc) {
    final raw = doc['outbox'];
    return Outbox.fromJson(jsonEncode(raw is Map ? raw : const {'ops': []}));
  }

  static IdMap _readIdMap(Map<String, dynamic> doc) {
    final raw = doc['idmap'];
    return raw is Map<String, dynamic> ? IdMap.fromJson(raw) : IdMap();
  }
}

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(HmsApp(graph: await _deviceGraph()));
}

/// Device wiring: Keystore-backed session + AES-sealed offline store under
/// the app documents directory. If the platform plugins are unavailable
/// (e.g. running on a host VM) the app still starts, with the in-memory
/// stores — nothing is silently lost, only not persisted.
Future<AppGraph> _deviceGraph() async {
  const baseUrl = String.fromEnvironment(
    'API_BASE',
    defaultValue: 'http://10.0.2.2:8000',
  );
  try {
    final docs = await getApplicationDocumentsDirectory();
    return await AppGraph.open(
      baseUrl: baseUrl,
      tokenStore: SecureTokenStore(),
      store: EncryptedFileStore(
        file: File('${docs.path}/hms_offline.json'),
        keys: SecureKeyProvider(),
      ),
    );
  } catch (_) {
    return AppGraph(baseUrl: baseUrl);
  }
}

class HmsApp extends StatelessWidget {
  const HmsApp({super.key, required this.graph});

  final AppGraph graph;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Aditya BioNova Smart HMS',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorSchemeSeed: const Color(0xFF0B3B5C),
        useMaterial3: true,
      ),
      home: LoginScreen(graph: graph),
    );
  }
}
