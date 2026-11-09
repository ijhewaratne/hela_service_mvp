import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hela_core/hela_core.dart';
import 'package:hela_customer/main.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _Language extends LanguagePref {
  @override
  Future<String?> read() async => 'en';
  @override
  Future<void> write(String language) async {}
}

class _TestBackend {
  final paths = <String>[];
  Map<String, dynamic>? savedProfile;
  bool requiredNotice = false;

  http.Response _json(Object content, [int status = 200]) => http.Response(
      jsonEncode(content), status, headers: {'content-type': 'application/json'});

  Future<http.Response> handle(http.Request request) async {
    final path = '${request.method} ${request.url.path}';
    paths.add(path);
    switch (path) {
      case 'POST /v1/auth/otp/request':
        return _json({'challenge_id': 'synthetic-challenge', 'expires_in': 300}, 202);
      case 'POST /v1/auth/otp/verify':
        return _json({'access_token': 'A', 'refresh_token': 'R', 'session_id': 'S'});
      case 'PATCH /v1/me':
        savedProfile = jsonDecode(request.body) as Map<String, dynamic>;
        return _json({'display_name': savedProfile?['display_name']});
      case 'GET /v1/notices/current':
        return _json(requiredNotice ? [{
          'id': 'n1',
          'title': 'Synthetic Privacy Notice',
          'body': 'Synthetic test notice only.',
          'version': 1,
          'approval_status': 'UNAPPROVED',
        }] : []);
      case 'POST /v1/notices/n1/accept':
        return _json({'accepted': true});
      case 'GET /v1/provider/me':
        return _json({'error': {'code': 'PROVIDER_NOT_FOUND', 'message_key': 'provider.not_found',
          'message': 'Provider profile not found', 'correlation_id': 'test',
          'retryable': false, 'details': <String, dynamic>{}}}, 404);
      case 'GET /v1/regions':
        return _json([{'code': 'synthetic', 'name': 'Synthetic Kandy'}]);
      case 'GET /v1/categories':
        return _json([]);
      default:
        return _json({});
    }
  }
}

HelaApp _app(_TestBackend backend, {String adminPortalUrl = ''}) => HelaApp(
  api: HelaApi(store: MemoryTokenStore(), baseUrl: 'http://synthetic',
    client: MockClient(backend.handle)),
  languagePref: _Language(),
  showMaps: false,
  offerPoll: const Duration(hours: 1),
  adminPortalUrl: adminPortalUrl,
);

