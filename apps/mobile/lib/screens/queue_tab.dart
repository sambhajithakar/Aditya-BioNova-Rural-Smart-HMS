/// Queue tab: add walk-ins to today's OPD queue. Works offline — the
/// mutation lands in the outbox with a per-day dedupe key, so tapping twice
/// (or retrying after a restart) can never double-book a patient.
///
/// The patient and branch pickers read the persistent [DirectoryCache], so
/// the tab keeps working with no network at all. A patient registered on
/// this device before syncing is referenced as `$ref:<local>` and the sync
/// engine swaps in the server id once the registration is accepted.
library;

import 'package:flutter/material.dart';

import '../data/directory.dart';
import '../main.dart';
import '../sync/idmap.dart';
import '../sync/outbox.dart';

class QueueTab extends StatefulWidget {
  const QueueTab({super.key, required this.graph});

  final AppGraph graph;

  @override
  State<QueueTab> createState() => _QueueTabState();
}

class _QueueTabState extends State<QueueTab> {
  String? _patientId;
  String? _branchId;
  String? _error;
  String? _notice;
  bool _loading = false;

  DirectoryCache get _directory => widget.graph.directory;

  @override
  void initState() {
    super.initState();
    _patientId = _directory.patients.isNotEmpty
        ? _directory.patients.first['id'] as String?
        : null;
    _branchId = _directory.branches.isNotEmpty
        ? _directory.branches.first['id'] as String?
        : null;
    _refresh();
  }

  /// Best-effort network refresh; the cache already covers the offline case.
  Future<void> _refresh() async {
    setState(() => _loading = true);
    try {
      final patients = await widget.graph.client.listPatients();
      final branches = await widget.graph.client.listBranches();
      final items = DirectoryCache.listOf(patients['items']);
      if (!mounted) return;
      await _directory.replacePatients(items);
      await _directory.replaceBranches(DirectoryCache.listOf(branches));
      if (!mounted) return;
      setState(() {
        _patientId ??= items.isNotEmpty ? items.first['id'] as String? : null;
        _branchId ??= _directory.branches.isNotEmpty
            ? _directory.branches.first['id'] as String?
            : null;
        _error = null;
        _loading = false;
      });
    } catch (_) {
      // Offline: the cached directory still works.
      if (!mounted) return;
      setState(() => _loading = false);
    }
  }

  /// Server id for a directory entry: resolved id when its registration has
  /// synced, the `$ref:` placeholder otherwise.
  String _patientRef(String id) {
    final entry = _directory.patientById(id);
    if (entry == null || entry['local'] != true) return id;
    return widget.graph.idMap.serverIdOf(id) ?? IdMap.placeholder(id);
  }

  void _addToQueue() {
    final outbox = widget.graph.outbox;
    final patientId = _patientId;
    final branchId = _branchId;
    if (patientId == null || branchId == null) {
      setState(() => _error = 'Pick a patient and a branch (need one sync).');
      return;
    }
    final today = DateTime.now().toUtc().toIso8601String().split('T').first;
    final dedupeKey = 'queue:$patientId:$today';
    final alreadyQueued = outbox.openOpFor(dedupeKey) != null;
    outbox.enqueue(
      entity: 'queue_entry',
      method: 'POST',
      path: '/opd/queue',
      payload: {
        'patient_id': _patientRef(patientId),
        'branch_id': branchId,
      },
      dedupeKey: dedupeKey,
    );
    setState(() {
      _error = null;
      _notice = alreadyQueued
          ? 'Already queued for today — duplicate prevented.'
          : 'Saved on device — pending sync '
              '(${outbox.count(OpStatus.pending)} in the outbox).';
    });
  }

  @override
  Widget build(BuildContext context) {
    final outbox = widget.graph.outbox;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Row(
          children: [
            Text('Offline queue entry',
                style: Theme.of(context).textTheme.titleMedium),
            const Spacer(),
            TextButton.icon(
              onPressed: _loading ? null : () => _refresh(),
              icon: const Icon(Icons.refresh, size: 18),
              label: const Text('Refresh'),
            ),
          ],
        ),
        const SizedBox(height: 8),
        Text('Patient', style: Theme.of(context).textTheme.labelLarge),
        DropdownButton<String>(
          key: const Key('queue-patient'),
          isExpanded: true,
          value: _patientId,
          hint: const Text('—'),
          items: [
            for (final p in _directory.patients)
              DropdownMenuItem<String>(
                value: p['id'] as String,
                child: Text(
                  p['local'] == true
                      ? '${p['full_name'] ?? ''} (pending sync)'
                      : _directory.patientLabel(p['id'] as String),
                ),
              ),
          ],
          onChanged: (v) => setState(() => _patientId = v),
        ),
        const SizedBox(height: 12),
        Text('Branch', style: Theme.of(context).textTheme.labelLarge),
        DropdownButton<String>(
          key: const Key('queue-branch'),
          isExpanded: true,
          value: _branchId,
          hint: const Text('—'),
          items: [
            for (final b in _directory.branches)
              DropdownMenuItem<String>(
                value: b['id'] as String,
                child: Text((b['name'] as String?) ?? ''),
              ),
          ],
          onChanged: (v) => setState(() => _branchId = v),
        ),
        const SizedBox(height: 12),
        FilledButton(
          key: const Key('queue-add'),
          onPressed: _addToQueue,
          child: const Text('Add to queue (works offline)'),
        ),
        const SizedBox(height: 8),
        Text(
          key: const Key('outbox-counts'),
          'Outbox — pending: ${outbox.count(OpStatus.pending)}, '
          'failed: ${outbox.count(OpStatus.failed)}, '
          'conflicted: ${outbox.count(OpStatus.conflicted)}',
          style: Theme.of(context).textTheme.bodySmall,
        ),
        if (_notice != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(_notice!, key: const Key('queue-notice')),
          ),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              _error!,
              key: const Key('queue-error'),
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ),
        if (_directory.patients.isEmpty && _error == null)
          Padding(
            padding: const EdgeInsets.only(top: 16),
            child: Text(
              _loading
                  ? 'Loading patients…'
                  : 'No patients cached yet — connect once to pull the '
                      'directory, after which queue entries work offline.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
      ],
    );
  }
}
