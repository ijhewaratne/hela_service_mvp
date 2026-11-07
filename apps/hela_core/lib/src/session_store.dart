import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Where the refresh token lives. The access token is never persisted.
abstract class TokenStore {
  Future<String?> readRefreshToken();
  Future<void> writeRefreshToken(String token);
  Future<void> clear();
}

/// Android Keystore-backed storage (Q9 "secure token handling per client").
class SecureTokenStore implements TokenStore {
  SecureTokenStore([FlutterSecureStorage? storage]) : _s = storage ?? const FlutterSecureStorage();
  final FlutterSecureStorage _s;
  static const _key = 'hela.refresh_token';

  @override
  Future<String?> readRefreshToken() => _s.read(key: _key);
  @override
  Future<void> writeRefreshToken(String token) => _s.write(key: _key, value: token);
  @override
  Future<void> clear() => _s.delete(key: _key);
}

class MemoryTokenStore implements TokenStore {
  String? token;
  @override
  Future<String?> readRefreshToken() async => token;
  @override
  Future<void> writeRefreshToken(String t) async => token = t;
  @override
  Future<void> clear() async => token = null;
}

/// Device-remembered language (FR-ID-02). Not a security boundary; kept beside the session for simplicity.
class LanguagePref {
  LanguagePref([FlutterSecureStorage? storage]) : _s = storage ?? const FlutterSecureStorage();
  final FlutterSecureStorage _s;
  Future<String?> read() => _s.read(key: 'hela.language');
  Future<void> write(String lang) => _s.write(key: 'hela.language', value: lang);
}
