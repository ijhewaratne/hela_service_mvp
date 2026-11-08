// End-to-end: the real customer app against the real local API and worker (no fakes). Synthetic data only.
//
//   scripts/app_e2e.sh   (needs the dev API on 127.0.0.1:8000 and the worker running; starts neither)
//
// The provider side is driven over HTTP with a second client; apps/provider has the mirror-image test.
import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hela_core/hela_core.dart';
import 'package:hela_customer/booking_flow.dart';
import 'package:hela_customer/main.dart';
import 'package:integration_test/integration_test.dart';

const providerPhone = String.fromEnvironment('HELA_E2E_PROVIDER_PHONE', defaultValue: '+94771234504');

/// Pumps frames while real network calls complete, until [finder] shows something or [timeout] passes.
Future<void> pumpUntil(WidgetTester tester, Finder finder, {Duration timeout = const Duration(seconds: 30)}) async {
  final end = DateTime.now().add(timeout);
  while (DateTime.now().isBefore(end)) {
    await tester.pump(const Duration(milliseconds: 200));
    if (finder.evaluate().isNotEmpty) return;
  }
  final visible = find.byType(Text).evaluate().map((e) => (e.widget as Text).data).whereType<String>().join(' | ');
  throw TimeoutException('Timed out waiting for $finder. On screen: $visible');
}

/// Lets running animations (a Stepper step opening, a route transition) finish, for at most a second. Not
/// pumpAndSettle: progress indicators on the searching screen never stop animating.
Future<void> settle(WidgetTester tester) async {
  for (var i = 0; i < 10 && tester.binding.hasScheduledFrame; i++) {
    await tester.pump(const Duration(milliseconds: 100));
  }
}

Future<void> tapAndWait(WidgetTester tester, Finder target, Finder next) async {
  await settle(tester);
  await tester.ensureVisible(target);
  await settle(tester);
  await tester.tap(target);
  await pumpUntil(tester, next);
}

/// Accepts each draft notice dialog shown after sign-in (FR-ID-05), as a person would, one at a time.
Future<void> acceptNotices(WidgetTester tester, HelaApi api) async {
  final count = (await api.get('/v1/notices/current?language=en') as List).length;
  final accept = find.widgetWithText(FilledButton, 'I have read and accept');
  for (var i = 0; i < count; i++) {
    await pumpUntil(tester, accept);
    expect(find.textContaining('UNAPPROVED'), findsWidgets);          // draft status is shown, never hidden
    await tester.tap(accept);
    await tester.pump(const Duration(milliseconds: 300));
  }
}

