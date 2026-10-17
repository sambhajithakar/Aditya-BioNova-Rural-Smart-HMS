/// [IdMap]: local → server id substitution for offline-created entities.
library;

import 'package:aditya_bionova_mobile/sync/idmap.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('placeholders resolve deeply, in nested maps and lists', () {
    final ids = IdMap()..put('local-1', 'srv-1');

    final resolved = ids.substitute({
      'patient_id': IdMap.placeholder('local-1'),
      'nested': {
        'parent': IdMap.placeholder('local-1'),
        'items': [
          {'ref': IdMap.placeholder('local-1')},
          {'ref': 'plain'},
        ],
      },
    });

    expect(resolved, isNotNull);
    expect(resolved!['patient_id'], 'srv-1');
    expect((resolved['nested'] as Map)['parent'], 'srv-1');
    expect(
      (((resolved['nested'] as Map)['items'] as List).first as Map)['ref'],
      'srv-1',
    );
  });

  test('an unresolved placeholder blocks the payload entirely', () {
    final ids = IdMap();
    final resolved = ids.substitute({
      'patient_id': IdMap.placeholder('local-unknown'),
    });
    expect(resolved, isNull);
    expect(IdMap.hasUnresolved({'patient_id': IdMap.placeholder('x')}, ids),
        isTrue);
  });

  test('payloads without placeholders pass through untouched', () {
    final ids = IdMap();
    final payload = {'patient_id': 'srv-9', 'branch_id': 'b1'};
    expect(ids.substitute(payload), payload);
    expect(IdMap.hasUnresolved(payload, ids), isFalse);
  });

  test('the map survives serialization', () {
    final ids = IdMap.fromJson((IdMap()
          ..put('a', 'srv-a')
          ..put('b', 'srv-b'))
        .toJson());
    expect(ids.serverIdOf('a'), 'srv-a');
    expect(ids.serverIdOf('b'), 'srv-b');
  });
}
