/// API transport: one small interface between the app and the network.
///
/// Everything above this line (HmsClient, SyncEngine, screens) depends on
/// [ApiTransport], so tests substitute fakes and no widget test touches
/// the network.
library;

import 'dart:convert';

import 'package:http/http.dart' as http;

class ApiResponse {
  const ApiResponse({required this.status, this.json});

  final int status;

  /// Parsed response body: a JSON object, a JSON array, or null.
  final Object? json;

  bool get ok => status >= 200 && status < 300;

  Map<String, dynamic> get body =>
      json is Map<String, dynamic> ? json as Map<String, dynamic> : const {};

  String get detail {
    final raw = body['detail'];
    if (raw is String) return raw;
    // The duplicate-registration 409 carries {message, candidates}: show
    // the message, not the whole JSON blob.
    if (raw is Map && raw['message'] is String) return raw['message'] as String;
    return jsonEncode(json);
  }
}

class ApiException implements Exception {
  ApiException(this.status, this.detail);

  final int status;
  final String detail;

  @override
  String toString() => 'ApiException($status): $detail';
}

abstract interface class ApiTransport {
  Future<ApiResponse> send({
    required String method,
    required String path,
    Map<String, dynamic>? body,
    Map<String, String>? headers,
  });
}

/// Production transport: talks to `{baseUrl}/api/v1...`.
class HttpTransport implements ApiTransport {
  HttpTransport({required this.baseUrl, http.Client? client})
      : _client = client ?? http.Client();

  final String baseUrl;
  final http.Client _client;

  @override
  Future<ApiResponse> send({
    required String method,
    required String path,
    Map<String, dynamic>? body,
    Map<String, String>? headers,
  }) async {
    final uri = Uri.parse('$baseUrl/api/v1$path');
    final request = http.Request(method, uri)
      ..headers.addAll({'Content-Type': 'application/json', ...?headers});
    if (body != null) {
      request.body = jsonEncode(body);
    }

    final streamed = await _client.send(request);
    final response = await http.Response.fromStream(streamed);

    Object? parsed;
    if (response.body.isNotEmpty) {
      try {
        parsed = jsonDecode(response.body);
      } on FormatException {
        parsed = {'detail': response.body};
      }
    }
    return ApiResponse(status: response.statusCode, json: parsed);
  }
}
