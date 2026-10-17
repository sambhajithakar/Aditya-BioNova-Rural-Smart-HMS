/// Login flow widget tests: the form posts the real `POST /auth/login`
/// contract through the fake transport, routes into the role shell on
/// success, and shows the server's `detail` verbatim on failure.
library;

import 'package:aditya_bionova_mobile/auth/session.dart';
import 'package:aditya_bionova_mobile/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_transport.dart';

AppGraph graphWith(FakeTransport fake) => AppGraph(
      baseUrl: 'http://test',
      transportOverride: fake,
      tokenStore: MemoryTokenStore(),
    );

void main() {
  testWidgets('valid credentials route into the role-aware shell',
      (tester) async {
    final fake = FakeTransport.routes({
      'POST /auth/login': json({
        'tokens': {'access_token': 'AT', 'refresh_token': 'RT'},
      }),
      'GET /auth/me': json({'role': 'doctor', 'user': {'full_name': 'Dr X'}}),
      'GET /patients': json({
        'items': [
          {'id': 'p1', 'uhid': 'P0001', 'full_name': 'Walk In'},
        ],
        'total': 1,
      }),
      'GET /branches': json([
        {'id': 'b1', 'name': 'Main', 'code': 'MAIN'},
      ]),
    });

    await tester.pumpWidget(HmsApp(graph: graphWith(fake)));
    expect(find.text('Sign in'), findsOneWidget);

    await tester.enterText(
        find.byKey(const Key('login-email')), 'dr@clinic.test');
    await tester.enterText(
        find.byKey(const Key('login-password')), 'secret');
    await tester.tap(find.byKey(const Key('login-submit')));
    await tester.pumpAndSettle();

    // landed on the shell with the role from /auth/me
    expect(find.text('HMS · doctor'), findsOneWidget);
    expect(find.byKey(const Key('queue-add')), findsOneWidget);

    // slug omitted when blank; login hit exactly once
    final logins =
        fake.calls.where((c) => c.path == '/auth/login').toList(growable: false);
    expect(logins, hasLength(1));
    expect(logins.single.body!.containsKey('organization_slug'), isFalse);
  });

  testWidgets('failed sign-in shows the server detail and stays put',
      (tester) async {
    final fake = FakeTransport.routes({
      'POST /auth/login':
          json({'detail': 'Invalid email or password'}, status: 401),
    });

    await tester.pumpWidget(HmsApp(graph: graphWith(fake)));
    await tester.enterText(
        find.byKey(const Key('login-email')), 'dr@clinic.test');
    await tester.enterText(find.byKey(const Key('login-password')), 'wrong');
    await tester.tap(find.byKey(const Key('login-submit')));
    await tester.pumpAndSettle();

    expect(find.byKey(const Key('login-error')), findsOneWidget);
    expect(find.textContaining('Invalid email or password'), findsOneWidget);
    // still on the login screen (no shell, no token stored)
    expect(find.byKey(const Key('login-submit')), findsOneWidget);
    expect(find.byKey(const Key('queue-add')), findsNothing);
  });
}
