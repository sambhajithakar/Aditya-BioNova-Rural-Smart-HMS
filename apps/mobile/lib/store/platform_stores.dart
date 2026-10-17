/// Device-backed stores: Android Keystore via `flutter_secure_storage`.
///
/// * [SecureTokenStore] — the session tokens. Keystore-encrypted at rest,
///   so an extracted app sandbox yields no bearer tokens.
/// * [SecureKeyProvider] — the 32-byte AES key that [EncryptedFileStore]
///   seals the offline document with. Generated once with `Random.secure`,
///   stored (as hex) in the same Keystore-backed container, and never
///   written to the offline file itself: stealing the encrypted file
///   without the Keystore is useless.
///
/// Both are only used from `main()`; tests and widget flows inject
/// [MemoryTokenStore] / [FixedKeyProvider] instead, so no platform channel
/// is touched off-device.
library;

import 'dart:math';
import 'dart:typed_data';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../auth/session.dart';
import 'local_store.dart';

class SecureTokenStore implements TokenStore {
  SecureTokenStore({FlutterSecureStorage? storage})
      : _storage = storage ?? const FlutterSecureStorage();

  final FlutterSecureStorage _storage;

  @override
  Future<String?> read(String key) => _storage.read(key: key);

  @override
  Future<void> write(String key, String value) =>
      _storage.write(key: key, value: value);

  @override
  Future<void> delete(String key) => _storage.delete(key: key);
}

class SecureKeyProvider implements KeyProvider {
  SecureKeyProvider({FlutterSecureStorage? storage})
      : _storage = storage ?? const FlutterSecureStorage();

  static const String keyName = 'hms_local_store_key_v1';

  final FlutterSecureStorage _storage;

  @override
  Future<Uint8List> key() async {
    final existing = await _storage.read(key: keyName);
    if (existing != null && existing.length == 64) {
      return Uint8List.fromList(List<int>.generate(
        32,
        (i) => int.parse(existing.substring(i * 2, i * 2 + 2), radix: 16),
      ));
    }
    final bytes = List<int>.generate(32, (_) => Random.secure().nextInt(256));
    await _storage.write(
      key: keyName,
      value: bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join(),
    );
    return Uint8List.fromList(bytes);
  }
}
