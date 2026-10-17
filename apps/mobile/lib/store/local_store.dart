/// Encrypted local storage (M4 / R4.2 — "encrypted local storage").
///
/// The app's offline state (outbox, directory cache, id map, drafts) lives
/// in ONE document with named sections. [EncryptedFileStore] seals that
/// document with **AES-256-GCM** (fresh random 12-byte nonce per write, the
/// `HMS1` magic as additional authenticated data), so nothing is readable
/// at rest — a lost or stolen device exposes no PHI and no pending work.
///
/// Properties the M4 acceptance matrix checks:
/// * round trip: what you save is what you load back;
/// * ciphertext never contains plaintext (any byte of the file);
/// * a flipped bit or a wrong key is DETECTED (GCM tag) and raises
///   [StateError] — offline work is never silently dropped or misread;
/// * writes are atomic (temp file + rename), so a crash mid-write keeps
///   the previous sealed document.
///
/// The key never lives in this file: it comes from a [KeyProvider]. On
/// device that is [SecureKeyProvider] (Android Keystore via
/// flutter_secure_storage); tests inject [FixedKeyProvider].
library;

import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:pointycastle/export.dart';

/// File magic: also used as the GCM additional authenticated data.
const List<int> kStoreMagic = [0x48, 0x4d, 0x53, 0x31]; // "HMS1"

const int _nonceLength = 12;
const int _tagBits = 128;

/// Keyed storage for a small JSON document split into named sections.
abstract interface class LocalStore {
  /// The currently loaded document. Empty until [open] has completed.
  Map<String, dynamic> get doc;

  /// Load (and decrypt) the persisted document; `{}` when nothing is stored.
  Future<Map<String, dynamic>> open();

  /// Replace one section and persist the whole document atomically.
  /// A `null` value removes the section.
  Future<void> saveSection(String name, Object? value);

  /// Wait until every queued write has hit the storage.
  Future<void> flush();
}

/// In-memory store: the test double and the fallback when the app is run
/// without a device store (e.g. widget tests, `flutter run` on the VM).
class MemoryLocalStore implements LocalStore {
  MemoryLocalStore([Map<String, dynamic>? doc]) : _doc = doc ?? <String, dynamic>{};

  final Map<String, dynamic> _doc;

  @override
  Map<String, dynamic> get doc => _doc;

  @override
  Future<Map<String, dynamic>> open() async => _doc;

  @override
  Future<void> saveSection(String name, Object? value) async {
    if (value == null) {
      _doc.remove(name);
    } else {
      _doc[name] = value;
    }
  }

  @override
  Future<void> flush() async {}
}

/// Source of the 32-byte AES key.
abstract interface class KeyProvider {
  Future<Uint8List> key();
}

/// Fixed key for tests and local tooling.
class FixedKeyProvider implements KeyProvider {
  FixedKeyProvider(this._key);

  /// Build from a hex string (64 hex chars = 32 bytes).
  FixedKeyProvider.fromHex(String hex)
      : _key = Uint8List.fromList(
          List<int>.generate(hex.length ~/ 2, (i) => int.parse(hex.substring(i * 2, i * 2 + 2), radix: 16)),
        );

  final Uint8List _key;

  @override
  Future<Uint8List> key() async => _key;
}

/// Seals one JSON document at [file] with AES-256-GCM.
///
/// File layout: `HMS1` ‖ 12-byte nonce ‖ ciphertext‖tag.
class EncryptedFileStore implements LocalStore {
  EncryptedFileStore({required this.file, required this.keys});

  final File file;
  final KeyProvider keys;

  Map<String, dynamic> _doc = <String, dynamic>{};
  Future<void> _chain = Future<void>.value();

  @override
  Map<String, dynamic> get doc => _doc;

  @override
  Future<Map<String, dynamic>> open() async {
    if (!await file.exists()) {
      _doc = <String, dynamic>{};
      return _doc;
    }
    final bytes = await file.readAsBytes();
    if (bytes.length <= kStoreMagic.length + _nonceLength) {
      throw StateError('Local store is corrupt: ${file.path}');
    }
    for (var i = 0; i < kStoreMagic.length; i++) {
      if (bytes[i] != kStoreMagic[i]) {
        throw StateError('Local store has an unknown header: ${file.path}');
      }
    }
    final key = await keys.key();
    final nonce = Uint8List.sublistView(bytes, kStoreMagic.length, kStoreMagic.length + _nonceLength);
    final sealed = Uint8List.sublistView(bytes, kStoreMagic.length + _nonceLength);

    final cipher = GCMBlockCipher(AESEngine())
      ..init(
        false,
        AEADParameters(
          KeyParameter(key),
          _tagBits,
          nonce,
          Uint8List.fromList(kStoreMagic),
        ),
      );
    final Uint8List plain;
    try {
      plain = cipher.process(sealed);
    } catch (err) {
      throw StateError(
        'Local store failed authentication (wrong key or tampered file): $err',
      );
    }
    final decoded = jsonDecode(utf8.decode(plain));
    if (decoded is! Map<String, dynamic>) {
      throw StateError('Local store does not hold a JSON object: ${file.path}');
    }
    _doc = decoded;
    return _doc;
  }

  @override
  Future<void> saveSection(String name, Object? value) {
    if (value == null) {
      _doc.remove(name);
    } else {
      _doc[name] = value;
    }
    // Serialize writes: two sections changed back to back must not
    // interleave on the same temp file.
    final next = _chain.then((_) => _sealAndWrite());
    _chain = next.catchError((Object _) {});
    return next;
  }

  @override
  Future<void> flush() => _chain;

  Future<void> _sealAndWrite() async {
    final key = await keys.key();
    final rng = Random.secure();
    final nonce = Uint8List.fromList(
      List<int>.generate(_nonceLength, (_) => rng.nextInt(256)),
    );

    final cipher = GCMBlockCipher(AESEngine())
      ..init(
        true,
        AEADParameters(
          KeyParameter(key),
          _tagBits,
          nonce,
          Uint8List.fromList(kStoreMagic),
        ),
      );
    final plain = Uint8List.fromList(utf8.encode(jsonEncode(_doc)));
    final sealed = cipher.process(plain);

    final bytes = Uint8List.fromList([
      ...kStoreMagic,
      ...nonce,
      ...sealed,
    ]);
    final tmp = File('${file.path}.tmp');
    await tmp.writeAsBytes(bytes, flush: true);
    await tmp.rename(file.path);
  }
}
