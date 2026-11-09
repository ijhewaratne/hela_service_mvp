import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Where the refresh token lives. The access token is never persisted.
abstract class TokenStore {
  Future<String?> readRefreshToken();
  Future<void> writeRefreshToken(String token);
  Future<void> clear();

  /// Refresh tokens of sessions the user left while offline. The server is asked to end them once a connection
  /// works again. Kept apart from the current token, so signing in with another number does not lose them.
  Future<List<String>> readPendingRevocations();
  Future<void> writePendingRevocations(List<String> tokens);
}

/// Android Keystore-backed storage (Q9 "secure token handling per client").
class SecureTokenStore implements TokenStore {
  SecureTokenStore([FlutterSecureStorage? storage]) : _s = storage ?? const FlutterSecureStorage();
  final FlutterSecureStorage _s;
  static const _key = 'hela.refresh_token';
  static const _pendingKey = 'hela.pending_revocations';

  @override
  Future<String?> readRefreshToken() => _s.read(key: _key);
  @override
  Future<void> writeRefreshToken(String token) => _s.write(key: _key, value: token);
  @override
  Future<void> clear() => _s.delete(key: _key);
  @override
  Future<List<String>> readPendingRevocations() async {
    final raw = await _s.read(key: _pendingKey);
    return raw == null || raw.isEmpty ? [] : raw.split('\n');
  }

  @override
  Future<void> writePendingRevocations(List<String> tokens) =>
      tokens.isEmpty ? _s.delete(key: _pendingKey) : _s.write(key: _pendingKey, value: tokens.join('\n'));
}

class MemoryTokenStore implements TokenStore {
  String? token;
  @override
  Future<String?> readRefreshToken() async => token;
  @override
  Future<void> writeRefreshToken(String t) async => token = t;
  @override
  Future<void> clear() async => token = null;
  List<String> pendingRevocations = [];
  @override
  Future<List<String>> readPendingRevocations() async => List.of(pendingRevocations);
  @override
  Future<void> writePendingRevocations(List<String> tokens) async => pendingRevocations = List.of(tokens);
}

/// Device-remembered language (FR-ID-02). Not a security boundary; kept beside the session for simplicity.
class LanguagePref {
  LanguagePref([FlutterSecureStorage? storage]) : _s = storage ?? const FlutterSecureStorage();
  final FlutterSecureStorage _s;
  Future<String?> read() => _s.read(key: 'hela.language');
  Future<void> write(String lang) => _s.write(key: 'hela.language', value: lang);
}
