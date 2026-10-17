/// Restart recovery (M4 acceptance): an app restart with no network must
/// keep every pending mutation, the cached directory and the id map — and
/// the next sync must push them in order, with the registration's server id
/// substituted into the queue entry that references it.
library;

import 'dart:io';
import 'dart:typed_data';

import 'package:aditya_bionova_mobile/auth/session.dart';
import 'package:aditya_bionova_mobile/main.dart';
import 'package:aditya_bionova_mobile/store/local_store.dart';
import 'package:aditya_bionova_mobile/sync/idmap.dart';
import 'package:aditya_bionova_mobile/sync/outbox.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_transport.dart';

void main() {
  late Directory tmpDir;
  late File file;

  setUp(() {
    tmpDir = Directory.systemTemp.createTempSync('hms_restart');
    file = File('${tmpDir.path}/offline.json');
  });

  tearDown(() {
    if (tmpDir.existsSync()) tmpDir.deleteSync(recursive: true);
  });

  EncryptedFileStore store() => EncryptedFileStore(
        file: file,
        keys: FixedKeyProvider(Uint8List.fromList(List<int>.generate(32, (i) => i))),
      );

  Future<AppGraph> boot(FakeTransport fake) async {
    final graph = await AppGraph.open(
      baseUrl: 'http://test',
      transportOverride: fake,
      tokenStore: MemoryTokenStore(),
      store: store(),
    );
    await graph.session.save(accessToken: 'tok', refreshToken: 'r');
    return graph;
  }

  test('offline work survives a restart and syncs with resolved ids',
      () async {
    final serverUp = ValueBox(false);
    final fake = FakeTransport((c) async {
      switch (c.route) {
        case 'GET /auth/me':
          return json({'role': 'receptionist'});
        case 'POST /patients':
          if (!serverUp.value) {
            return json({'detail': 'Network down'}, status: 503);
          }
          return json({'id': 'srv-p1', 'uhid': 'P0042'}, status: 201);
        case 'POST /opd/queue':
          return json({'id': 'q1', 'token_number': 7}, status: 201);
      }
      return json({'detail': 'no stub for ${c.route}'}, status: 404);
    });

    // ---- session 1: register a walk-in, then queue them — all offline ----
    final graph1 = await boot(fake);
    final localRef = Outbox.newId();
    graph1.outbox.enqueue(
      entity: 'patient',
      method: 'POST',
      path: '/patients',
      payload: {
        'full_name': 'Walk In',
        'gender': 'female',
        'phone': '9876543210',
      },
      dedupeKey: 'patient:walk in:9876543210:today',
      localRef: localRef,
    );
    await graph1.directory.addLocalPatient(
      localRef: localRef,
      fullName: 'Walk In',
      gender: 'female',
      phone: '9876543210',
    );
    graph1.outbox.enqueue(
      entity: 'queue_entry',
      method: 'POST',
      path: '/opd/queue',
      payload: {
        'patient_id': IdMap.placeholder(localRef),
        'branch_id': 'b1',
      },
      dedupeKey: 'queue:$localRef:today',
    );

    expect(graph1.outbox.total, 2);
    // nothing may have hit the network while enqueuing offline
    expect(fake.calls, isEmpty);
    await graph1.flush(); // the sealed file is complete before "restart"

    // ---- "restart" the app on the same sealed file ----
    final graph2 = await boot(fake);

    expect(graph2.outbox.total, 2, reason: 'pending ops survive the restart');
    expect(graph2.directory.patientById(localRef), isNotNull,
        reason: 'the cached directory survives the restart');
    expect(graph2.outbox.ops.map((o) => o.localRef), contains(localRef));

    // ---- the server is reachable again: one sync drains everything ----
    serverUp.value = true;
    final result = await graph2.engine.sync();

    expect(result.pushed, 2, reason: 'registration first, then its queue op');
    expect(result.conflicts, isEmpty);
    expect(graph2.outbox.total, 0);
    expect(graph2.idMap.serverIdOf(localRef), 'srv-p1');

    final paths = [for (final c in fake.calls) c.route];
    expect(paths, ['POST /patients', 'POST /opd/queue']);

    final queuePush = fake.calls.last;
    expect(queuePush.body!['patient_id'], 'srv-p1',
        reason: 'the local ref was swapped for the server id');
    expect(queuePush.headers!['Idempotency-Key'], isNotEmpty);

    // nothing named $ref: ever reached the server
    expect(
      fake.calls.any((c) => c.body?.toString().contains('\$ref:') ?? false),
      isFalse,
    );
  });

  test('a dependent op is deferred while its parent is refused, not pushed '
      'with a bogus id', () async {
    final fake = FakeTransport((c) async {
      switch (c.route) {
        case 'POST /patients':
          return json({'detail': 'Possible duplicate registration'},
              status: 409);
        case 'POST /opd/queue':
          return json({'id': 'q1'}, status: 201);
      }
      return json({'detail': 'no stub for ${c.route}'}, status: 404);
    });

    final graph = await boot(fake);
    final localRef = Outbox.newId();
    graph.outbox.enqueue(
      entity: 'patient',
      method: 'POST',
      path: '/patients',
      payload: {'full_name': 'Dup Person', 'gender': 'male'},
      dedupeKey: 'patient:dup:today',
      localRef: localRef,
    );
    graph.outbox.enqueue(
      entity: 'queue_entry',
      method: 'POST',
      path: '/opd/queue',
      payload: {
        'patient_id': IdMap.placeholder(localRef),
        'branch_id': 'b1',
      },
      dedupeKey: 'queue:$localRef:today',
    );

    final result = await graph.engine.sync();

    expect(result.conflicts, hasLength(1));
    expect(result.deferred, 1,
        reason: 'the queue op waits for the registration to be resolved');
    expect(fake.calls.map((c) => c.route), ['POST /patients']);
    expect(graph.outbox.count(OpStatus.conflicted), 1);
    expect(graph.outbox.count(OpStatus.pending), 1);
  });
}

/// Tiny mutable flag holder for the fake server's state.
class ValueBox {
  ValueBox(this.value);
  bool value;
}
