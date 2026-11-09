import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hela_core/hela_core.dart';
import 'package:hela_provider/jobs.dart';
import 'package:hela_provider/main.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class FakeBackend {
  bool hasProfile = true;
  bool dropFirstAccept = false;
  final acceptKeys = <String?>[];
  final transitions = <Map<String, dynamic>>[];
  String status = 'accepted';
  int version = 1;
  Map<String, dynamic>? applied;

  http.Response _j(Object body, [int code = 200]) => http.Response(jsonEncode(body), code, headers: {'content-type': 'application/json'});
  http.Response _err(String code, int status) => _j({'error': {'code': code, 'message_key': 'k', 'message': code, 'correlation_id': 'c',
        'retryable': false, 'details': {}}}, status);

  Future<http.Response> handle(http.Request req) async {
    final key = '${req.method} ${req.url.path}';
    switch (key) {
      case 'POST /v1/auth/otp/request':
        return _j({'challenge_id': 'c1'}, 202);
      case 'POST /v1/auth/otp/verify':
        return _j({'access_token': 'A', 'refresh_token': 'R', 'session_id': 's'});
      case 'PATCH /v1/me':
        return _j({});
      case 'GET /v1/notices/current':
        return _j([]);
      case 'GET /v1/provider/me':
        return hasProfile
            ? _j({'provider_id': 'p1', 'application_state': 'approved', 'account_state': 'active', 'identity_verified': true,
                'age_verified': true, 'evidence': [], 'approvals': []})
            : _err('PROVIDER_NOT_FOUND', 404);
      case 'POST /v1/provider/apply':
        applied = jsonDecode(req.body) as Map<String, dynamic>;
        hasProfile = true;
        return _j({'provider_id': 'p1', 'application_state': 'draft'}, 201);
      case 'GET /v1/offers/me':
        return _j(acceptKeys.where((k) => k != null).length > 1 || status != 'accepted' ? [] : [
          {'id': 'o1', 'category_code': 'household_cleaning', 'start_at': '2026-11-07T04:30:00Z', 'end_at': '2026-11-07T06:30:00Z',
            'expires_at': DateTime.now().toUtc().add(const Duration(minutes: 9)).toIso8601String(), 'distance_band': '<2 km',
            'area_label': 'Synthetic Kandy Central', 'task_codes': ['dusting'], 'total_minor': 240000, 'currency': 'LKR'}
        ]);
      case 'POST /v1/offers/o1/accept':
        acceptKeys.add(req.headers['Idempotency-Key']);
        if (dropFirstAccept && acceptKeys.length == 1) throw http.ClientException('connection reset'); // response lost
        return _j({'booking_id': 'b1', 'status': 'accepted', 'replayed': acceptKeys.length > 1});
      case 'GET /v1/bookings/b1':
        return _j({
          'booking_id': 'b1', 'status': status, 'version': version, 'start_at': '2026-11-07T04:30:00Z', 'end_at': '2026-11-07T06:30:00Z',
          'task_codes': ['dusting'], 'price': {'amount_minor': 240000, 'payment_status': 'UNPAID'},
          if (const {'accepted', 'en_route', 'arrived', 'in_progress'}.contains(status))
            'address': {'text': '12 Synthetic Lane, Kandy', 'pin': '7.290600,80.633700'},
        });
      case 'POST /v1/bookings/b1/transitions':
        final body = jsonDecode(req.body) as Map<String, dynamic>;
        transitions.add(body);
        if (body['target_status'] == 'in_progress' && body['checkin_pin'] != '4821') return _err('CHECKIN_PIN_INVALID', 422);
        status = body['target_status'] as String;
        version++;
        return _j({'booking_id': 'b1', 'status': status, 'version': version});
    }
    return _err('NOT_FOUND', 404);
  }
}

Future<void> signIn(WidgetTester tester) async {
  await tester.tap(find.byKey(const Key('lang-en')));
  await tester.pumpAndSettle();
  await tester.enterText(find.byKey(const Key('phone')), '+94771000001');
  await tester.tap(find.byKey(const Key('send-code')));
  await tester.pumpAndSettle();
  await tester.enterText(find.byKey(const Key('code')), '123456');
  await tester.tap(find.byKey(const Key('verify')));
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 300));
}

class StubLanguagePref extends LanguagePref {
  @override
  Future<String?> read() async => 'en';
  @override
  Future<void> write(String lang) async {}
}

