/// Offline outbox: mutations captured while the device has no network,
/// replayed later in order.
///
/// Rules (M4 acceptance):
/// * every mutation is an [OutboxOp] with a client-generated id used as the
///   server `Idempotency-Key`, so replay never double-applies;
/// * duplicate prevention: one open op per `dedupeKey` (e.g. "queue the
///   same patient into today's queue twice" stays ONE op);
/// * ops never silently disappear: a failed push keeps the op as `failed`
///   for the next attempt, and a server conflict parks it as `conflicted`
///   for the user to resolve — no last-write-wins on clinical data;
/// * [toJson]/[fromJson] survive an app restart (offline recovery).
library;

import 'dart:convert';
import 'dart:math';

enum OpStatus { pending, inFlight, conflicted, failed }

class OutboxOp {
  OutboxOp({
    required this.id,
    required this.entity,
    required this.method,
    required this.path,
    required this.payload,
    required this.dedupeKey,
    required this.baseVersion,
    required this.createdAt,
    this.status = OpStatus.pending,
    this.attempts = 0,
    this.lastError = '',
    this.localRef,
  });

  /// Device-side id of the entity this op CREATES (e.g. a patient
  /// registered offline). Other ops refer to it as `$ref:<localRef>`; the
  /// sync engine swaps in the server id once this op is accepted.
  ///
  /// It is never sent to the API — the server models use `extra=forbid`,
  /// so the placeholder only ever exists on the device.

  factory OutboxOp.fromJson(Map<String, dynamic> json) => OutboxOp(
        id: json['id'] as String,
        entity: json['entity'] as String,
        method: json['method'] as String,
        path: json['path'] as String,
        payload: (json['payload'] as Map<String, dynamic>).cast<String, dynamic>(),
        dedupeKey: json['dedupeKey'] as String,
        baseVersion: json['baseVersion'] as int? ?? 0,
        createdAt: DateTime.parse(json['createdAt'] as String),
        status: OpStatus.values.firstWhere(
          (s) => s.name == json['status'],
          orElse: () => OpStatus.pending,
        ),
        attempts: json['attempts'] as int? ?? 0,
        lastError: json['lastError'] as String? ?? '',
        localRef: json['localRef'] as String?,
      );

  /// Client-generated UUID; replayed as the server `Idempotency-Key`.
  final String id;
  final String entity;
  final String method;
  final String path;
  final Map<String, dynamic> payload;
  final String dedupeKey;
  final int baseVersion;
  final String? localRef;
  final DateTime createdAt;
  OpStatus status;
  int attempts;
  String lastError;

  Map<String, dynamic> toJson() => {
        'id': id,
        'localRef': localRef,
        'entity': entity,
        'method': method,
        'path': path,
        'payload': payload,
        'dedupeKey': dedupeKey,
        'baseVersion': baseVersion,
        'createdAt': createdAt.toIso8601String(),
        'status': status.name,
        'attempts': attempts,
        'lastError': lastError,
      };
}

class Outbox {
  Outbox();

  final List<OutboxOp> _ops = [];

  /// Fired whenever the queue changes, so the graph can persist it to the
  /// encrypted local store (an app restart then keeps every pending op).
  void Function()? onChanged;

  void markDirty() => onChanged?.call();

  static final Random _rng = Random();

  List<OutboxOp> get ops => List.unmodifiable(_ops);

  Iterable<OutboxOp> get pending =>
      _ops.where((o) => o.status == OpStatus.pending);

  int count(OpStatus status) => _ops.where((o) => o.status == status).length;

  int get total => _ops.length;

  /// Queue a mutation. Returns the EXISTING op when an open op with the
  /// same [dedupeKey] is already queued (offline duplicate prevention).
  OutboxOp enqueue({
    required String entity,
    required String method,
    required String path,
    required Map<String, dynamic> payload,
    required String dedupeKey,
    int baseVersion = 0,
    String? id,
    String? localRef,
    DateTime? now,
  }) {
    final existing = openOpFor(dedupeKey);
    if (existing != null) return existing;

    final op = OutboxOp(
      id: id ?? newId(),
      entity: entity,
      method: method,
      path: path,
      payload: payload,
      dedupeKey: dedupeKey,
      baseVersion: baseVersion,
      createdAt: now ?? DateTime.now().toUtc(),
      localRef: localRef,
    );
    _ops.add(op);
    onChanged?.call();
    return op;
  }

  /// The open (pending/in-flight/failed) op for a dedupe key, if any.
  OutboxOp? openOpFor(String dedupeKey) {
    for (final op in _ops) {
      if (op.dedupeKey == dedupeKey &&
          op.status != OpStatus.conflicted) {
        return op;
      }
    }
    return null;
  }

  void remove(OutboxOp op) {
    _ops.remove(op);
    onChanged?.call();
  }

  /// Re-queue everything the user asked to retry (failed + conflicted).
  void retryOpen({bool includeConflicted = false}) {
    var changed = false;
    for (final op in _ops) {
      if (op.status == OpStatus.failed ||
          (includeConflicted && op.status == OpStatus.conflicted)) {
        op.status = OpStatus.pending;
        op.lastError = '';
        changed = true;
      }
    }
    if (changed) onChanged?.call();
  }

  /// Resolve a conflict: either discard the local change (server wins for
  /// THAT op — the user explicitly chose it) or re-queue with the server's
  /// current version as the new base.
  void resolveConflict(OutboxOp op, {required bool keepLocal}) {
    if (op.status != OpStatus.conflicted) return;
    if (keepLocal) {
      op.status = OpStatus.pending;
      op.lastError = '';
    } else {
      _ops.remove(op);
    }
    onChanged?.call();
  }

  String toJson() => jsonEncode({
        'ops': _ops.map((o) => o.toJson()).toList(),
      });

  factory Outbox.fromJson(String source) {
    final outbox = Outbox();
    final decoded = jsonDecode(source) as Map<String, dynamic>;
    final ops = decoded['ops'] as List<dynamic>? ?? const [];
    for (final raw in ops) {
      outbox._ops
          .add(OutboxOp.fromJson((raw as Map<String, dynamic>).cast<String, dynamic>()));
    }
    return outbox;
  }

  static String newId() {
    final bytes = List<int>.generate(16, (_) => _rng.nextInt(256));
    bytes[6] = (bytes[6] & 0x0f) | 0x40; // uuid v4 flavour
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    final hex = bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
    return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-'
        '${hex.substring(12, 16)}-${hex.substring(16, 20)}-${hex.substring(20)}';
  }
}
