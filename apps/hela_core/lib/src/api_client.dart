import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart' show MediaType;

import 'config.dart';
import 'session_store.dart';

/// Server error envelope (spec §13.1). The UI shows [messageKey] text and the correlation id for support.
class ApiException implements Exception {
  ApiException(this.status, this.code, this.messageKey, this.message, this.correlationId, this.retryable, this.details);

  final int status;
  final String code;
  final String messageKey;
  final String message;
  final String correlationId;
  final bool retryable;
  final Map<String, dynamic> details;

  /// Network failure: no server answer. The app must never assume the action happened (NFR-08).
  factory ApiException.offline() =>
      ApiException(0, 'OFFLINE', 'error.offline',
          'Connection lost. The action may have reached the server. Check the latest status before retrying.',
          '-', true, const {});

  List<String> get reasonCodes => (details['reason_codes'] as List?)?.cast<String>() ?? const [];

  @override
  String toString() => '$code: $message (ref $correlationId)';
}

enum _RefreshResult { refreshed, invalid, unavailable }

/// Customer/provider API client.
///
/// * Access token: memory only, 10 minutes. Refresh token: [TokenStore] (Android Keystore-backed in the apps).
/// * Refresh happens only on TOKEN_INVALID and only once at a time, because the server revokes the whole session
///   if a rotated refresh token is ever replayed.
/// * Commands that change state carry an Idempotency-Key that the caller keeps across retries (AT-020).
class HelaApi {
  HelaApi({required this.store, http.Client? client, String? baseUrl, this.timeout = const Duration(seconds: 20)})
      : _http = client ?? http.Client(),
        base = baseUrl ?? HelaConfig.apiBase;

  final TokenStore store;
  final http.Client _http;
  final String base;
  final Duration timeout;
  String? _access;
  Completer<_RefreshResult>? _refreshing;
  void Function()? onSignedOut;

  bool get hasSession => _access != null;

  static String newIdempotencyKey() {
    final r = Random.secure();
    return 'app-${List.generate(16, (_) => r.nextInt(256).toRadixString(16).padLeft(2, '0')).join()}';
  }

  Future<dynamic> get(String path) => _send('GET', path);
  Future<dynamic> post(String path, [Object? body, String? idempotencyKey]) =>
      _send('POST', path, body: body, idempotencyKey: idempotencyKey);
  Future<dynamic> put(String path, Object body) => _send('PUT', path, body: body);
  Future<dynamic> patch(String path, Object body) => _send('PATCH', path, body: body);
  Future<dynamic> delete(String path) => _send('DELETE', path);

  /// Multipart evidence upload (P02). The server sniffs the file content; [mediaType] must match it.
  Future<dynamic> upload(String path, Map<String, String> fields, List<int> bytes, String filename, String mediaType) async {
    Future<http.Response> attempt() async {
      final req = http.MultipartRequest('POST', Uri.parse('$base$path'))
        ..fields.addAll(fields)
        ..files.add(http.MultipartFile.fromBytes('file', bytes, filename: filename, contentType: MediaType.parse(mediaType)));
      if (_access != null) req.headers['Authorization'] = 'Bearer $_access';
      return http.Response.fromStream(await _http.send(req).timeout(timeout));
    }

    return _handle(await _guard(attempt), () async => _handle(await _guard(attempt), null));
  }

  Future<dynamic> _send(String method, String path, {Object? body, String? idempotencyKey}) async {
    Future<http.Response> attempt() {
      final headers = <String, String>{'Accept': 'application/json'};
      if (_access != null) headers['Authorization'] = 'Bearer $_access';
      if (body != null) headers['Content-Type'] = 'application/json';
      if (idempotencyKey != null) headers['Idempotency-Key'] = idempotencyKey;
      final req = http.Request(method, Uri.parse('$base$path'))..headers.addAll(headers);
      if (body != null) req.body = jsonEncode(body);
      return _http.send(req).timeout(timeout).then(http.Response.fromStream);
    }

    return _handle(await _guard(attempt), () async => _handle(await _guard(attempt), null));
  }

  Future<http.Response> _guard(Future<http.Response> Function() attempt) async {
    try {
      return await attempt();
    } on TimeoutException {
      throw ApiException.offline();
    } on http.ClientException {
      throw ApiException.offline();
    }
  }

