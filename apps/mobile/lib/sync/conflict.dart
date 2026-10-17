/// Conflicts raised while pushing the outbox.
///
/// The blueprint forbids last-write-wins on clinical data: when the server
/// refuses a mutation (version mismatch, policy rejection), the op parks
/// here for a human to resolve — it is never silently dropped or
/// overwritten.
library;

import 'outbox.dart';

enum ConflictKind {
  /// The server row moved on since the device snapshotted it (409).
  versionMismatch,

  /// The server refused the payload itself (422 / policy rejection).
  rejectedByPolicy,

  /// Session is gone: the user must sign in again before syncing.
  authRequired,
}

class Conflict {
  const Conflict({
    required this.op,
    required this.kind,
    required this.detail,
    this.serverState,
  });

  final OutboxOp op;
  final ConflictKind kind;
  final String detail;
  final Map<String, dynamic>? serverState;

  Map<String, dynamic> toJson() => {
        'op': op.toJson(),
        'kind': kind.name,
        'detail': detail,
        if (serverState != null) 'serverState': serverState,
      };
}
