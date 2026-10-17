/// Sync engine: pushes the outbox in order, classifying every outcome.
///
/// * 2xx  -> op applied and removed (replay-safe via `Idempotency-Key`);
///   when the op CREATED an entity its returned id is recorded in the
///   [IdMap], so later ops referring to it as `$ref:…` can push;
/// * 409/412 -> [ConflictKind.versionMismatch] — op parked as conflicted;
/// * 422  -> [ConflictKind.rejectedByPolicy] — parked, never auto-retried;
/// * 401  -> [ConflictKind.authRequired] and the run STOPS (nothing else
///   can push without a session);
/// * 5xx / network error -> op kept as `failed` with the error recorded;
///   the next sync (after `Outbox.retryOpen`) tries again.
/// * an op whose payload still references an entity that has not synced
///   yet is DEFERRED to the next run — never pushed with a bogus id.
///
/// Each op also carries `X-Base-Version`, the server version the device
/// based its change on, so the API can refuse stale writes even for
/// endpoints without a natural idempotency story.
library;

import '../api/transport.dart';
import '../auth/session.dart';
import 'conflict.dart';
import 'idmap.dart';
import 'outbox.dart';

class SyncResult {
  const SyncResult({
    required this.pushed,
    required this.conflicts,
    required this.failed,
    this.deferred = 0,
    this.synced = const <String>[],
    this.stoppedUnauthorized = false,
  });

  final int pushed;
  final List<Conflict> conflicts;
  final int failed;

  /// Ops left pending because something they reference has not synced yet.
  final int deferred;

  /// Entity labels pushed successfully in this run (the "Synced" state).
  final List<String> synced;

  final bool stoppedUnauthorized;

  bool get clean =>
      failed == 0 &&
      conflicts.isEmpty &&
      deferred == 0 &&
      !stoppedUnauthorized;
}

class SyncEngine {
  SyncEngine({
    required this.transport,
    required this.session,
    required this.outbox,
    IdMap? idMap,
    this.onPersist,
  }) : idMap = idMap ?? IdMap();

  final ApiTransport transport;
  final Session session;
  final Outbox outbox;
  final IdMap idMap;

  /// Called after every run so the graph can seal the outbox + id map into
  /// the encrypted store (restart keeps both).
  final Future<void> Function()? onPersist;

  Future<SyncResult> sync() async {
    var pushed = 0;
    var failed = 0;
    var deferred = 0;
    final conflicts = <Conflict>[];
    final synced = <String>[];

    for (final op in List<OutboxOp>.of(outbox.pending)) {
      final token = await session.accessToken();
      if (token == null) {
        conflicts.add(Conflict(
          op: op,
          kind: ConflictKind.authRequired,
          detail: 'Not signed in',
        ));
        return _finish(
          pushed: pushed,
          conflicts: conflicts,
          failed: failed,
          deferred: deferred,
          synced: synced,
          stoppedUnauthorized: true,
        );
      }

      // Substitute $ref:<local> placeholders with real server ids. A
      // reference that has not synced yet defers this op to the next run.
      final payload = idMap.substitute(op.payload);
      if (payload == null) {
        deferred += 1;
        continue;
      }

      op.status = OpStatus.inFlight;
      final ApiResponse res;
      try {
        res = await transport.send(
          method: op.method,
          path: op.path,
          body: payload,
          headers: {
            'Authorization': 'Bearer $token',
            'Idempotency-Key': op.id,
            'X-Base-Version': '${op.baseVersion}',
          },
        );
      } catch (err) {
        // Airplane mode / server down: keep the op, try again next time.
        op.attempts += 1;
        op.lastError = err.toString();
        op.status = OpStatus.failed;
        failed += 1;
        continue;
      }

      if (res.ok) {
        final created = res.body['id'];
        if (op.localRef != null && created is String) {
          idMap.put(op.localRef!, created);
        }
        outbox.remove(op);
        pushed += 1;
        synced.add(op.entity);
        continue;
      }

      if (res.status == 401) {
        op.status = OpStatus.pending; // nothing pushed for this op
        conflicts.add(Conflict(
          op: op,
          kind: ConflictKind.authRequired,
          detail: res.detail,
        ));
        return _finish(
          pushed: pushed,
          conflicts: conflicts,
          failed: failed,
          deferred: deferred,
          synced: synced,
          stoppedUnauthorized: true,
        );
      }

      if (res.status == 409 || res.status == 412) {
        op.status = OpStatus.conflicted;
        op.lastError = res.detail;
        conflicts.add(Conflict(
          op: op,
          kind: ConflictKind.versionMismatch,
          detail: res.detail,
          serverState: res.body,
        ));
        continue;
      }

      if (res.status >= 400 && res.status < 500) {
        // Client-side rejection: retrying the same payload would fail the
        // same way — park it for a human.
        op.status = OpStatus.conflicted;
        op.lastError = res.detail;
        conflicts.add(Conflict(
          op: op,
          kind: ConflictKind.rejectedByPolicy,
          detail: res.detail,
          serverState: res.body,
        ));
        continue;
      }

      // 5xx (or anything else transient): retryable failure.
      op.attempts += 1;
      op.lastError = res.detail;
      op.status = OpStatus.failed;
      failed += 1;
    }

    return _finish(
      pushed: pushed,
      conflicts: conflicts,
      failed: failed,
      deferred: deferred,
      synced: synced,
    );
  }

  Future<SyncResult> _finish({
    required int pushed,
    required List<Conflict> conflicts,
    required int failed,
    required int deferred,
    required List<String> synced,
    bool stoppedUnauthorized = false,
  }) async {
    outbox.markDirty();
    await onPersist?.call();
    return SyncResult(
      pushed: pushed,
      conflicts: conflicts,
      failed: failed,
      deferred: deferred,
      synced: synced,
      stoppedUnauthorized: stoppedUnauthorized,
    );
  }
}
