/// Encrypted local store (M4 / R4.2): the device must never hold readable
/// offline data, and it must never silently swallow a corrupted store.
library;

import 'dart:io';
import 'dart:typed_data';

import 'package:aditya_bionova_mobile/store/local_store.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  late Directory tmpDir;

  setUp(() {
    tmpDir = Directory.systemTemp.createTempSync('hms_store_test');
  });

  tearDown(() {
    if (tmpDir.existsSync()) tmpDir.deleteSync(recursive: true);
  });

  Uint8List keyOf(int fill) =>
      Uint8List.fromList(List<int>.generate(32, (_) => fill));

  EncryptedFileStore storeAt(String name, Uint8List key) => EncryptedFileStore(
        file: File('${tmpDir.path}/$name'),
        keys: FixedKeyProvider(key),
      );

  test('sections round trip across a reopen with the same key', () async {
    final store = storeAt('offline.json', keyOf(1));
    expect(await store.open(), isEmpty);

    await store.saveSection('outbox', {
      'ops': [
        {'id': 'op1', 'entity': 'queue_entry'}
      ],
    });
    await store.saveSection('directory', {
      'patients': [
        {'id': 'p1', 'full_name': 'मराठी नाव'}
      ],
    });

    final reopened = storeAt('offline.json', keyOf(1));
    final doc = await reopened.open();
    expect((doc['outbox'] as Map)['ops'], hasLength(1));
    expect(
      ((doc['directory'] as Map)['patients'] as List).first['full_name'],
      'मराठी नाव',
    );
  });

  test('nothing is readable at rest — no plaintext byte of the payload',
      () async {
    final store = storeAt('offline.json', keyOf(1));
    await store.open();
    await store.saveSection('directory', {
      'patients': [
        {'id': 'p1', 'full_name': 'Sunita Patil', 'phone': '9876543210'}
      ],
    });

    final bytes = File('${tmpDir.path}/offline.json').readAsBytesSync();
    final asText = String.fromCharCodes(bytes);
    for (final needle in ['Sunita', 'Patil', '9876543210', 'patients', 'p1']) {
      expect(asText.contains(needle), isFalse,
          reason: '"$needle" must not appear in the stored file');
    }
  });

  test('a tampered byte is detected (GCM tag) instead of misread', () async {
    final file = File('${tmpDir.path}/offline.json');
    final store = storeAt('offline.json', keyOf(1));
    await store.open();
    await store.saveSection('outbox', {
      'ops': [
        {'id': 'op1'}
      ],
    });

    final bytes = file.readAsBytesSync();
    bytes[bytes.length - 8] ^= 0x01;
    file.writeAsBytesSync(bytes);

    await expectLater(storeAt('offline.json', keyOf(1)).open(),
        throwsA(isA<StateError>()));
  });

  test('a wrong key is rejected', () async {
    final store = storeAt('offline.json', keyOf(1));
    await store.open();
    await store.saveSection('outbox', {'ops': <dynamic>[]});

    await expectLater(storeAt('offline.json', keyOf(2)).open(),
        throwsA(isA<StateError>()));
  });

  test('an unknown header raises instead of dropping pending work', () async {
    File('${tmpDir.path}/offline.json')
        .writeAsBytesSync(List<int>.filled(64, 0x41));

    await expectLater(storeAt('offline.json', keyOf(1)).open(),
        throwsA(isA<StateError>()));
  });

  test('writes are atomic — no temp file is left behind', () async {
    final store = storeAt('offline.json', keyOf(1));
    await store.open();
    await store.saveSection('outbox', {'ops': <dynamic>[]});

    expect(File('${tmpDir.path}/offline.json.tmp').existsSync(), isFalse);
    expect(File('${tmpDir.path}/offline.json').existsSync(), isTrue);
  });

  test('deleting a section persists the removal', () async {
    final store = storeAt('offline.json', keyOf(1));
    await store.open();
    await store.saveSection('rx_drafts', {'drafts': <dynamic>[]});
    await store.saveSection('rx_drafts', null);

    final doc = await storeAt('offline.json', keyOf(1)).open();
    expect(doc.containsKey('rx_drafts'), isFalse);
  });
}
