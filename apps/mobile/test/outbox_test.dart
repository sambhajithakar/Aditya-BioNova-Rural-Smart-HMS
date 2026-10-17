/// Outbox unit tests: duplicate prevention, status handling and the
/// serialize/restore round trip that makes an app restart safe offline.
library;

import 'package:aditya_bionova_mobile/sync/outbox.dart';
import 'package:flutter_test/flutter_test.dart';

OutboxOp enqueue(Outbox outbox, {String dedupeKey = 'k1', String path = '/x'}) =>
    outbox.enqueue(
      entity: 'thing',
      method: 'POST',
      path: path,
      payload: {'a': 1},
      dedupeKey: dedupeKey,
    );

void main() {
  test('enqueue creates a pending op with a client id', () {
    final outbox = Outbox();
    final op = enqueue(outbox);
    expect(outbox.total, 1);
    expect(op.status, OpStatus.pending);
    expect(op.id, isNotEmpty);
    expect(op.id.contains('-'), isTrue); // uuid-shaped
  });

  test('duplicate dedupe keys collapse to ONE open op', () {
    final outbox = Outbox();
    final first = enqueue(outbox, dedupeKey: 'queue:p1:2026-10-16');
    final second = enqueue(outbox, dedupeKey: 'queue:p1:2026-10-16');
    expect(identical(first, second), isTrue);
    expect(outbox.total, 1);
    // a different patient/day is a different op
    enqueue(outbox, dedupeKey: 'queue:p2:2026-10-16');
    expect(outbox.total, 2);
  });

  test('conflicted ops no longer block their dedupe key', () {
    final outbox = Outbox();
    final op = enqueue(outbox, dedupeKey: 'rx:1');
    op.status = OpStatus.conflicted;
    expect(outbox.openOpFor('rx:1'), isNull);
    enqueue(outbox, dedupeKey: 'rx:1');
    expect(outbox.total, 2);
  });

  test('retryOpen re-queues failed ops only when asked', () {
    final outbox = Outbox();
    final failed = enqueue(outbox, dedupeKey: 'a')..status = OpStatus.failed;
    final conflicted = enqueue(outbox, dedupeKey: 'b')
      ..status = OpStatus.conflicted;
    outbox.retryOpen();
    expect(failed.status, OpStatus.pending);
    expect(conflicted.status, OpStatus.conflicted);
    outbox.retryOpen(includeConflicted: true);
    expect(conflicted.status, OpStatus.pending);
  });

  test('resolveConflict keeps or discards the local change explicitly', () {
    final outbox = Outbox();
    final keep = enqueue(outbox, dedupeKey: 'keep')..status = OpStatus.conflicted;
    final drop = enqueue(outbox, dedupeKey: 'drop')..status = OpStatus.conflicted;
    outbox.resolveConflict(keep, keepLocal: true);
    outbox.resolveConflict(drop, keepLocal: false);
    expect(keep.status, OpStatus.pending);
    expect(outbox.ops.contains(drop), isFalse);
    expect(outbox.total, 1);
  });

  test('serialize/restore survives an app restart', () {
    final outbox = Outbox();
    enqueue(outbox, dedupeKey: 'queue:p1:2026-10-16', path: '/opd/queue');
    enqueue(outbox, dedupeKey: 'rx:1', path: '/prescriptions')
      ..status = OpStatus.failed
      ..attempts = 3
      ..lastError = 'boom';

    final restored = Outbox.fromJson(outbox.toJson());
    expect(restored.total, 2);
    final failed = restored.ops.firstWhere((o) => o.dedupeKey == 'rx:1');
    expect(failed.status, OpStatus.failed);
    expect(failed.attempts, 3);
    expect(failed.lastError, 'boom');
    expect(failed.method, 'POST');
    expect(failed.payload['a'], 1);
    // ids survive too: replay keeps the same Idempotency-Key
    expect(
      restored.ops.map((o) => o.id).toSet(),
      outbox.ops.map((o) => o.id).toSet(),
    );
  });

  test('newId produces distinct uuid-shaped ids', () {
    final ids = {for (var i = 0; i < 100; i++) Outbox.newId()};
    expect(ids.length, 100);
    expect(Outbox.newId().length, 36);
  });
}
