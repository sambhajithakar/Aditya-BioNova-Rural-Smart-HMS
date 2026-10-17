/// SyncEngine unit tests: the push outcome matrix from M4's spec —
/// success clears the op (replay-safe), conflicts park it (no
/// last-write-wins), transient failures stay queued, and a dead session
/// stops the run without dropping anything.
library;

import 'package:aditya_bionova_mobile/auth/session.dart';
import 'package:aditya_bionova_mobile/sync/conflict.dart';
import 'package:aditya_bionova_mobile/sync/engine.dart';
import 'package:aditya_bionova_mobile/sync/outbox.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_transport.dart';

OutboxOp queueOp(Outbox outbox, {String dedupeKey = 'k1', int baseVersion = 0}) =>
    outbox.enqueue(
      entity: 'queue_entry',
      method: 'POST',
      path: '/opd/queue',
      payload: {'patient_id': 'p1', 'branch_id': 'b1'},
      dedupeKey: dedupeKey,
      baseVersion: baseVersion,
    );

void main() {
  late Session session;
  late Outbox outbox;

  setUp(() {
    session = Session(MemoryTokenStore());
    outbox = Outbox();
  });

  Future<void> signIn() =>
      session.save(accessToken: 'tok-123', refreshToken: 'ref');

  test('pushes in order with bearer, Idempotency-Key and base version',
      () async {
    await signIn();
    final first = queueOp(outbox, dedupeKey: 'a', baseVersion: 7);
    final second = queueOp(outbox, dedupeKey: 'b');
    final ids = [first.id, second.id];

    final fake = FakeTransport((c) async => json({'id': 'q'}, status: 201));
    final engine = SyncEngine(transport: fake, session: session, outbox: outbox);

    final result = await engine.sync();

    expect(result.pushed, 2);
    expect(result.clean, isTrue);
    expect(outbox.total, 0);
    expect(fake.calls.map((c) => c.path), ['/opd/queue', '/opd/queue']);
    final h1 = fake.calls[0].headers!;
    expect(h1['Authorization'], 'Bearer tok-123');
    expect(h1['Idempotency-Key'], ids[0]);
    expect(h1['X-Base-Version'], '7');
    expect(fake.calls[1].headers!['Idempotency-Key'], ids[1]);
    expect(fake.calls[1].headers!['X-Base-Version'], '0');
  });

  test('409 parks the op as a version conflict and is never auto-retried',
      () async {
    await signIn();
    queueOp(outbox);
    final fake = FakeTransport(
        (c) async => json({'detail': 'row changed underneath'}, status: 409));
    final engine = SyncEngine(transport: fake, session: session, outbox: outbox);

    final result = await engine.sync();
    expect(result.conflicts, hasLength(1));
    expect(result.conflicts.single.kind, ConflictKind.versionMismatch);
    expect(result.conflicts.single.detail, 'row changed underneath');
    expect(outbox.total, 1);
    expect(outbox.ops.single.status, OpStatus.conflicted);

    // a second sync must NOT touch a conflicted op
    final again = await engine.sync();
    expect(again.pushed, 0);
    expect(fake.calls, hasLength(1));
  });

  test('422 is a policy rejection: parked, not retried, not dropped',
      () async {
    await signIn();
    queueOp(outbox);
    final fake = FakeTransport(
        (c) async => json({'detail': 'Unknown or inactive drug(s)'}, status: 422));
    final engine = SyncEngine(transport: fake, session: session, outbox: outbox);

    final result = await engine.sync();
    expect(result.conflicts.single.kind, ConflictKind.rejectedByPolicy);
    expect(outbox.ops.single.status, OpStatus.conflicted);
    expect(outbox.ops.single.lastError, 'Unknown or inactive drug(s)');
  });

  test('network failure stays queued; retryOpen then a later sync lands it',
      () async {
    await signIn();
    queueOp(outbox);
    var online = false;
    final fake = FakeTransport((c) async {
      if (!online) throw Exception('network down');
      return json({'id': 'q'}, status: 201);
    });
    final engine = SyncEngine(transport: fake, session: session, outbox: outbox);

    final offline = await engine.sync();
    expect(offline.failed, 1);
    expect(outbox.ops.single.status, OpStatus.failed);
    expect(outbox.ops.single.attempts, 1);
    expect(outbox.ops.single.lastError, contains('network down'));

    outbox.retryOpen();
    expect(outbox.ops.single.status, OpStatus.pending);

    online = true;
    final onlineResult = await engine.sync();
    expect(onlineResult.pushed, 1);
    expect(outbox.total, 0);
  });

  test('5xx is retryable: op kept as failed, retryOpen re-queues it',
      () async {
    await signIn();
    queueOp(outbox);
    final fake = FakeTransport((c) async => json({'detail': 'oops'}, status: 503));
    final engine = SyncEngine(transport: fake, session: session, outbox: outbox);

    final result = await engine.sync();
    expect(result.failed, 1);
    expect(result.conflicts, isEmpty);
    expect(outbox.ops.single.status, OpStatus.failed);
    expect(outbox.ops.single.lastError, 'oops');

    outbox.retryOpen();
    expect(outbox.count(OpStatus.pending), 1);
  });

  test('401 stops the run and leaves the op pending for re-login', () async {
    await signIn();
    queueOp(outbox, dedupeKey: 'a');
    queueOp(outbox, dedupeKey: 'b');
    final fake =
        FakeTransport((c) async => json({'detail': 'Token expired'}, status: 401));
    final engine = SyncEngine(transport: fake, session: session, outbox: outbox);

    final result = await engine.sync();
    expect(result.stoppedUnauthorized, isTrue);
    expect(result.conflicts.single.kind, ConflictKind.authRequired);
    expect(fake.calls, hasLength(1)); // second op never attempted
    expect(outbox.count(OpStatus.pending), 2); // nothing dropped
  });

  test('no session at all fails fast without hitting the network', () async {
    queueOp(outbox);
    final fake = FakeTransport((c) async => json({}));
    final engine = SyncEngine(transport: fake, session: session, outbox: outbox);

    final result = await engine.sync();
    expect(result.stoppedUnauthorized, isTrue);
    expect(result.conflicts.single.kind, ConflictKind.authRequired);
    expect(fake.calls, isEmpty);
    expect(outbox.ops.single.status, OpStatus.pending);
  });
}
