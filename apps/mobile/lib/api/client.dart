/// Thin typed client for the endpoints the mobile app needs.
///
/// Authenticated requests carry the session bearer token; errors surface
/// the API's `detail` verbatim (the same 403/409/422 the web shows).
library;

import '../auth/session.dart';
import 'transport.dart';

class HmsClient {
  HmsClient({
    required this.transport,
    required this.session,
    this.onUnauthorized,
  });

  final ApiTransport transport;
  final Session session;
  final void Function()? onUnauthorized;

  /// Raw call that returns the [ApiResponse] instead of throwing, so the
  /// sync engine can distinguish 409 (conflict) from 5xx (retry later).
  Future<ApiResponse> sendRaw(
    String method,
    String path, {
    Map<String, dynamic>? body,
    Map<String, String>? extraHeaders,
  }) async {
    final token = await session.accessToken();
    final headers = <String, String>{
      if (token != null) 'Authorization': 'Bearer $token',
      ...?extraHeaders,
    };
    final res = await transport.send(
      method: method,
      path: path,
      body: body,
      headers: headers,
    );
    if (res.status == 401) {
      onUnauthorized?.call();
    }
    return res;
  }

  /// Same as [sendRaw] but throws [ApiException] on non-2xx responses.
  Future<Object?> request(
    String method,
    String path, {
    Map<String, dynamic>? body,
    Map<String, String>? extraHeaders,
  }) async {
    final res = await sendRaw(method, path,
        body: body, extraHeaders: extraHeaders);
    if (!res.ok) {
      throw ApiException(res.status, res.detail);
    }
    return res.json;
  }

  Future<Map<String, dynamic>> requestMap(
    String method,
    String path, {
    Map<String, dynamic>? body,
  }) async {
    final json = await request(method, path, body: body);
    if (json is! Map<String, dynamic>) {
      throw ApiException(200, 'Unexpected response shape for $path');
    }
    return json;
  }

  Future<void> login({
    required String email,
    required String password,
    String? organizationSlug,
  }) async {
    final res = await transport.send(
      method: 'POST',
      path: '/auth/login',
      body: {
        'email': email,
        'password': password,
        if (organizationSlug != null && organizationSlug.isNotEmpty)
          'organization_slug': organizationSlug,
      },
    );
    if (!res.ok) {
      throw ApiException(res.status, res.detail);
    }
    final tokens = res.body['tokens'];
    if (tokens is! Map<String, dynamic>) {
      throw ApiException(res.status, 'Malformed login response');
    }
    await session.save(
      accessToken: tokens['access_token'] as String,
      refreshToken: tokens['refresh_token'] as String,
    );
  }

  Future<Map<String, dynamic>> me() => requestMap('GET', '/auth/me');

  Future<Map<String, dynamic>> listPatients({int limit = 100}) =>
      requestMap('GET', '/patients?limit=$limit');

  Future<List<dynamic>> listBranches() async {
    final json = await request('GET', '/branches');
    if (json is! List) {
      throw ApiException(200, 'Unexpected response shape for /branches');
    }
    return json;
  }
}
