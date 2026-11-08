// End-to-end: the real provider app against the real local API and worker (no fakes). Synthetic data only.
//
//   scripts/app_e2e.sh provider   (needs the dev API on 127.0.0.1:8000 and the worker running; starts neither)
//
// The customer side is driven over HTTP with a second client; apps/customer has the mirror-image test.
import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hela_core/hela_core.dart';
import 'package:hela_provider/main.dart';
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

Finder button(String label) => find.widgetWithText(FilledButton, label).hitTestable();

Future<void> acceptNotices(WidgetTester tester, HelaApi api) async {
  final count = (await api.get('/v1/notices/current?language=en') as List).length;
  final accept = find.widgetWithText(FilledButton, 'I have read and accept');
  for (var i = 0; i < count; i++) {
    await pumpUntil(tester, accept);
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

  testWidgets('provider accepts a real offer in the app, checks in with the PIN and finishes the job', (tester) async {
    expect(HelaConfig.devInboxToken, isNotEmpty, reason: 'run with --dart-define HELA_DEV_INBOX_TOKEN=...');

    // A fresh synthetic customer asks for cleaning three days ahead at 14:00 Colombo time, over HTTP.
    final customer = await signInOverHttp('+947791${(Random().nextInt(900000) + 100000)}');
    for (final n in (await customer.get('/v1/notices/current?language=en') as List).cast<Map<String, dynamic>>()) {
      await customer.post('/v1/notices/${n['id']}/accept', {'processing_context': 'customer_signup'});
    }
    final address = await customer.post('/v1/addresses', {
      'label': 'Home', 'address_text': '11 E2E Synthetic Lane, Kandy', 'lat': 7.2906, 'lon': 80.6337, 'pin_confirmed': true,
    }) as Map<String, dynamic>;
    final day = DateTime.now().add(const Duration(days: 3));
    final quote = await customer.post('/v1/quotes/preview', {
      'category_code': 'household_cleaning',
      'address_id': address['id'],
      'start_at': colomboToUtcIso(DateTime(day.year, day.month, day.day, 14)),
      'duration_minutes': 120,
    }) as Map<String, dynamic>;
    await customer.post('/v1/quotes/${quote['quote_id']}/accept', {'policy_version': quote['policy_version']});
    final request = await customer.post('/v1/requests',
        {'quote_id': quote['quote_id'], 'task_codes': ['dusting', 'tidying']}, HelaApi.newIdempotencyKey())
        as Map<String, dynamic>;

    Future<String?> bookingOf() async => ((await customer.get('/v1/requests') as List).cast<Map<String, dynamic>>()
        .firstWhere((r) => r['request_id'] == request['request_id']))['booking_id'] as String?;
    try {
      // P01–P04: the provider signs in to the real app with the code from the sandbox SMS inbox.
      final api = HelaApi(store: MemoryTokenStore());
      await tester.pumpWidget(ProviderApp(api: api, showMaps: false, offerPoll: const Duration(seconds: 2)));
      await pumpUntil(tester, find.byKey(const Key('lang-en')));
      await tapAndWait(tester, find.byKey(const Key('lang-en')), find.byKey(const Key('phone')));
      await tester.enterText(find.byKey(const Key('phone')), providerPhone);
      await tapAndWait(tester, find.byKey(const Key('send-code')), find.byKey(const Key('dev-fill')));
      await tester.tap(find.byKey(const Key('dev-fill')));
      final codeField = find.byKey(const Key('code'));
      final end = DateTime.now().add(const Duration(seconds: 10));
      while (tester.widget<TextField>(codeField).controller!.text.length != 6 && DateTime.now().isBefore(end)) {
        await tester.pump(const Duration(milliseconds: 200));
      }
      await tapAndWait(tester, find.byKey(const Key('verify')), find.text('Job offers'));
      await acceptNotices(tester, api);

      // P05: the worker's offer appears with a countdown and without the address (FR-LOC-03).
      final ours = (await customer.get('/v1/requests') as List).cast<Map<String, dynamic>>()
          .firstWhere((r) => r['request_id'] == request['request_id']);
      final window = '${formatLocal(ours['start_at'] as String)} – ${formatLocal(ours['end_at'] as String)}';
      final offerCard = find.ancestor(
          of: find.text(window),
          matching: find.byWidgetPredicate((w) => w.key is ValueKey<String> &&
              (w.key! as ValueKey<String>).value.startsWith('offer-')));
      await pumpUntil(tester, offerCard, timeout: const Duration(seconds: 45));
      expect(find.descendant(of: offerCard.first, matching: find.byKey(const Key('countdown'))), findsOneWidget);
      expect(find.textContaining('E2E Synthetic Lane'), findsNothing);

      // P06: accept; the job screen shows the address only now.
      final accept = find.descendant(of: offerCard.first, matching: find.widgetWithText(FilledButton, 'Accept'));
      await tapAndWait(tester, accept, find.byKey(const Key('address')));
      expect(find.textContaining('11 E2E Synthetic Lane'), findsOneWidget);

      // P07, P08: on the way, arrived, then the check-in code the customer reads out from their app.
      await tapAndWait(tester, button('I am on my way'), button('I have arrived'));
      await tapAndWait(tester, button('I have arrived'), find.byKey(const Key('checkin-pin')));
      final bookingId = await bookingOf();
      final view = await customer.get('/v1/bookings/$bookingId') as Map<String, dynamic>;
      expect(view['status'], 'arrived');
      await tester.enterText(find.byKey(const Key('checkin-pin')), view['checkin_pin'] as String);
      await tapAndWait(tester, button('Start work'), button('Work is finished'));

      // P09: finished; the customer confirms over HTTP, which also leaves the provider free for the next run.
      await tapAndWait(tester, button('Work is finished'), find.text('completed pending confirmation'));
      final done = await customer.get('/v1/bookings/$bookingId') as Map<String, dynamic>;
      expect(done['status'], 'completed_pending_confirmation');
    } finally {
      // Clean up even after a failure, so the next run finds the provider free.
      // Clean-up errors are printed, never thrown: they must not hide the failure that got us here.
      try {
        final id = await bookingOf();
        if (id == null) {
          await customer.post('/v1/requests/${request['request_id']}/cancel', {'reason': 'End-to-end test clean-up'});
        } else {
          final b = await customer.get('/v1/bookings/$id') as Map<String, dynamic>;
          final target = switch (b['status']) {
            'completed_pending_confirmation' => 'completed',     // the customer confirms the finished job
            'accepted' || 'en_route' || 'arrived' => 'cancelled',
            _ => null,
          };
          if (target != null) {
            await customer.post('/v1/bookings/$id/transitions', {
              'target_status': target, 'expected_version': b['version'], 'reason': 'End-to-end test clean-up',
            }, HelaApi.newIdempotencyKey());
          }
        }
      } catch (e) {
        debugPrint('E2E clean-up failed: $e');
      }
    }
  }, timeout: const Timeout(Duration(minutes: 4)));
}
