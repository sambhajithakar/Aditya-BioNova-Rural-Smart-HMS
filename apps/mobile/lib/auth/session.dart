/// Session (token) storage.
///
/// The offline core and tests use [MemoryTokenStore]. The Android build
/// backs [TokenStore] with flutter_secure_storage (Keystore-encrypted) —
/// that wiring is part of M4's device work and lands with the Android
/// toolchain (see TASKS.md). Nothing outside this file knows which
/// implementation is in use.
library;

abstract interface class TokenStore {
  Future<String?> read(String key);

  Future<void> write(String key, String value);

  Future<void> delete(String key);
}

class MemoryTokenStore implements TokenStore {
  final Map<String, String> _data = {};

  @override
  Future<String?> read(String key) async => _data[key];

  @override
  Future<void> write(String key, String value) async {
    _data[key] = value;
  }

  @override
  Future<void> delete(String key) async {
    _data.remove(key);
  }
}

class Session {
  Session(this._store);

  static const accessTokenKey = 'access_token';
  static const refreshTokenKey = 'refresh_token';

  final TokenStore _store;

  Future<void> save({
    required String accessToken,
    required String refreshToken,
  }) async {
    await _store.write(accessTokenKey, accessToken);
    await _store.write(refreshTokenKey, refreshToken);
  }

  Future<String?> accessToken() => _store.read(accessTokenKey);

  Future<void> clear() async {
    await _store.delete(accessTokenKey);
    await _store.delete(refreshTokenKey);
  }
}
