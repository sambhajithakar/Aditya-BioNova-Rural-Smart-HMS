/// Directory cache: the slice of the server the app needs to work with no
/// network — patients, branches and the validated drug catalog.
///
/// Loaded at start-up from the encrypted [LocalStore] (that is what makes
/// "connect once, then work offline" true), refreshed whenever a list
/// endpoint succeeds, and updated with locally registered patients the
/// moment they are queued so reception can put a walk-in into today's queue
/// without ever having synced.
library;

import '../store/local_store.dart';

class DirectoryCache {
  DirectoryCache(this.store);

  static const String section = 'directory';

  final LocalStore store;

  List<Map<String, dynamic>> patients = <Map<String, dynamic>>[];
  List<Map<String, dynamic>> branches = <Map<String, dynamic>>[];
  List<Map<String, dynamic>> drugs = <Map<String, dynamic>>[];

  /// Read the cache out of the already-open store document.
  void load() {
    final raw = store.doc[section];
    if (raw is! Map<String, dynamic>) return;
    patients = _list(raw['patients']);
    branches = _list(raw['branches']);
    drugs = _list(raw['drugs']);
  }

  Future<void> save() => store.saveSection(section, {
        'patients': patients,
        'branches': branches,
        'drugs': drugs,
      });

  Future<void> replacePatients(List<Map<String, dynamic>> items) async {
    patients = items;
    await save();
  }

  Future<void> replaceBranches(List<Map<String, dynamic>> items) async {
    branches = items;
    await save();
  }

  Future<void> replaceDrugs(List<Map<String, dynamic>> items) async {
    drugs = items;
    await save();
  }

  /// Add a patient registered on this device BEFORE it reached the server.
  /// The entry carries the local ref as its `id` and an empty `uhid`; the
  /// sync engine maps the ref to the server id when the op is accepted.
  Future<void> addLocalPatient({
    required String localRef,
    required String fullName,
    required String gender,
    String phone = '',
  }) async {
    if (patientById(localRef) != null) return;
    patients = [
      {
        'id': localRef,
        'uhid': '',
        'full_name': fullName,
        'gender': gender,
        'phone': phone,
        'local': true,
      },
      ...patients,
    ];
    await save();
  }

  Map<String, dynamic>? patientById(String id) {
    for (final p in patients) {
      if (p['id'] == id) return p;
    }
    return null;
  }

  String patientLabel(String id) {
    final p = patientById(id);
    if (p == null) return id;
    final uhid = (p['uhid'] as String?) ?? '';
    final name = (p['full_name'] as String?) ?? '';
    final label = '$uhid $name'.trim();
    return label.isEmpty ? id : label;
  }

  /// Normalize a JSON array of objects into typed maps.
  static List<Map<String, dynamic>> listOf(Object? raw) =>
      (raw as List<dynamic>? ?? const [])
          .whereType<Map<dynamic, dynamic>>()
          .map((e) => e.cast<String, dynamic>())
          .toList();

  static List<Map<String, dynamic>> _list(Object? raw) => listOf(raw);
}
