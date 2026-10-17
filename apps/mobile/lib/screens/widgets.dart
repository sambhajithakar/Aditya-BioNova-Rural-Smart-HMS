/// Small shared widgets: the explicit sync-state labels R4.2 asks for —
/// **Saved on device / Pending sync / Synced / Conflict** — plus the two
/// extra states the engine can be in (syncing, retryable failure).
library;

import 'package:flutter/material.dart';

import '../sync/outbox.dart';

/// Human label for an outbox op's state.
String opStateLabel(OutboxOp op) => switch (op.status) {
      OpStatus.pending =>
        op.attempts == 0 ? 'Pending sync' : 'Pending sync · retry ${op.attempts}',
      OpStatus.inFlight => 'Syncing…',
      OpStatus.failed => 'Failed — will retry',
      OpStatus.conflicted => 'Conflict — needs you',
    };

class StateChip extends StatelessWidget {
  const StateChip({super.key, required this.label, required this.tone});

  final String label;
  final Color tone;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: tone.withValues(alpha: 0.15),
        border: Border.all(color: tone),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        label,
        style: TextStyle(color: tone, fontSize: 12),
      ),
    );
  }
}

/// Chip for an outbox op, coloured by its state.
class OpStateChip extends StatelessWidget {
  const OpStateChip({super.key, required this.op});

  final OutboxOp op;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final (label, tone) = switch (op.status) {
      OpStatus.pending => ('Saved on device · ${opStateLabel(op)}', scheme.primary),
      OpStatus.inFlight => (opStateLabel(op), scheme.tertiary),
      OpStatus.failed => (opStateLabel(op), scheme.error),
      OpStatus.conflicted => (opStateLabel(op), scheme.error),
    };
    return StateChip(label: label, tone: tone);
  }
}
