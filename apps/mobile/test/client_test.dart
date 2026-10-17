/// HmsClient unit tests: login stores tokens, authenticated calls carry
/// the bearer, and 4xx details surface verbatim (both as thrown
/// [ApiException] and raw responses for the sync engine).
library;

import 'package:aditya_bionova_mobile/api/client.dart';
import 'package:aditya_bionova_mobile/api/transport.dart';
import 'package:aditya_bionova_mobile/auth/session.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_transport.dart';

void main() {
  late MemoryTokenStore store;
  late Session session;

  setUp(() {
    store = MemoryTokenStore();
    session = Session(store);
  });

  test('login posts credentials (slug only when given) and stores tokens',
      () async {
    final fake = FakeTransport.routes({
      'POST /auth/login': json({
        'tokens': {'access_token': 'AT', 'refresh_token': 'RT'},
        'role': 'doctor',
      }),
    });
    final client = HmsClient(transport: fake, session: session);

    await client.login(
      email: 'dr@clinic.test',
      password: 'secret',
      organizationSlug: 'clinic-a',
    );

    expect(await store.read(Session.accessTokenKey), 'AT');
    expect(await store.read(Session.refreshTokenKey), 'RT');
    final call = fake.calls.single;
    expect(call.path, '/auth/login');
    expect(call.body!['email'], 'dr@clinic.test');
    expect(call.body!['organization_slug'], 'clinic-a');
// login is unauthenticated: no Authorization header at all
    expect(call.headers?.containsKey('Authorization') ?? false, isFalse);

    // empty slug is omitted entirely
    final fake2 = FakeTransport.routes({
      'POST /auth/login': json({
        'tokens': {'access_token': 'AT', 'refresh_token': 'RT'},
      }),
    });
    final client2 = HmsClient(transport: fake2, session: session);
    await client2.login(email: 'a@b.c', password: 'x');
    expect(fake2.calls.single.body!.containsKey('organization_slug'), isFalse);
  });

  test('authenticated requests carry the session bearer token', () async {
    await session.save(accessToken: 'tok', refreshToken: 'r');
    final fake = FakeTransport.routes({
      'GET /auth/me': json({'role': 'doctor'}),
    });
    final client = HmsClient(transport: fake, session: session);

    final me = await client.me();
    expect(me['role'], 'doctor');
    expect(fake.calls.single.headers!['Authorization'], 'Bearer tok');
  });

  test('sendRaw surfaces a 409 without throwing; request throws the detail',
      () async {
    await session.save(accessToken: 'tok', refreshToken: 'r');
    final fake = FakeTransport(
        (c) async => json({'detail': 'Idempotency-Key conflict'}, status: 409));
    final client = HmsClient(transport: fake, session: session);

    final raw = await client.sendRaw('POST', '/x', body: {'a': 1});
    expect(raw.status, 409);
    expect(raw.detail, 'Idempotency-Key conflict');

    await expectLater(
      client.request('POST', '/x', body: {'a': 1}),
      throwsA(isA<ApiException>()
          .having((e) => e.status, 'status', 409)
          .having((e) => e.detail, 'detail', 'Idempotency-Key conflict')),
    );
  });

  test('401 fires the onUnauthorized callback (session is dead)', () async {
    var expired = false;
    await session.save(accessToken: 'tok', refreshToken: 'r');
    final fake = FakeTransport((c) async => json({'detail': 'x'}, status: 401));
    final client = HmsClient(
      transport: fake,
      session: session,
      onUnauthorized: () => expired = true,
    );

    await client.sendRaw('GET', '/auth/me');
    expect(expired, isTrue);
  });
}
