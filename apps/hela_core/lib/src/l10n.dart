import 'package:flutter/widgets.dart';

/// Message keys with English as the reviewed source (Q61). Sinhala and Tamil are NOT machine-translated: until
/// human-reviewed text exists, those languages show English with a visible "translation pending" notice. Only the
/// language names themselves are given natively. Keys mirror the API's message_key values where they overlap.
class Strings {
  Strings(this.language);
  final String language; // 'si' | 'ta' | 'en'

  static const languageNames = {'si': 'සිංහල', 'ta': 'தமிழ்', 'en': 'English'};
  static const reviewed = {'en'};

  bool get translationPending => !reviewed.contains(language);

  String t(String key, [Map<String, String> args = const {}]) {
    var text = _en[key] ?? key;
    args.forEach((k, v) => text = text.replaceAll('{$k}', v));
    return text;
  }

  static Strings of(BuildContext context) => LanguageScope.of(context);

  static const _en = <String, String>{
    'app.synthetic': 'Synthetic test app. Do not enter real personal information.',
    'lang.choose': 'Choose your language',
    'lang.pending': 'This language is not reviewed yet. Text is shown in English until a human-reviewed translation exists.',
    'auth.phone': 'Mobile number (international format, e.g. +94771234567)',
    'auth.send_code': 'Send code',
    'auth.code': '6-digit code',
    'auth.verify': 'Sign in',
    'auth.dev_fill': 'Dev: fill code from sandbox inbox',
    'auth.code_sent': 'If this number can receive messages, a code has been sent.',
    'notice.title': 'Before you continue',
    'notice.accept': 'I have read and accept',
    'common.retry': 'Try again',
    'common.continue': 'Continue',
    'common.cancel': 'Cancel',
    'common.save': 'Save',
    'common.sign_out': 'Sign out',
    'common.reason': 'Reason',
    'common.ref': 'Reference',
    'error.offline': 'No connection. Nothing was changed. Try again.',
    'support.title': 'Report a problem',
    'support.body': 'A person reviews every report. Hela Service is not an emergency service.',
    'support.kind': 'What happened?',
    'support.severity': 'How serious is it?',
    'support.describe': 'Describe what happened (at least 10 characters)',
    'support.sent': 'Report received. A person will review it.',
    'safety.guidance_pending': 'Emergency contact guidance is pending local validation. If you are in danger, contact local emergency services.',
  };
}

class LanguageScope extends InheritedWidget {
  const LanguageScope({super.key, required this.strings, required this.setLanguage, required super.child});
  final Strings strings;
  final ValueChanged<String> setLanguage;

  static Strings of(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<LanguageScope>()?.strings ?? Strings('en');
  static ValueChanged<String> setterOf(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<LanguageScope>()!.setLanguage;

  @override
  bool updateShouldNotify(LanguageScope old) => old.strings.language != strings.language;
}
