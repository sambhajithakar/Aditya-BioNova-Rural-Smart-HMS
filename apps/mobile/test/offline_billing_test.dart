/// Offline billing (M4 acceptance slice): an invoice is raised with no
/// network, deduped on a double-tap, and pushed with its Idempotency-Key.
/// The device sends the lines only — the server computes the totals.
library;

import 'package:aditya_bionova_mobile/auth/session.dart';
import 'package:aditya_bionova_mobile/main.dart';
import 'package:aditya_bionova_mobile/screens/home_screen.dart';
import 'package:aditya_bionova_mobile/store/local_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_transport.dart';

Future<AppGraph> boot(FakeTransport fake) async {
  final graph = await AppGraph.open(
    baseUrl: 'http://test',
    transportOverride: fake,
    tokenStore: MemoryTokenStore(),
    store: MemoryLocalStore({
      'directory': {
        'patients': [
          {'id': 'p1', 'uhid': 'P0001', 'full_name': 'Aarti Patil'},
        ],
        'branches': [
          {'id': 'b1', 'name': 'Main', 'code': 'MAIN'},
        ],
        'drugs': <dynamic>[],
      },
    }),
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
      'POST /billing/invoices': json(
        {'id': 'inv9', 'invoice_number': 'INV-000009', 'total': 200},
        status: 201,
      ),
    });

Future<void> fillLine(WidgetTester tester) async {
  await tester.enterText(
      find.byKey(const Key('billing-description')), 'Consultation fee');
  await tester.enterText(find.byKey(const Key('billing-price')), '200');
}

void main() {
  testWidgets('raise an invoice offline, dedupe it, push it idempotently',
      (tester) async {
    final fake = receptionistServer();
    final graph = await boot(fake);

    await tester.pumpWidget(MaterialApp(home: HomeScreen(graph: graph)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Billing'));
    await tester.pumpAndSettle();

    await fillLine(tester);
    await tester.tap(find.byKey(const Key('billing-add')));
    await tester.pump();

    expect(find.textContaining('saved on device'), findsOneWidget);
    expect(graph.outbox.total, 1);
    expect(fake.calls.where((c) => c.method == 'POST'), isEmpty);

    // the same line again: one op only
    await fillLine(tester);
    await tester.tap(find.byKey(const Key('billing-add')));
    await tester.pump();
    expect(find.textContaining('duplicate prevented'), findsOneWidget);
    expect(graph.outbox.total, 1);

    await tester.tap(find.text('Sync'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('sync-run')));
    await tester.pumpAndSettle();

    expect(find.textContaining('pushed 1'), findsOneWidget);
    final push = fake.calls.singleWhere((c) => c.method == 'POST');
    expect(push.path, '/billing/invoices');
    expect(push.body!['patient_id'], 'p1');
    expect(push.body!['branch_id'], 'b1');
    expect(push.body!['items'][0]['description'], 'Consultation fee');
    expect(push.body!['items'][0]['unit_price'], 200);
    expect(push.headers!['Idempotency-Key'], isNotEmpty);
    expect(graph.outbox.total, 0);
  });
}
