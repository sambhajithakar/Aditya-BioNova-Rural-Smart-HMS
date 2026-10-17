/// Sync tab: the sync centre.
///
/// Shows every queued mutation with its **explicit sync state** (R4.2:
/// Saved on device · Pending sync / Synced / Conflict, plus retryable
/// failures), runs a push, and resolves conflicts one by one — keeping the
/// local change (re-send) or discarding it. Nothing is ever dropped
/// silently and nothing is last-write-wins.
library;

import 'package:flutter/material.dart';

import '../main.dart';
import '../sync/conflict.dart';
import '../sync/engine.dart';
import '../sync/outbox.dart';
import 'widgets.dart';

class SyncTab extends StatefulWidget {
  const SyncTab({super.key, required this.graph});

  final AppGraph graph;

  @override
  State<SyncTab> createState() => _SyncTabState();
}

class _SyncTabState extends State<SyncTab> {
  SyncResult? _result;
  String? _error;
  bool _busy = false;

  Future<void> _sync() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final result = await widget.graph.engine.sync();
      await widget.graph.drafts.adoptSynced(widget.graph.idMap);
      if (!mounted) return;
      setState(() => _result = result);
    } catch (err) {
      if (!mounted) return;
      setState(() => _error = err.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _retryFailed() {
    widget.graph.outbox.retryOpen();
    setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final outbox = widget.graph.outbox;
    final conflicted =
        outbox.ops.where((o) => o.status == OpStatus.conflicted).toList();

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Text('Sync centre', style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 8),
        Text(
          key: const Key('sync-counts'),
          'pending: ${outbox.count(OpStatus.pending)} · '
          'failed: ${outbox.count(OpStatus.failed)} · '
          'conflicted: ${outbox.count(OpStatus.conflicted)} · '
          'applied: ${outbox.count(OpStatus.inFlight)} in flight',
          style: Theme.of(context).textTheme.bodySmall,
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            FilledButton(
              key: const Key('sync-run'),
              onPressed: _busy ? null : () => _sync(),
              child: Text(_busy ? 'Syncing…' : 'Sync now'),
            ),
            const SizedBox(width: 8),
            OutlinedButton(
              key: const Key('sync-retry'),
              onPressed: outbox.count(OpStatus.failed) == 0
                  ? null
                  : _retryFailed,
              child: const Text('Retry failed'),
            ),
          ],
        ),
        if (_result != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              key: const Key('sync-result'),
              'pushed ${_result!.pushed} · conflicts '
              '${_result!.conflicts.length} · failed ${_result!.failed}'
              '${_result!.deferred > 0 ? ' · waiting ${_result!.deferred}' : ''}'
              '${_result!.stoppedUnauthorized ? ' · stopped (sign in again)' : ''}',
            ),
          ),
        if (_result != null && _result!.synced.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Wrap(
              spacing: 6,
              runSpacing: 4,
              children: [
                for (final entity in _result!.synced)
                  StateChip(
                    key: Key('synced-$entity'),
                    label: 'Synced · $entity',
                    tone: const Color(0xFF1B7F5A),
                  ),
              ],
            ),
          ),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              _error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ),
        const SizedBox(height: 16),
        Text('Queued on this device',
            style: Theme.of(context).textTheme.titleSmall),
        const SizedBox(height: 4),
        for (final op in outbox.ops)
          Card(
            margin: const EdgeInsets.symmetric(vertical: 6),
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '${op.entity} · ${op.method} ${op.path}',
                    style: Theme.of(context).textTheme.titleSmall,
                  ),
                  const SizedBox(height: 6),
                  OpStateChip(op: op),
                  if (op.lastError.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 6),
                      child: Text(
                        op.lastError,
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                    ),
                ],
              ),
            ),
          ),
        if (outbox.ops.isEmpty)
          Text(
            'Nothing queued — every change has reached the server.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        const SizedBox(height: 16),
        if (conflicted.isEmpty)
          Text(
            'No conflicts — local and server agree.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        for (final op in conflicted)
          Card(
            margin: const EdgeInsets.symmetric(vertical: 6),
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('${op.entity} · ${op.method} ${op.path}',
                      style: Theme.of(context).textTheme.titleSmall),
                  Text(op.lastError,
                      style: Theme.of(context).textTheme.bodySmall),
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      TextButton(
                        onPressed: () {
                          outbox.resolveConflict(op, keepLocal: true);
                          setState(() {});
                        },
                        child: const Text('Keep local (re-send)'),
                      ),
                      TextButton(
                        onPressed: () {
                          outbox.resolveConflict(op, keepLocal: false);
                          setState(() {});
                        },
                        child: const Text('Discard (server wins here)'),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
      ],
    );
  }
}

/// Exported so the shell can classify conflict kinds for tooltips.
String conflictLabel(ConflictKind kind) => switch (kind) {
      ConflictKind.versionMismatch => 'The record changed on the server',
      ConflictKind.rejectedByPolicy => 'The server refused this change',
      ConflictKind.authRequired => 'Sign in again to continue',
    };