void main() {
  testWidgets('entry shows four distinct journeys before phone login', (tester) async {
    final backend = _TestBackend();
    await tester.pumpWidget(_app(backend));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('role-customer')), findsOneWidget);
    expect(find.byKey(const Key('role-provider')), findsOneWidget);
    expect(find.byKey(const Key('role-recipient')), findsOneWidget);
    expect(find.byKey(const Key('role-admin')), findsOneWidget);
    expect(backend.paths, isEmpty);
    expect(find.byKey(const Key('phone')), findsNothing);
  });

  testWidgets('service recipient learns about SMS consent without creating an account', (tester) async {
    final backend = _TestBackend();
    await tester.pumpWidget(_app(backend));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('role-recipient')));
    await tester.pumpAndSettle();
    expect(find.textContaining('one-time SMS'), findsOneWidget);
    expect(find.textContaining('no separate service-recipient account'), findsOneWidget);
    expect(find.byKey(const Key('phone')), findsNothing);
    expect(backend.paths, isEmpty);
    await tester.tap(find.byKey(const Key('recipient-back')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('role-customer')), findsOneWidget);
  });

  testWidgets('administrator choice does not create an account or offer customer OTP', (tester) async {
    final backend = _TestBackend();
    await tester.pumpWidget(_app(backend, adminPortalUrl: 'http://untrusted.test/admin'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('role-admin')));
    await tester.pumpAndSettle();
    expect(find.textContaining('separate secure web console'), findsOneWidget);
    expect(find.byKey(const Key('open-admin-portal')), findsNothing);
    expect(find.textContaining('not been configured'), findsOneWidget);
    expect(find.byKey(const Key('phone')), findsNothing);
    expect(backend.paths, isEmpty);
  });

  testWidgets('create account uses phone ownership and opens profile then provider review', (tester) async {
    tester.view.physicalSize = const Size(1080, 2400);
    tester.view.devicePixelRatio = 3;
    addTearDown(tester.view.reset);
    final backend = _TestBackend();
    await tester.pumpWidget(_app(backend));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const Key('role-provider')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('entry-create-account')), findsOneWidget);
    await tester.tap(find.byKey(const Key('entry-create-account')));
    await tester.pumpAndSettle();
    final send = find.byKey(const Key('send-code'));
    expect(tester.widget<FilledButton>(send).onPressed, isNull);
    await tester.tap(find.byKey(const Key('registration-ack')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('phone')), '+94770000001');
    await tester.tap(send);
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('code')), '123456');
    // The primary confirmation is fixed at the bottom even with longer registration disclosures.
    expect(find.byKey(const Key('verify')).hitTestable(), findsOneWidget);
    await tester.tap(find.byKey(const Key('verify')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('setup-display-name')), findsOneWidget);
    await tester.enterText(find.byKey(const Key('setup-display-name')), 'Synthetic Worker');
    await tester.tap(find.byKey(const Key('setup-save')));
    await tester.pumpAndSettle();
    expect(backend.savedProfile?['display_name'], 'Synthetic Worker');
    expect(find.text('Work with Hela'), findsOneWidget);
    expect(backend.paths, contains('GET /v1/provider/me'));
    expect(find.textContaining('approved to receive jobs'), findsNothing);
    expect(tester.takeException(), isNull);
  });


  testWidgets('declining required notices blocks the service and does not acknowledge terms', (tester) async {
    tester.view.physicalSize = const Size(1080, 2400);
    tester.view.devicePixelRatio = 3;
    addTearDown(tester.view.reset);
    final backend = _TestBackend()..requiredNotice = true;
    await tester.pumpWidget(_app(backend));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('role-customer')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('entry-sign-in')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('phone')), '+94770000001');
    await tester.tap(find.byKey(const Key('send-code')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('code')), '123456');
    await tester.tap(find.byKey(const Key('verify')));
    await tester.pumpAndSettle();
    expect(find.text('Synthetic Privacy Notice'), findsOneWidget);
    expect(find.byKey(const Key('decline-notice')), findsOneWidget);
    await tester.tap(find.byKey(const Key('decline-notice')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('role-customer')), findsOneWidget);
    expect(find.byKey(const Key('switch-role')), findsNothing);
    expect(backend.paths, isNot(contains('POST /v1/notices/n1/accept')));
  });

  testWidgets('sign in uses passwordless OTP and allows account switching', (tester) async {
    tester.view.physicalSize = const Size(1080, 2400);
    tester.view.devicePixelRatio = 3;
    addTearDown(tester.view.reset);
    final backend = _TestBackend();
    await tester.pumpWidget(_app(backend));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('role-customer')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('entry-sign-in')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Sign in without a password'), findsOneWidget);
    expect(find.textContaining('no password to reset'), findsOneWidget);
    await tester.enterText(find.byKey(const Key('phone')), '+94770000001');
    await tester.tap(find.byKey(const Key('send-code')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('change-phone')), findsOneWidget);
    await tester.enterText(find.byKey(const Key('code')), '123456');
    await tester.tap(find.byKey(const Key('verify')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('switch-role')), findsOneWidget);
    await tester.tap(find.byKey(const Key('switch-role')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('role-provider')), findsOneWidget);
    await tester.tap(find.byKey(const Key('role-provider')));
    await tester.pumpAndSettle();
    // The same verified account may apply, but is not automatically an approved provider.
    expect(find.text('Work with Hela'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
