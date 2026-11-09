import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hela_core/hela_core.dart';
import 'package:hela_customer/booking_flow.dart';
import 'package:hela_customer/bookings.dart';
import 'package:hela_customer/main.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

/// A tiny in-memory stand-in for the API, recording what the app sent.
class FakeBackend {
  final calls = <String>[];
  final bodies = <String, Map<String, dynamic>>{};
  final idempotencyKeys = <String?>[];
  String requestStatus = 'offering';
  String? bookingId;

  http.Response _j(Object body, [int status = 200]) =>
      http.Response(jsonEncode(body), status, headers: {'content-type': 'application/json'});

  Future<http.Response> handle(http.Request req) async {
    final key = '${req.method} ${req.url.path}';
    calls.add(key);
    if (req.body.isNotEmpty) bodies[key] = jsonDecode(req.body) as Map<String, dynamic>;
    switch (key) {
      case 'POST /v1/auth/otp/request':
        return _j({'challenge_id': 'c1', 'expires_in': 300}, 202);
      case 'POST /v1/auth/otp/verify':
        return _j({'access_token': 'A', 'refresh_token': 'R', 'session_id': 's'});
      case 'PATCH /v1/me':
        return _j({});
      case 'GET /v1/notices/current':
        return _j([]);
      case 'GET /v1/regions':
        return _j([{'code': 'synthetic_kandy_central', 'name': 'Synthetic Kandy Central'}]);
      case 'GET /v1/categories':
        return _j([{
          'code': 'household_cleaning', 'display_name': 'Household cleaning', 'policy_version': 1, 'approval_status': 'UNAPPROVED',
          'min_duration_minutes': 60, 'max_duration_minutes': 480, 'allowed_tasks': ['dusting', 'tidying'],
          'prohibited_tasks': ['clinical_tasks'],
        }]);
      case 'GET /v1/addresses':
        return _j([{'id': 'addr1', 'label': 'Home', 'area_label': 'Synthetic Kandy Central', 'address_text': '12 Synthetic Lane'}]);
      case 'GET /v1/beneficiaries':
        return _j([]);
      case 'POST /v1/quotes/preview':
        return _j({
          'quote_id': 'q1', 'currency': 'LKR', 'basis': 'hourly_estimate', 'total_minor': 240000, 'policy_version': 1,
          'price_approval_status': 'UNAPPROVED', 'expires_at': '2026-11-07T04:30:00Z',
          'components': [{'code': 'base_fee', 'amount_minor': 0}, {'code': 'labour', 'amount_minor': 240000}],
          'assumptions': ['Payment is made offline to the provider.'], 'cancellation_terms': {'fee_lkr_minor': 0},
        }, 201);
      case 'POST /v1/quotes/q1/accept':
        return _j({'accepted': true});
      case 'POST /v1/requests':
        idempotencyKeys.add(req.headers['Idempotency-Key']);
        return _j({'request_id': 'r1', 'status': 'requested'}, 201);
      case 'GET /v1/requests/r1':
        return _j({'request_id': 'r1', 'status': requestStatus, 'booking_id': bookingId});
      case 'GET /v1/bookings/b1':
        return _j({
          'booking_id': 'b1', 'status': 'accepted', 'version': 1, 'start_at': '2026-11-07T04:30:00Z', 'end_at': '2026-11-07T06:30:00Z',
          'provider': {'display_name': 'Provider 001', 'approved_category': 'household_cleaning'}, 'checkin_pin': '4821',
          'price': {'amount_minor': 240000, 'payment_status': 'UNPAID', 'platform_collected_funds': false},
        });
    }
    return _j({'error': {'code': 'NOT_FOUND', 'message_key': 'x', 'message': 'nf $key', 'correlation_id': 'c', 'retryable': false, 'details': {}}}, 404);
  }
}

class StubLanguagePref extends LanguagePref {
  @override
  Future<String?> read() async => 'en';
  @override
  Future<void> write(String lang) async {}
}

