/// Shared test double for [ApiTransport]: records every call and returns
/// whatever the handler decides. No widget or unit test touches the
/// network.
library;

import 'package:aditya_bionova_mobile/api/transport.dart';

class RecordedCall {
  RecordedCall(this.method, this.path, this.headers, this.body);

  final String method;
  final String path;
  final Map<String, String>? headers;
  final Map<String, dynamic>? body;

  String get route => '$method ${path.split('?').first}';
}

class FakeTransport implements ApiTransport {
  FakeTransport(this.handler);

  factory FakeTransport.routes(Map<String, ApiResponse> routes) =>
      FakeTransport((call) async =>
          routes[call.route] ??
          json({'detail': 'no stub for ${call.route}'}, status: 404));

  final Future<ApiResponse> Function(RecordedCall call) handler;
  final List<RecordedCall> calls = [];

  @override
  Future<ApiResponse> send({
    required String method,
    required String path,
    Map<String, dynamic>? body,
    Map<String, String>? headers,
  }) {
    final call = RecordedCall(method, path, headers, body);
    calls.add(call);
    return handler(call);
  }
}

ApiResponse json(Object? data, {int status = 200}) =>
    ApiResponse(status: status, json: data);
