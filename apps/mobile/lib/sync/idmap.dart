/// Local → server id mapping.
///
/// Offline-first rule: a mutation captured with no network has no server id
/// yet (a patient registered in airplane mode, for example). The device
/// generates the id itself and records the op's `localRef`; every other
/// queued mutation refers to it as `$ref:<localRef>` (see [IdMap.placeholder]).
///
/// The sync engine substitutes references right before pushing, in queue
/// order. A reference that is still unresolved (its creating op has not
/// been accepted yet) DEFERS the dependent op to the next run instead of
/// pushing a bogus id — the server FKs would reject it, and worse, it could
/// attach a queue entry to the wrong patient.
library;

class IdMap {
  IdMap([Map<String, String>? ids]) : _ids = Map<String, String>.of(ids ?? const {});

  static const String prefix = r'$ref:';

  final Map<String, String> _ids;

  /// The placeholder a queued payload stores for a not-yet-synced entity.
  static String placeholder(String localRef) => '$prefix$localRef';

  bool isPlaceholder(Object? value) =>
      value is String && value.startsWith(prefix);

  String? serverIdOf(String localRef) => _ids[localRef];

  void put(String localRef, String serverId) {
    _ids[localRef] = serverId;
  }

  Map<String, dynamic> toJson() => {'ids': _ids};

  factory IdMap.fromJson(Map<String, dynamic> json) {
    final raw = json['ids'];
    return IdMap(raw is Map<String, dynamic> ? raw.cast<String, String>() : null);
  }

  /// Deep-copy [payload] with every `$ref:…` replaced by its server id.
  ///
  /// Returns `null` when at least one reference is still unresolved — the
  /// caller must NOT push this payload yet.
  Map<String, dynamic>? substitute(Map<String, dynamic> payload) {
    var unresolved = false;

    Object? walk(Object? value) {
      if (value is String && value.startsWith(prefix)) {
        final server = _ids[value.substring(prefix.length)];
        if (server == null) {
          unresolved = true;
          return value;
        }
        return server;
      }
      if (value is Map) {
        return value.map((k, v) => MapEntry(k.toString(), walk(v)));
      }
      if (value is List) return value.map(walk).toList();
      return value;
    }

    final resolved = walk(payload);
    if (unresolved) return null;
    return resolved is Map<String, dynamic> ? resolved : null;
  }

  /// True when [payload] still contains an unresolved local reference.
  static bool hasUnresolved(Map<String, dynamic> payload, IdMap ids) =>
      ids.substitute(payload) == null;
}
