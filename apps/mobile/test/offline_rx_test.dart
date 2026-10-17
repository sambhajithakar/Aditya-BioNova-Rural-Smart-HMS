/// Offline Fast Rx (M4 acceptance slice): a doctor writes a draft from the
/// validated drug catalog with no network; the draft is visibly "pending
/// sync" and CANNOT be signed until the server has accepted it — the
/// allergy gate and the sign permission live server-side, so an offline
/// draft can never become a signed prescription.
library;

import 'package:aditya_bionova_mobile/auth/session.dart';
import 'package:aditya_bionova_mobile/main.dart';
import 'package:aditya_bionova_mobile/screens/home_screen.dart';
import 'package:aditya_bionova_mobile/screens/rx_tab.dart';
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
        'drugs': [
          {
            'id': 'd1',
            'generic_name': 'Amoxicillin',
            'brand_hint': '',
            'drug_class': 'antibiotic',
          },
        ],
      },
    }),
  );
  await graph.session.save(accessToken: 'tok', refreshToken: 'r');
  return graph;
}

void main() {
  testWidgets('draft offline → pending chip → sign unlocks only after sync',
      (tester) async {
    // one tall surface so the whole tab is on screen (no scrolling needed)
    tester.view.physicalSize = const Size(1080, 2600);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final fake = FakeTransport.routes({
      'GET /auth/me': json({'role': 'doctor'}),
      'GET /patients': json({
        'items': [
          {'id': 'p1', 'uhid': 'P0001', 'full_name': 'Aarti Patil'},
        ],
        'total': 1,
      }),
      'GET /branches': json([
        {'id': 'b1', 'name': 'Main', 'code': 'MAIN'},
      ]),
      'POST /prescriptions': json({'id': 'srv-rx', 'status': 'draft'}, status: 201),
      'POST /prescriptions/srv-rx/sign':
          json({'id': 'srv-rx', 'status': 'signed'}),
    });
    final graph = await boot(fake);

    await tester.pumpWidget(MaterialApp(home: HomeScreen(graph: graph)));
    await tester.pumpAndSettle();

    // doctor sees the Rx tab; reception would not
    await tester.tap(find.text('Rx'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('rx-save')), findsOneWidget);

    await tester.enterText(find.byKey(const Key('rx-dose')), '500 mg');
    await tester.enterText(find.byKey(const Key('rx-frequency')), 'BD');
    await tester.ensureVisible(find.byKey(const Key('rx-add-item')));
    await tester.tap(find.byKey(const Key('rx-add-item')));
    await tester.pump();
    await tester.enterText(
        find.byKey(const Key('rx-diagnosis')), 'Enteric fever');
    await tester.ensureVisible(find.byKey(const Key('rx-save')));
    await tester.tap(find.byKey(const Key('rx-save')));
    await tester.pump();

    expect(find.textContaining('Saved on device'), findsOneWidget);
    final op = graph.outbox.ops.single;
    expect(op.entity, 'prescription');
    expect(op.payload['items'], hasLength(1));
    expect(op.payload['items'][0]['drug_id'], 'd1',
        reason: 'medicines come from the validated catalog, never free text');
    expect(fake.calls.where((c) => c.method == 'POST'), isEmpty);

    // the draft is listed as pending, and signing is locked
    expect(find.textContaining('Saved on device · Pending sync'), findsOneWidget);
    final locked = tester
        .widget<TextButton>(find.byKey(Key('rx-sign-${op.localRef}')));
    expect(locked.onPressed, isNull, reason: 'signing is online-only');
    expect(find.text('Sign (after sync)'), findsOneWidget);

    // sync the draft, come back, refresh
    await tester.tap(find.text('Sync'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('sync-run')));
    await tester.pumpAndSettle();
    expect(find.textContaining('pushed 1'), findsOneWidget);

    await tester.tap(find.text('Rx'));
    await tester.pumpAndSettle();
    await tester.tap(find.descendant(
      of: find.byType(RxTab),
      matching: find.byIcon(Icons.refresh),
    ));
    await tester.pumpAndSettle();

    expect(find.text('Synced'), findsOneWidget);
    final unlocked =
        tester.widget<TextButton>(find.byKey(Key('rx-sign-${op.localRef}')));
    expect(unlocked.onPressed, isNotNull, reason: 'server accepted the draft');

    await tester.ensureVisible(find.byKey(Key('rx-sign-${op.localRef}')));
    await tester.tap(find.byKey(Key('rx-sign-${op.localRef}')));
    await tester.pumpAndSettle();

    final sign = fake.calls.firstWhere((c) => c.route == 'POST /prescriptions/srv-rx/sign');
    expect(sign.path, '/prescriptions/srv-rx/sign');
    expect(find.text('Signed'), findsOneWidget);
  });

  testWidgets('a receptionist gets no Rx tab (role gate)', (tester) async {
    final fake = FakeTransport.routes({
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
    });
    final graph = await boot(fake);

    await tester.pumpWidget(MaterialApp(home: HomeScreen(graph: graph)));
    await tester.pumpAndSettle();

    expect(find.text('Rx'), findsNothing);
    await tester.tap(find.text('Register'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('register-add')), findsOneWidget);
  });
}