  Future<dynamic> _handle(http.Response res, Future<dynamic> Function()? retry) async {
    if (res.statusCode >= 200 && res.statusCode < 300) {
      if (res.statusCode == 204 || res.body.isEmpty) return null;
      return jsonDecode(utf8.decode(res.bodyBytes));
    }
    final err = _toError(res);
    if (err.code == 'TOKEN_INVALID' && retry != null) {
      final outcome = await _refresh();
      if (outcome == _RefreshResult.refreshed) return retry();
      if (outcome == _RefreshResult.unavailable) {
        // A timeout, gateway error or rate limit is not proof that the session was revoked.
        // Do not erase the stored token or silently repeat the original state-changing request.
        throw ApiException(0, 'AUTH_UNAVAILABLE', 'error.auth_unavailable',
            'Your session could not be verified right now. Please check your connection and retry.',
            err.correlationId, true, const {});
      }
    }
    if (const {'SESSION_REVOKED', 'TOKEN_INVALID', 'ACCOUNT_INACTIVE', 'REFRESH_REUSED'}.contains(err.code)) {
      await signOutLocally();
    }
    throw err;
  }

  ApiException _toError(http.Response res) {
    try {
      final e = (jsonDecode(utf8.decode(res.bodyBytes)) as Map<String, dynamic>)['error'] as Map<String, dynamic>;
      return ApiException(res.statusCode, e['code'] as String, e['message_key'] as String, e['message'] as String,
          e['correlation_id'] as String, e['retryable'] as bool, (e['details'] as Map?)?.cast<String, dynamic>() ?? {});
    } catch (_) {
      return ApiException(res.statusCode, 'HTTP_${res.statusCode}', 'error.unexpected', 'Something went wrong.',
          res.headers['x-correlation-id'] ?? '-', res.statusCode >= 500, const {});
    }
  }

  /// Restores a session at app start from the stored refresh token.
  /// A temporary outage leaves the token in secure storage so login can recover later.
  Future<bool> resume() async => await _refresh() == _RefreshResult.refreshed;

  Future<_RefreshResult> _refresh() {
    final inFlight = _refreshing;
    if (inFlight != null) return inFlight.future;
    final done = Completer<_RefreshResult>();
    _refreshing = done;
    () async {
      try {
        final refresh = await store.readRefreshToken();
        if (refresh == null) return done.complete(_RefreshResult.invalid);
        final res = await _http
            .post(Uri.parse('$base/v1/auth/refresh'),
                headers: {'Content-Type': 'application/json'}, body: jsonEncode({'refresh_token': refresh}))
            .timeout(timeout);
        if (res.statusCode != 200) {
          // Only a definitive client/auth rejection should destroy a session.
          // A temporary server outage, timeout or rate limit is not such evidence.
          if (res.statusCode == 408 || res.statusCode == 429 || res.statusCode >= 500) {
            return done.complete(_RefreshResult.unavailable);
          }
          await signOutLocally();
          return done.complete(_RefreshResult.invalid);
        }
        await _adopt(jsonDecode(res.body) as Map<String, dynamic>);
        done.complete(_RefreshResult.refreshed);
      } catch (_) {
        // Preserve the stored token on transport failures and ambiguous responses.
        // Do not assume a state-changing operation did not reach the server.
        done.complete(_RefreshResult.unavailable);
      } finally {
        _refreshing = null;
      }
    }();
    return done.future;
  }

  Future<void> _adopt(Map<String, dynamic> tokens) async {
    _access = tokens['access_token'] as String;
    await store.writeRefreshToken(tokens['refresh_token'] as String);
  }

  // --- auth (FR-ID-01) ---------------------------------------------------------------------------------------
  Future<String> requestOtp(String phone) async =>
      (await post('/v1/auth/otp/request', {'phone': phone}) as Map<String, dynamic>)['challenge_id'] as String;

  Future<void> verifyOtp(String phone, String challengeId, String code, {String device = 'android'}) async {
    final tokens = await post('/v1/auth/otp/verify',
        {'phone': phone, 'challenge_id': challengeId, 'code': code, 'device_label': device}) as Map<String, dynamic>;
    await _adopt(tokens);
  }

  Future<void> logout() async {
    try {
      await post('/v1/auth/logout');
    } finally {
      await signOutLocally();
    }
  }

  Future<void> signOutLocally() async {
    final had = _access != null;
    _access = null;
    await store.clear();
    if (had) onSignedOut?.call();
  }

  /// Debug builds only: read the sandbox SMS inbox so testers can sign in without a real SMS vendor (Q8).
  Future<String?> devLatestCode(String phone) async {
    final token = HelaConfig.devInboxToken;
    if (token.isEmpty) return null;
    final res = await _http.post(Uri.parse('$base/v1/dev/sms-inbox/latest'),
        headers: {'Content-Type': 'application/json', 'X-Dev-Inbox-Token': token}, body: jsonEncode({'phone': phone}));
    if (res.statusCode != 200) return null;
    final body = (jsonDecode(res.body) as Map<String, dynamic>)['body'] as String;
    return RegExp(r'code: (\d{6})').firstMatch(body)?.group(1);
  }
}
