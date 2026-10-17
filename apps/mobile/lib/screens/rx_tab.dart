/// Rx tab (doctor): write a prescription draft with no network, from the
/// cached **validated drug catalog** — never free-text medication.
///
/// Drafts land in the outbox like any other mutation and are listed here
/// with their sync state. **Signing is deliberately online-only**: the
/// server owns the blocking allergy check and the `prescription.sign`
/// permission, so the sign button appears only once the server has
/// accepted the draft (its id arrives through the sync engine's id map).
/// An offline draft can therefore never become a signed prescription.
library;

import 'package:flutter/material.dart';

import '../data/directory.dart';
import '../main.dart';
import '../sync/outbox.dart';
import 'widgets.dart';

class RxTab extends StatefulWidget {
  const RxTab({super.key, required this.graph});

  final AppGraph graph;

  @override
  State<RxTab> createState() => _RxTabState();
}

class _RxTabState extends State<RxTab> {
  String? _patientId;
  String? _drugId;
  final _diagnosis = TextEditingController();
  final _dose = TextEditingController();
  final _frequency = TextEditingController();
  final _duration = TextEditingController(text: '5');
  final List<Map<String, dynamic>> _items = <Map<String, dynamic>>[];
  String? _error;
  String? _notice;

  @override
  void initState() {
    super.initState();
    final patients = widget.graph.directory.patients;
    _patientId = patients.isNotEmpty ? patients.first['id'] as String? : null;
    final drugs = widget.graph.directory.drugs;
    _drugId = drugs.isNotEmpty ? drugs.first['id'] as String? : null;
    _adopt();
  }

  Future<void> _adopt() async {
    await widget.graph.drafts.adoptSynced(widget.graph.idMap);
    if (mounted) setState(() {});
  }

  Future<void> _refreshCatalog() async {
    try {
      final drugs = await widget.graph.client.request('GET', '/prescriptions/drugs');
      if (drugs is! List) return;
      await widget.graph.directory.replaceDrugs(DirectoryCache.listOf(drugs));
      if (!mounted) return;
      setState(() {
        final items = widget.graph.directory.drugs;
        _drugId ??= items.isNotEmpty ? items.first['id'] as String? : null;
      });
    } catch (_) {
      // Offline: the cached catalog still works.
    }
  }

  String _patientRef(String id) {
    final entry = widget.graph.directory.patientById(id);
    if (entry == null || entry['local'] != true) return id;
    return widget.graph.idMap.serverIdOf(id) ?? '\$ref:$id';
  }

  void _addItem() {
    final drugId = _drugId;
    if (drugId == null) {
      setState(() => _error = 'No drug catalog cached yet — connect once.');
      return;
    }
    final dose = _dose.text.trim();
    final frequency = _frequency.text.trim();
    final duration = int.tryParse(_duration.text.trim()) ?? 0;
    if (dose.isEmpty || frequency.isEmpty || duration < 1) {
      setState(() => _error = 'Dose, frequency and duration are required.');
      return;
    }
    final drug = widget.graph.directory.drugs.firstWhere(
      (d) => d['id'] == drugId,
      orElse: () => <String, dynamic>{'id': drugId, 'generic_name': drugId},
    );
    setState(() {
      _error = null;
      _items.add({
        'drug_id': drugId,
        'drug_name': (drug['generic_name'] as String?) ?? drugId,
        'dose': dose,
        'route': 'oral',
        'frequency': frequency,
        'duration_days': duration,
      });
      _dose.clear();
      _frequency.clear();
    });
  }

  Future<void> _saveDraft() async {
    final patientId = _patientId;
    if (patientId == null) {
      setState(() => _error = 'Pick a patient first.');
      return;
    }
    if (_items.isEmpty) {
      setState(() => _error = 'Add at least one medicine.');
      return;
    }

    final localRef = Outbox.newId();
    final op = widget.graph.outbox.enqueue(
      entity: 'prescription',
      method: 'POST',
      path: '/prescriptions',
      payload: {
        'patient_id': _patientRef(patientId),
        'diagnosis_text': _diagnosis.text.trim(),
        'items': [
          for (final item in _items)
            {
              'drug_id': item['drug_id'],
              'dose': item['dose'],
              'route': item['route'],
              'frequency': item['frequency'],
              'duration_days': item['duration_days'],
            },
        ],
      },
      dedupeKey:
          'rx:$patientId:${_items.map((i) => i['drug_id']).join(',')}',
      localRef: localRef,
    );

    if (op.localRef != localRef) {
      setState(() {
        _error = null;
        _notice = 'Identical draft already queued — duplicate prevented.';
      });
      return;
    }

    await widget.graph.drafts.addDraft(
      localRef: localRef,
      patientRef: _patientRef(patientId),
      patientLabel: widget.graph.directory.patientLabel(patientId),
      diagnosis: _diagnosis.text.trim(),
      items: List<Map<String, dynamic>>.of(_items),
    );
    if (!mounted) return;
    setState(() {
      _error = null;
      _notice = 'Draft saved on device — pending sync. Sign it after it '
          'syncs (the allergy check runs on the server).';
      _items.clear();
      _diagnosis.clear();
    });
  }

