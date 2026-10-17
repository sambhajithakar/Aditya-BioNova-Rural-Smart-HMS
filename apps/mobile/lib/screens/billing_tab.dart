/// Billing tab: raise an invoice with no network.
///
/// The invoice is queued with a per-day, per-patient, per-line dedupe key
/// (a double-tap stays ONE op) and pushed with its `Idempotency-Key`, so a
/// retry after a flaky connection can never charge the patient twice. The
/// server computes the totals — the device never invents an amount, and a
/// payment is only ever taken against a server-issued invoice.
library;

import 'package:flutter/material.dart';

import '../main.dart';
import '../sync/outbox.dart';

const List<String> _itemTypes = [
  'consultation',
  'procedure',
  'pharmacy',
  'lab',
  'other',
];

class BillingTab extends StatefulWidget {
  const BillingTab({super.key, required this.graph});

  final AppGraph graph;

  @override
  State<BillingTab> createState() => _BillingTabState();
}

class _BillingTabState extends State<BillingTab> {
  String? _patientId;
  String _itemType = 'consultation';
  final _description = TextEditingController();
  final _quantity = TextEditingController(text: '1');
  final _price = TextEditingController();
  String? _error;
  String? _notice;

  @override
  void initState() {
    super.initState();
    final patients = widget.graph.directory.patients;
    _patientId = patients.isNotEmpty ? patients.first['id'] as String? : null;
  }

  String _patientRef(String id) {
    final entry = widget.graph.directory.patientById(id);
    if (entry == null || entry['local'] != true) return id;
    return widget.graph.idMap.serverIdOf(id) ?? '\$ref:$id';
  }

  Future<void> _saveInvoice() async {
    final patientId = _patientId;
    final description = _description.text.trim();
    if (patientId == null) {
      setState(() => _error = 'Pick a patient first.');
      return;
    }
    if (description.isEmpty) {
      setState(() => _error = 'Describe the line being billed.');
      return;
    }
    final quantity = int.tryParse(_quantity.text.trim()) ?? 0;
    final price = double.tryParse(_price.text.trim()) ?? 0;
    if (quantity < 1 || price <= 0) {
      setState(() => _error = 'Quantity and unit price are required.');
      return;
    }

    final branchId = widget.graph.directory.branches.isNotEmpty
        ? widget.graph.directory.branches.first['id'] as String?
        : null;
    final today = DateTime.now().toUtc().toIso8601String().split('T').first;
    final dedupeKey =
        'invoice:$patientId:${description.toLowerCase()}:$today';
    final existing = widget.graph.outbox.openOpFor(dedupeKey);
    final op = widget.graph.outbox.enqueue(
      entity: 'invoice',
      method: 'POST',
      path: '/billing/invoices',
      payload: {
        'patient_id': _patientRef(patientId),
        'branch_id': branchId,
        'discount': 0,
        'items': [
          {
            'item_type': _itemType,
            'description': description,
            'quantity': quantity,
            'unit_price': price,
            'tax_rate': 0,
          },
        ],
      },
      dedupeKey: dedupeKey,
    );

    if (existing != null && existing.id == op.id) {
      setState(() {
        _error = null;
        _notice = 'Identical invoice already queued — duplicate prevented.';
      });
      return;
    }

    setState(() {
      _error = null;
      _notice = 'Invoice saved on device — pending sync. Totals and the '
          'invoice number are computed by the server.';
      _description.clear();
      _price.clear();
      _quantity.text = '1';
    });
  }

  @override
  Widget build(BuildContext context) {
    final directory = widget.graph.directory;
    final outbox = widget.graph.outbox;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Text('Raise invoice (works offline)',
            style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 12),
        Text('Patient', style: Theme.of(context).textTheme.labelLarge),
        DropdownButton<String>(
          key: const Key('billing-patient'),
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
        Text('Line type', style: Theme.of(context).textTheme.labelLarge),
        DropdownButton<String>(
          key: const Key('billing-item-type'),
          value: _itemType,
          isExpanded: true,
          items: [
            for (final t in _itemTypes)
              DropdownMenuItem(value: t, child: Text(t)),
          ],
          onChanged: (v) => setState(() => _itemType = v ?? 'consultation'),
        ),
        const SizedBox(height: 12),
        TextField(
          key: const Key('billing-description'),
          controller: _description,
          decoration: const InputDecoration(labelText: 'Description'),
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            Expanded(
              child: TextField(
                key: const Key('billing-quantity'),
                controller: _quantity,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(labelText: 'Quantity'),
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: TextField(
                key: const Key('billing-price'),
                controller: _price,
                keyboardType:
                    const TextInputType.numberWithOptions(decimal: true),
                decoration: const InputDecoration(labelText: 'Unit price'),
              ),
            ),
          ],
        ),
        const SizedBox(height: 16),
        FilledButton(
          key: const Key('billing-add'),
          onPressed: _saveInvoice,
          child: const Text('Save invoice (works offline)'),
        ),
        if (_notice != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(_notice!, key: const Key('billing-notice')),
          ),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              _error!,
              key: const Key('billing-error'),
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ),
        const SizedBox(height: 16),
        Text(
          'Outbox — pending: ${outbox.count(OpStatus.pending)}, '
          'failed: ${outbox.count(OpStatus.failed)}, '
          'conflicted: ${outbox.count(OpStatus.conflicted)}',
          key: const Key('billing-outbox-counts'),
          style: Theme.of(context).textTheme.bodySmall,
        ),
      ],
    );
  }
}
