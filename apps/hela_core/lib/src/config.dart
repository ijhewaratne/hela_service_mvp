import 'package:flutter/foundation.dart';

/// Build-time configuration via --dart-define. Nothing secret is compiled into release builds.
class HelaConfig {
  /// Android emulator reaches the host's 127.0.0.1 as 10.0.2.2.
  static const apiBase = String.fromEnvironment('HELA_API_BASE', defaultValue: 'http://10.0.2.2:8000');

  /// Sandbox SMS inbox token (Q8). Ignored in release builds even if supplied.
  static String get devInboxToken => kReleaseMode ? '' : const String.fromEnvironment('HELA_DEV_INBOX_TOKEN');
}
