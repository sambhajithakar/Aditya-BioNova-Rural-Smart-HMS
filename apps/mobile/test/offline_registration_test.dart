/// Offline registration (M4 acceptance slice): a walk-in is registered with
/// no network, deduped on a double-tap, appears in the cached directory
/// straight away, can be put into today's queue — and the sync pushes the
/// registration FIRST, then swaps its server id into the queue entry.
library;

import 'package:aditya_bionova_mobile/auth/session.dart';
import 'package:aditya_bionova_mobile/main.dart';
import 'package:aditya_bionova_mobile/screens/home_screen.dart';
import 'package:aditya_bionova_mobile/store/local_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_transport.dart';

Map<String, dynamic> directoryDoc() => {
      'directory': {
        'patients': [
          {'id': 'p1', 'uhid': 'P0001', 'full_name': 'Aarti Patil'},
        ],
        'branches': [
          {'id': 'b1', 'name': 'Main', 'code': 'MAIN'},
        ],
        'drugs': <dynamic>[],
      },
    };

Future<AppGraph> boot(FakeTransport fake) async {
  final graph = await AppGraph.open(
    baseUrl: 'http://test',
    transportOverride: fake,
    tokenStore: MemoryTokenStore(),
    store: MemoryLocalStore(directoryDoc()),
  );
  await graph.session.save(accessToken: 'tok', refreshToken: 'r');
  return graph;
}

FakeTransport receptionistServer() => FakeTransport.routes({
      'GET /auth/me': json({'role': 'receptionist'}),
      'GET /patients': json({
        'items': [
          {'id': 'p1', 'uhid': 'P0001', 'full_name': 'Aarti Patil'},
        ],
        'total': 1,
      }),
      'GET /branches': json([
        {'id': 'b1', 'name': 'Main', 'code': 'MAIN'},
      ]),
      'POST /patients':
          json({'id': 'srv9', 'uhid': 'P0009'}, status: 201),
      'POST /opd/queue': json({'id': 'q9', 'token_number': 9}, status: 201),
    });

Future<void> fillRegistration(WidgetTester tester) async {
  await tester.enterText(find.byKey(const Key('register-name')), 'Sunita Patil');
  await tester.enterText(find.byKey(const Key('register-phone')), '9876500001');
}

void main() {
  testWidgets('register offline → queued → sync resolves the id for the '
      'queue entry that follows it', (tester) async {
    final fake = receptionistServer();
    final graph = await boot(fake);

    await tester.pumpWidget(MaterialApp(home: HomeScreen(graph: graph)));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Register'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('register-add')), findsOneWidget);

    await fillRegistration(tester);
    await tester.tap(find.byKey(const Key('register-add')));
    await tester.pump();

    expect(find.textContaining('Saved on device'), findsOneWidget);
    expect(graph.outbox.total, 1);
    final op = graph.outbox.ops.single;
    expect(op.entity, 'patient');
    expect(op.localRef, isNotNull);
    expect(
      graph.directory.patients.any((p) => p['full_name'] == 'Sunita Patil'),
      isTrue,
      reason: 'the new patient is usable offline immediately',
    );
    // nothing reached the network while enqueuing
    expect(fake.calls.where((c) => c.method == 'POST'), isEmpty);

    // put the still-unsynced patient into today's queue, offline
    await tester.tap(find.text('Queue'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('queue-patient')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Sunita Patil (pending sync)'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('queue-add')));
    await tester.pump();
    expect(graph.outbox.total, 2);

    // sync: registration first, queue entry second, id substituted
    await tester.tap(find.text('Sync'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('sync-run')));
    await tester.pumpAndSettle();

    expect(find.textContaining('pushed 2'), findsOneWidget);
    final paths = [
      for (final c in fake.calls)
        if (c.method == 'POST') c.route,
    ];
    expect(paths, ['POST /patients', 'POST /opd/queue']);

    final registration =
        fake.calls.firstWhere((c) => c.method == 'POST' && c.path == '/patients');
    expect(
      registration.body!.keys.toSet(),
      {'full_name', 'gender', 'date_of_birth', 'dob_estimated', 'phone', 'allergies'},
      reason: 'nothing device-local (no \$ref) may leak into the API body',
    );
    expect(registration.headers!['Idempotency-Key'], isNotEmpty);

    final queuePush =
        fake.calls.firstWhere((c) => c.method == 'POST' && c.path == '/opd/queue');
    expect(queuePush.body!['patient_id'], 'srv9');
    expect(graph.outbox.total, 0);
    expect(graph.idMap.serverIdOf(op.localRef!), 'srv9');
  });

  testWidgets('registering the same person twice offline keeps ONE op',
      (tester) async {
    final fake = receptionistServer();
    final graph = await boot(fake);

    await tester.pumpWidget(MaterialApp(home: HomeScreen(graph: graph)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Register'));
    await tester.pumpAndSettle();

    await fillRegistration(tester);
    await tester.tap(find.byKey(const Key('register-add')));
    await tester.pump();
    expect(graph.outbox.total, 1);

    // same details again (the fields were cleared after the first save)
    await fillRegistration(tester);
    await tester.tap(find.byKey(const Key('register-add')));
    await tester.pump();

    expect(find.textContaining('duplicate prevented'), findsOneWidget);
    expect(graph.outbox.total, 1, reason: 'duplicate prevented');
    expect(fake.calls.where((c) => c.method == 'POST'), isEmpty);
  });
}
