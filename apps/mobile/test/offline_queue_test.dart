/// Offline queue workflow widget test — the M4 acceptance slice:
/// add a walk-in to the queue (offline-safe), tapping twice keeps ONE op,
/// then the sync centre pushes it with an Idempotency-Key and the outbox
/// drains.
library;

import 'package:aditya_bionova_mobile/auth/session.dart';
import 'package:aditya_bionova_mobile/main.dart';
import 'package:aditya_bionova_mobile/screens/home_screen.dart';
import 'package:aditya_bionova_mobile/sync/outbox.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_transport.dart';

void main() {
  testWidgets('queue add dedupes offline, then sync drains the outbox',
      (tester) async {
    final fake = FakeTransport.routes({
      'GET /auth/me': json({'role': 'receptionist'}),
      'GET /patients': json({
        'items': [
          {'id': 'p1', 'uhid': 'P0001', 'full_name': 'Walk In'},
        ],
        'total': 1,
      }),
      'GET /branches': json([
        {'id': 'b1', 'name': 'Main', 'code': 'MAIN'},
      ]),
      'POST /opd/queue': json({'id': 'q1', 'token_number': 3}, status: 201),
    });

    final graph = AppGraph(
      baseUrl: 'http://test',
      transportOverride: fake,
      tokenStore: MemoryTokenStore(),
    );
    await graph.session.save(accessToken: 'tok', refreshToken: 'r');

    await tester.pumpWidget(MaterialApp(home: HomeScreen(graph: graph)));
    await tester.pumpAndSettle();

    // directory loaded; defaults picked
    expect(find.byKey(const Key('queue-patient')), findsOneWidget);
    expect(find.textContaining('P0001 Walk In'), findsWidgets);

    // tap twice while "offline": one op only
    await tester.tap(find.byKey(const Key('queue-add')));
    await tester.pump();
    expect(graph.outbox.total, 1);
    expect(graph.outbox.ops.single.status, OpStatus.pending);
    expect(graph.outbox.ops.single.dedupeKey, startsWith('queue:p1:'));

    await tester.tap(find.byKey(const Key('queue-add')));
    await tester.pump();
    expect(graph.outbox.total, 1); // duplicate prevented
    expect(find.textContaining('duplicate prevented'), findsOneWidget);

    // no queue POST reached the network while offline-enqueued
    expect(fake.calls.where((c) => c.method == 'POST'), isEmpty);

    // sync centre: push it
    await tester.tap(find.byIcon(Icons.sync_outlined));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('sync-run')));
    await tester.pumpAndSettle();

// the result line IS the keyed widget, so match it directly
    expect(find.byKey(const Key('sync-result')), findsOneWidget);
    expect(
      find.textContaining('pushed 1 · conflicts 0 · failed 0'),
      findsOneWidget,
    );
    expect(graph.outbox.total, 0); // drained

    final push = fake.calls.firstWhere((c) => c.method == 'POST');
    expect(push.path, '/opd/queue');
    expect(push.body!['patient_id'], 'p1');
    expect(push.body!['branch_id'], 'b1');
    expect(push.headers!['Idempotency-Key'], isNotEmpty);
    expect(push.headers!['Authorization'], 'Bearer tok');
  });

  testWidgets('failed push stays visible and Retry re-queues it',
      (tester) async {
    var serverUp = false;
    final fake = FakeTransport((c) async {
      switch (c.route) {
        case 'GET /auth/me':
          return json({'role': 'receptionist'});
        case 'GET /patients':
          return json({
            'items': [
              {'id': 'p1', 'uhid': 'P0001', 'full_name': 'Walk In'},
            ],
            'total': 1,
          });
        case 'GET /branches':
          return json([
            {'id': 'b1', 'name': 'Main', 'code': 'MAIN'},
          ]);
        case 'POST /opd/queue':
          if (!serverUp) {
            return json({'detail': 'Bad gateway'}, status: 502);
          }
          return json({'id': 'q1'}, status: 201);
      }
      return json({'detail': 'no stub'}, status: 404);
    });

    final graph = AppGraph(
      baseUrl: 'http://test',
      transportOverride: fake,
      tokenStore: MemoryTokenStore(),
    );
    await graph.session.save(accessToken: 'tok', refreshToken: 'r');

    await tester.pumpWidget(MaterialApp(home: HomeScreen(graph: graph)));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('queue-add')));
    await tester.pump();

    await tester.tap(find.byIcon(Icons.sync_outlined));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('sync-run')));
    await tester.pumpAndSettle();

    expect(find.textContaining('failed 1'), findsOneWidget);
    expect(graph.outbox.count(OpStatus.failed), 1);

    serverUp = true;
    await tester.tap(find.byKey(const Key('sync-retry')));
    await tester.pump();
    await tester.tap(find.byKey(const Key('sync-run')));
    await tester.pumpAndSettle();

    expect(find.textContaining('pushed 1'), findsOneWidget);
    expect(graph.outbox.total, 0);
  });
}