void main() {
  testWidgets('saved customer session survives a startup outage and recovers on retry', (tester) async {
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
    await tester.pumpWidget(CustomerApp(api: api, languagePref: StubLanguagePref(), showMaps: false));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('retry-session')), findsOneWidget);
    expect(find.byKey(const Key('phone')), findsNothing);
    expect(store.token, 'R1');

    online = true;
    await tester.tap(find.byKey(const Key('retry-session')));
    await tester.pumpAndSettle();
    expect(find.text('Book a service'), findsOneWidget);
    expect(store.token, 'R2');
  });

  testWidgets('language -> sign in -> book cleaning -> honest searching state -> booking with PIN', (tester) async {
    tester.view.physicalSize = const Size(1080, 2400);
    tester.view.devicePixelRatio = 2.5;
    addTearDown(tester.view.reset);
    final backend = FakeBackend();
    final api = HelaApi(store: MemoryTokenStore(), baseUrl: 'http://api', client: MockClient(backend.handle));

    await tester.pumpWidget(CustomerApp(api: api, showMaps: false));
    await tester.pumpAndSettle();

    // C01: Sinhala chosen -> translation-pending notice is shown, no machine translation.
    await tester.tap(find.byKey(const Key('lang-si')));
    await tester.pumpAndSettle();
    expect(find.textContaining('not reviewed yet'), findsOneWidget);

    // C02: OTP sign-in.
    await tester.enterText(find.byKey(const Key('phone')), '+94770000001');
    await tester.tap(find.byKey(const Key('send-code')));
    await tester.pumpAndSettle();
    await tester.enterText(find.byKey(const Key('code')), '123456');
    await tester.tap(find.byKey(const Key('verify')));
    await tester.pumpAndSettle();
    expect(backend.bodies['PATCH /v1/me'], {'preferred_language': 'si'}); // FR-ID-02 synced to the server

    // C03 -> C04..C07
    await tester.tap(find.byKey(const Key('cat-household_cleaning')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('addr-Home')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('next-address')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('next-who')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('task-dusting')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('next-tasks')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('See price'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('total')), findsOneWidget);
    expect(find.text('LKR 2,400.00'), findsWidgets);
    expect(find.textContaining('Not an approved tariff'), findsOneWidget);

    // Cannot request without accepting the terms.
    final request = find.widgetWithText(FilledButton, 'Request this service');
    expect(tester.widget<FilledButton>(request).onPressed, isNull);
    await tester.ensureVisible(find.byKey(const Key('accept-terms')));
    await tester.tap(find.byKey(const Key('accept-terms')));
    await tester.pumpAndSettle();
    await tester.ensureVisible(request);
    await tester.tap(request);
    await tester.pump();
    await tester.pump(const Duration(seconds: 1)); // route transition (spinner never settles)

    expect(backend.bodies['POST /v1/requests'], {'quote_id': 'q1', 'task_codes': ['dusting']});
    expect(backend.idempotencyKeys.single, startsWith('app-'));
    expect(backend.bodies['POST /v1/quotes/preview']!['address_id'], 'addr1');

    // C08: never "confirmed" before the server has a booking (NFR-08).
    expect(find.byType(FindingProviderScreen), findsOneWidget);
    expect(find.textContaining('This is not confirmed yet'), findsOneWidget);
    expect(find.byKey(const Key('open-booking')), findsNothing);

    backend
      ..requestStatus = 'accepted'
      ..bookingId = 'b1';
    await tester.pump(const Duration(seconds: 4)); // next poll
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('open-booking')));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('pin')), findsOneWidget);
    expect(find.text('4821'), findsOneWidget);
    expect(find.textContaining('Provider 001'), findsOneWidget);
  });

  testWidgets('booking screen scrolls on a small phone with large text: cancelling stays reachable', (tester) async {
    tester.view.physicalSize = const Size(1080, 1920);   // 360 x 640 dp
    tester.view.devicePixelRatio = 3;
    tester.platformDispatcher.textScaleFactorTestValue = 1.5;
    addTearDown(tester.view.reset);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
    final api = HelaApi(store: MemoryTokenStore(), baseUrl: 'http://api', client: MockClient(FakeBackend().handle));
    await tester.pumpWidget(MaterialApp(home: BookingScreen(api: api, bookingId: 'b1')));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);   // no overflow
    final cancel = find.widgetWithText(FilledButton, 'Cancel booking (no fee)');
    await tester.scrollUntilVisible(cancel, 200, scrollable: find.byType(Scrollable).first);
    expect(cancel.hitTestable(), findsOneWidget);
  });
}