Future<HelaApi> signInOverHttp(String phone) async {
  final api = HelaApi(store: MemoryTokenStore());
  final challenge = await api.requestOtp(phone);
  final code = await api.devLatestCode(phone);
  await api.verifyOtp(phone, challenge, code!, device: 'e2e');
  return api;
}

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  WidgetController.hitTestWarningShouldBeFatal = true;   // a tap that misses fails at once, with the reason

  testWidgets('customer books cleaning in the real app; a real provider accepts; the app shows the booking',
      (tester) async {
    expect(HelaConfig.devInboxToken, isNotEmpty, reason: 'run with --dart-define HELA_DEV_INBOX_TOKEN=...');
    // A fresh synthetic customer each run (+94 77 9xx xxxx is not allocated to anyone real in this test plan).
    final phone = '+947790${(Random().nextInt(900000) + 100000)}';
    final api = HelaApi(store: MemoryTokenStore());

    await tester.pumpWidget(CustomerApp(api: api, showMaps: false));
    await pumpUntil(tester, find.byKey(const Key('lang-en')));

    // C01, C02: language, then OTP sign-in with the code from the sandbox SMS inbox.
    await tapAndWait(tester, find.byKey(const Key('lang-en')), find.byKey(const Key('phone')));
    await tester.enterText(find.byKey(const Key('phone')), phone);
    await tapAndWait(tester, find.byKey(const Key('send-code')), find.byKey(const Key('dev-fill')));
    await tester.tap(find.byKey(const Key('dev-fill')));
    final codeField = find.byKey(const Key('code'));
    final end = DateTime.now().add(const Duration(seconds: 10));
    while (tester.widget<TextField>(codeField).controller!.text.length != 6 && DateTime.now().isBefore(end)) {
      await tester.pump(const Duration(milliseconds: 200));
    }
    await tapAndWait(tester, find.byKey(const Key('verify')), find.byKey(const Key('cat-household_cleaning')));
    await acceptNotices(tester, api);
    await pumpUntil(tester, find.byKey(const Key('cat-household_cleaning')).hitTestable());

    // An address inside the synthetic provider's service area (the app's own add-address screen is covered by
    // widget tests; the map picker needs tiles from the network).
    await api.post('/v1/addresses', {
      'label': 'Home', 'address_text': '9 E2E Synthetic Road, Kandy', 'lat': 7.2906, 'lon': 80.6337, 'pin_confirmed': true,
    });

    // C03 to C07: category, address, who it is for, tasks, quote, terms, request.
    await tapAndWait(tester, find.byKey(const Key('cat-household_cleaning')), find.byKey(const Key('addr-Home')));
    await tapAndWait(tester, find.byKey(const Key('addr-Home')), find.byKey(const Key('next-address')));
    await tapAndWait(tester, find.byKey(const Key('next-address')), find.byKey(const Key('next-who')));
    await tapAndWait(tester, find.byKey(const Key('next-who')), find.byKey(const Key('task-dusting')));
    await settle(tester);
    await tester.ensureVisible(find.byKey(const Key('task-dusting')));
    await settle(tester);
    await tester.tap(find.byKey(const Key('task-dusting')));
    await tester.pump();
    await tapAndWait(tester, find.byKey(const Key('next-tasks')), find.text('See price'));
    await tapAndWait(tester, find.text('See price'), find.byKey(const Key('total')));
    expect(find.textContaining('Not an approved tariff'), findsOneWidget);   // unapproved price rule is disclosed
    await settle(tester);
    await tester.ensureVisible(find.byKey(const Key('accept-terms')));
    await settle(tester);
    await tester.tap(find.byKey(const Key('accept-terms')));
    await tester.pump();
    await tapAndWait(tester, find.widgetWithText(FilledButton, 'Request this service'), find.byType(FindingProviderScreen));

    // C08: honest searching state, never "confirmed" before the server has a booking.
    expect(find.textContaining('This is not confirmed yet'), findsOneWidget);
    expect(find.byKey(const Key('open-booking')), findsNothing);

    // This run's request (newest first). Offers carry no request id, so the provider side matches on start time.
    final mine = (await api.get('/v1/requests') as List).first as Map<String, dynamic>;
    String? bookingId;
    try {
      // The real worker dispatches an offer; the real provider accepts it.
      final provider = await signInOverHttp(providerPhone);
      List<Map<String, dynamic>> offers = [];
      final offerDeadline = DateTime.now().add(const Duration(seconds: 30));
      while (offers.isEmpty && DateTime.now().isBefore(offerDeadline)) {
        await tester.pump(const Duration(milliseconds: 500));
        offers = (await provider.get('/v1/offers/me') as List).cast<Map<String, dynamic>>()
            .where((o) => o['start_at'] == mine['start_at'])
            .toList();
      }
      expect(offers, isNotEmpty, reason: 'the worker did not dispatch an offer to the synthetic provider');
      final offer = offers.first;
      expect(offer.containsKey('address'), isFalse);                   // no address before acceptance
      final accepted = await provider.post('/v1/offers/${offer['id']}/accept', null, HelaApi.newIdempotencyKey())
          as Map<String, dynamic>;
      bookingId = accepted['booking_id'] as String;

      // C09: the app picks up the booking on its next poll and shows the check-in code.
      await pumpUntil(tester, find.byKey(const Key('open-booking')));
      await tapAndWait(tester, find.byKey(const Key('open-booking')), find.byKey(const Key('pin')));
      final pin = tester.widget<Text>(find.byKey(const Key('pin'))).data!;
      expect(RegExp(r'^\d{4}$').hasMatch(pin), isTrue);
      final view = await api.get('/v1/bookings/$bookingId') as Map<String, dynamic>;
      expect(view['checkin_pin'], pin);                                 // the app shows what the server issued
    } finally {
      // Clean up even after a failure, so the next run finds the provider free: the customer cancels.
      // Clean-up errors are printed, never thrown: they must not hide the failure that got us here.
      try {
        if (bookingId != null) {
          final view = await api.get('/v1/bookings/$bookingId') as Map<String, dynamic>;
          await api.post('/v1/bookings/$bookingId/transitions', {
            'target_status': 'cancelled', 'expected_version': view['version'], 'reason': 'End-to-end test clean-up',
          }, HelaApi.newIdempotencyKey());
        } else {
          await api.post('/v1/requests/${mine['request_id']}/cancel', {'reason': 'End-to-end test clean-up'});
        }
      } catch (e) {
        debugPrint('E2E clean-up failed: $e');
      }
    }
  }, timeout: const Timeout(Duration(minutes: 4)));
}
