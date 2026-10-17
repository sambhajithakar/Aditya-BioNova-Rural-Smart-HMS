/// Prescription drafts written on this device.
///
/// A draft is created offline in the outbox and listed here so the doctor
/// can see it, and — only once the server has accepted it (the sync engine
/// recorded its id in the [IdMap]) — sign it. Signing stays online on
/// purpose: the API's blocking allergy check and the sign-permission gate
/// live server-side, and M4 must never sign a prescription the server has
/// not validated.
library;

import '../store/local_store.dart';
import '../sync/idmap.dart';

class RxDraftBook {
  RxDraftBook(this.store);

  static const String section = 'rx_drafts';

  final LocalStore store;
  List<Map<String, dynamic>> drafts = <Map<String, dynamic>>[];

  void load() {
    final raw = store.doc[section];
    if (raw is! Map<String, dynamic>) return;
    drafts = DirectoryList.of(raw['drafts']);
  }

  Future<void> save() => store.saveSection(section, {'drafts': drafts});

  Future<void> addDraft({
    required String localRef,
    required String patientRef,
    required String patientLabel,
    required String diagnosis,
    required List<Map<String, dynamic>> items,
  }) async {
    drafts = [
      {
        'localRef': localRef,
        'patientRef': patientRef,
        'patientLabel': patientLabel,
        'diagnosis': diagnosis,
        'items': items,
        'serverId': null,
        'signed': false,
        'createdAt': DateTime.now().toUtc().toIso8601String(),
      },
      ...drafts,
    ];
    await save();
  }

  /// Attach the ids the server returned (via the sync engine's id map) to
  /// drafts that have now synced. Signing becomes possible at this point.
  Future<void> adoptSynced(IdMap ids) async {
    var changed = false;
    for (final draft in drafts) {
      if (draft['serverId'] != null) continue;
      final localRef = draft['localRef'];
      if (localRef is! String) continue;
      final serverId = ids.serverIdOf(localRef);
      if (serverId == null) continue;
      draft['serverId'] = serverId;
      changed = true;
    }
    if (changed) await save();
  }

  Future<void> markSigned(String localRef) async {
    for (final draft in drafts) {
      if (draft['localRef'] == localRef) draft['signed'] = true;
    }
    await save();
  }
}

/// Tiny JSON-array normalizer shared by the local caches.
class DirectoryList {
  static List<Map<String, dynamic>> of(Object? raw) =>
      (raw as List<dynamic>? ?? const [])
          .whereType<Map<dynamic, dynamic>>()
          .map((e) => e.cast<String, dynamic>())
          .toList();
}