  Future<void> _sign(String localRef, String serverId) async {
    setState(() => _error = null);
    try {
      await widget.graph.client
          .request('POST', '/prescriptions/$serverId/sign');
      await widget.graph.drafts.markSigned(localRef);
      if (!mounted) return;
      setState(() => _notice = 'Prescription signed.');
    } catch (err) {
      if (!mounted) return;
      setState(() => _error = err.toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    final directory = widget.graph.directory;
    final drafts = widget.graph.drafts.drafts;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Row(
          children: [
            Text('Fast Rx draft (works offline)',
                style: Theme.of(context).textTheme.titleMedium),
            const Spacer(),
            TextButton.icon(
              onPressed: () async {
                await _refreshCatalog();
                await _adopt();
              },
              icon: const Icon(Icons.refresh, size: 18),
              label: const Text('Refresh'),
            ),
          ],
        ),
        const SizedBox(height: 12),
        Text('Patient', style: Theme.of(context).textTheme.labelLarge),
        DropdownButton<String>(
          key: const Key('rx-patient'),
          isExpanded: true,
          value: _patientId,
          hint: const Text('—'),
          items: [
            for (final p in directory.patients)
              DropdownMenuItem<String>(
                value: p['id'] as String,
                child: Text(p['local'] == true
                    ? '${p['full_name'] ?? ''} (pending sync)'
                    : directory.patientLabel(p['id'] as String)),
              ),
          ],
          onChanged: (v) => setState(() => _patientId = v),
        ),
        const SizedBox(height: 12),
        Text('Medicine (validated catalog)',
            style: Theme.of(context).textTheme.labelLarge),
        DropdownButton<String>(
          key: const Key('rx-drug'),
          isExpanded: true,
          value: _drugId,
          hint: const Text('—'),
          items: [
            for (final d in directory.drugs)
              DropdownMenuItem<String>(
                value: d['id'] as String,
                child: Text((d['generic_name'] as String?) ?? ''),
              ),
          ],
          onChanged: (v) => setState(() => _drugId = v),
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            Expanded(
              child: TextField(
                key: const Key('rx-dose'),
                controller: _dose,
                decoration: const InputDecoration(labelText: 'Dose (500 mg)'),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: TextField(
                key: const Key('rx-frequency'),
                controller: _frequency,
                decoration: const InputDecoration(labelText: 'Frequency (BD)'),
              ),
            ),
            const SizedBox(width: 8),
            SizedBox(
              width: 80,
              child: TextField(
                key: const Key('rx-duration'),
                controller: _duration,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(labelText: 'Days'),
              ),
            ),
          ],
        ),
        Align(
          alignment: Alignment.centerLeft,
          child: TextButton.icon(
            key: const Key('rx-add-item'),
            onPressed: _addItem,
            icon: const Icon(Icons.add, size: 18),
            label: const Text('Add medicine'),
          ),
        ),
        for (final item in _items)
          ListTile(
            dense: true,
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.medication_outlined, size: 18),
            title: Text(
              '${item['drug_name']} — ${item['dose']}, ${item['frequency']}, '
              '${item['duration_days']} days',
            ),
          ),
        TextField(
          key: const Key('rx-diagnosis'),
          controller: _diagnosis,
          decoration: const InputDecoration(labelText: 'Diagnosis (text)'),
        ),
        const SizedBox(height: 12),
        FilledButton(
          key: const Key('rx-save'),
          onPressed: _saveDraft,
          child: const Text('Save draft (works offline)'),
        ),
        if (_notice != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(_notice!, key: const Key('rx-notice')),
          ),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              _error!,
              key: const Key('rx-error'),
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ),
        const SizedBox(height: 20),
        Text('Drafts on this device',
            style: Theme.of(context).textTheme.titleSmall),
        for (final draft in drafts) _draftCard(draft),
        if (drafts.isEmpty)
          Text(
            'No drafts yet — they are kept here with their sync state.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
      ],
    );
  }

  Widget _draftCard(Map<String, dynamic> draft) {
    final localRef = draft['localRef'] as String? ?? '';
    final serverId = draft['serverId'] as String?;
    final signed = draft['signed'] == true;
    final op = widget.graph.outbox.openOpFor('rx:$localRef') ??
        widget.graph.outbox.ops.cast<OutboxOp?>().firstWhere(
              (o) => o?.localRef == localRef,
              orElse: () => null,
            );
    final conflicted = op?.status == OpStatus.conflicted;
    final pending = op != null && !conflicted;

    return Card(
      margin: const EdgeInsets.symmetric(vertical: 6),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '${draft['patientLabel'] ?? ''} — ${draft['diagnosis'] ?? ''}'
                  .trim(),
              style: Theme.of(context).textTheme.titleSmall,
            ),
            const SizedBox(height: 4),
            Text(
              [
                for (final item in (draft['items'] as List<dynamic>? ?? const []))
                  '${item['drug_name']} ${item['dose']} ${item['frequency']} '
                  '${item['duration_days']}d',
              ].join('\n'),
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                if (signed)
                  StateChip(label: 'Signed', tone: Theme.of(context).colorScheme.primary)
                else if (conflicted)
                  const StateChip(
                      label: 'Conflict — needs you', tone: Color(0xFFB3261E))
                else if (pending)
                  const StateChip(
                      label: 'Saved on device · Pending sync', tone: Color(0xFF0B5D8A))
                else
                  const StateChip(label: 'Synced', tone: Color(0xFF1B7F5A)),
                const Spacer(),
                if (!signed)
                  TextButton(
                    key: Key('rx-sign-$localRef'),
                    onPressed: (serverId != null && !pending)
                        ? () => _sign(localRef, serverId)
                        : null,
                    child: Text(pending || serverId == null
                        ? 'Sign (after sync)'
                        : 'Sign'),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