void main() {
  testWidgets('profile lookup outage does not show provider application', (tester) async {
    var down = true;
    final backend = FakeBackend();
    final api = HelaApi(store: MemoryTokenStore(), baseUrl: 'http://api', client: MockClient((req) async {
      if (req.method == 'GET' && req.url.path == '/v1/provider/me' && down) {
        return backend._err('TEMPORARY_UNAVAILABLE', 503);
      }
      return backend.handle(req);
    }));
    await tester.pumpWidget(ProviderApp(api: api, showMaps: false, offerPoll: const Duration(hours: 1)));
    await tester.pumpAndSettle();
    await signIn(tester);
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('retry-provider-profile')), findsOneWidget);
    expect(find.text('Work with Hela'), findsNothing);

    down = false;
    await tester.tap(find.byKey(const Key('retry-provider-profile')));
    await tester.pumpAndSettle();
    expect(find.text('Job offers'), findsOneWidget);
  });

  testWidgets('an unexpected profile response shows retry, not an endless spinner or the application form',
      (tester) async {
    var broken = true;
    final backend = FakeBackend();
    final api = HelaApi(store: MemoryTokenStore(), baseUrl: 'http://api', client: MockClient((req) async {
      if (req.method == 'GET' && req.url.path == '/v1/provider/me' && broken) {
        return backend._j(['not', 'a', 'profile']);   // 200, but not the shape the app expects
      }
      return backend.handle(req);
    }));
    await tester.pumpWidget(ProviderApp(api: api, showMaps: false, offerPoll: const Duration(hours: 1)));
    await tester.pumpAndSettle();
    await signIn(tester);
    await tester.pumpAndSettle();
    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(find.byKey(const Key('retry-provider-profile')), findsOneWidget);
    expect(find.text('Work with Hela'), findsNothing);
    expect(find.textContaining('Something went wrong'), findsOneWidget);
    expect(find.textContaining('not, a, profile'), findsNothing);   // raw response content is never shown

    broken = false;
    await tester.tap(find.byKey(const Key('retry-provider-profile')));
    await tester.pumpAndSettle();
    expect(find.text('Job offers'), findsOneWidget);
  });

  testWidgets('"sign in with a different number" during an outage ends the old session once back online',
      (tester) async {
    var online = false;
    final revoked = <String>[];
    final backend = FakeBackend();
    final store = MemoryTokenStore()..token = 'R-old-provider-session-1';
    final api = HelaApi(store: store, baseUrl: 'http://api', client: MockClient((req) async {
      if (req.url.path == '/v1/auth/refresh') return http.Response('', 503);
      if (!online) throw http.ClientException('no route');
      if (req.url.path == '/v1/auth/revoke') {
        revoked.add((jsonDecode(req.body) as Map)['refresh_token'] as String);
        return http.Response('', 204);
      }
      return backend.handle(req);
    }));
    await tester.pumpWidget(ProviderApp(
      api: api, languagePref: StubLanguagePref(), showMaps: false, offerPoll: const Duration(hours: 1)));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('choose-other-account')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('phone')), findsOneWidget);
    expect(store.token, isNull);
    expect(store.pendingRevocations, ['R-old-provider-session-1']);

    online = true;
    await tester.enterText(find.byKey(const Key('phone')), '+94771000002');
    await tester.tap(find.byKey(const Key('send-code')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('code')), '123456');
    await tester.tap(find.byKey(const Key('verify')));
    await tester.pumpAndSettle();
    expect(revoked, ['R-old-provider-session-1']);
    expect(store.pendingRevocations, isEmpty);
  });

  testWidgets('saved provider session survives a startup outage and recovers on retry', (tester) async {
    var online = false;
    final backend = FakeBackend();
    final store = MemoryTokenStore()..token = 'R1';
    final api = HelaApi(store: store, baseUrl: 'http://api', client: MockClient((req) async {
      if (req.url.path == '/v1/auth/refresh') {
        if (!online) return http.Response('', 503);
        return http.Response(jsonEncode({'access_token': 'A2', 'refresh_token': 'R2'}), 200);
      }
      return backend.handle(req);
    }));
    await tester.pumpWidget(ProviderApp(
      api: api, languagePref: StubLanguagePref(), showMaps: false, offerPoll: const Duration(hours: 1)));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('retry-session')), findsOneWidget);
    expect(find.byKey(const Key('phone')), findsNothing);
    expect(store.token, 'R1');

    online = true;
    await tester.tap(find.byKey(const Key('retry-session')));
    await tester.pumpAndSettle();
    expect(find.text('Job offers'), findsOneWidget);
    expect(store.token, 'R2');
  });

  setUp(() {});

  testWidgets('new provider sees onboarding and can apply', (tester) async {
    final backend = FakeBackend()..hasProfile = false;
    final api = HelaApi(store: MemoryTokenStore(), baseUrl: 'http://api', client: MockClient(backend.handle));
    await tester.pumpWidget(ProviderApp(api: api, showMaps: false, offerPoll: const Duration(hours: 1)));
    await tester.pumpAndSettle();
    await signIn(tester);
    await tester.pumpAndSettle();
    expect(find.text('Work with Hela'), findsOneWidget);
    await tester.enterText(find.byKey(const Key('legal-name')), 'Synthetic Provider');
    await tester.tap(find.byKey(const Key('dob')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('OK'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Apply'));
    await tester.pumpAndSettle();
    expect(backend.applied!['legal_name'], 'Synthetic Provider');
    expect(find.text('Job offers'), findsOneWidget);
  });

  testWidgets('offer without address -> accept survives a lost response -> address -> PIN check-in', (tester) async {
    tester.view.physicalSize = const Size(1080, 2600);
    tester.view.devicePixelRatio = 2.5;
    addTearDown(tester.view.reset);
    final backend = FakeBackend()..dropFirstAccept = true;
    final api = HelaApi(store: MemoryTokenStore(), baseUrl: 'http://api', client: MockClient(backend.handle));
    await tester.pumpWidget(ProviderApp(api: api, showMaps: false, offerPoll: const Duration(hours: 1)));
    await tester.pumpAndSettle();
    await signIn(tester);
    await tester.pump(const Duration(seconds: 1));

    // P05: offer card shows area, band, time, tasks, price, countdown; never the address.
    expect(find.byKey(const Key('offer-o1')), findsOneWidget);
    expect(find.textContaining('Synthetic Kandy Central · <2 km'), findsOneWidget);
    expect(find.textContaining('Synthetic Lane'), findsNothing);
    expect(find.byKey(const Key('countdown')), findsOneWidget);

    // First tap: the response is lost. The app must not pretend it got the job;
    // the server may already have accepted it, so the outcome is uncertain.
    await tester.tap(find.widgetWithText(FilledButton, 'Accept'));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.textContaining('may have reached the server'), findsOneWidget);
    // Retry uses the same idempotency key, so the server returns the same booking instead of a second one.
    await tester.tap(find.widgetWithText(FilledButton, 'Accept'));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    expect(backend.acceptKeys, hasLength(2));
    expect(backend.acceptKeys[0], backend.acceptKeys[1]);

    // P06: address only now.
    expect(find.byKey(const Key('address')), findsOneWidget);
    expect(find.textContaining('12 Synthetic Lane'), findsOneWidget);

    Future<void> press(String label) async {
      await tester.tap(find.widgetWithText(FilledButton, label));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));
    }

    await press('I am on my way');
    await press('I have arrived');
    await tester.enterText(find.byKey(const Key('checkin-pin')), '0000');
    await press('Start work');
    expect(find.textContaining('CHECKIN_PIN_INVALID'), findsOneWidget);
    expect(backend.status, 'arrived');
    await tester.enterText(find.byKey(const Key('checkin-pin')), '4821');
    await press('Start work');
    expect(backend.status, 'in_progress');
    await press('Work is finished');
    expect(backend.status, 'completed_pending_confirmation');
    expect(backend.transitions.map((t) => t['expected_version']), [1, 2, 3, 3, 4]); // version-checked every time
    expect(find.byKey(const Key('address')), findsNothing); // address hidden once work is done
  });

  testWidgets('job screen scrolls on a small phone with large text: every action stays reachable', (tester) async {
    // 360 x 640 logical pixels at 1.5x text. Before the fix, the body did not scroll and the actions at the bottom
    // were cut off (found by the macOS end-to-end run).
    tester.view.physicalSize = const Size(1080, 1920);
    tester.view.devicePixelRatio = 3;
    tester.platformDispatcher.textScaleFactorTestValue = 1.5;
    addTearDown(tester.view.reset);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
    final backend = FakeBackend()..status = 'en_route';
    final api = HelaApi(store: MemoryTokenStore(), baseUrl: 'http://api', client: MockClient(backend.handle));
    await tester.pumpWidget(MaterialApp(home: JobScreen(api: api, bookingId: 'b1')));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);   // no overflow
    final leave = find.widgetWithText(FilledButton, 'Leave for safety / cannot attend');
    await tester.scrollUntilVisible(leave, 200, scrollable: find.byType(Scrollable).first);
    expect(leave.hitTestable(), findsOneWidget);
  });
}
