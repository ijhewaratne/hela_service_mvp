import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hela_core/hela_core.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:latlong2/latlong.dart';

/// Spec §16: sufficient touch targets, contrast, screen-reader labels and scalable text, checked on a small
/// phone (360 x 640 dp) at normal and 200% text size. These are automated checks; they do not replace testing
/// with TalkBack on a real device.
HelaApi fakeApi() => HelaApi(store: MemoryTokenStore(), baseUrl: 'http://api', client: MockClient((req) async {
      final body = switch ('${req.method} ${req.url.path}') {
        'GET /v1/me/privacy-requests' => [
            {'kind': 'correction', 'status': 'completed_pending_confirmation', 'created_at': '2026-11-07T04:30:00Z'}
          ],
        _ => <String, dynamic>{},
      };
      return http.Response(jsonEncode(body), 200, headers: {'content-type': 'application/json'});
    }));

Future<void> pumpAt(WidgetTester tester, Widget screen, double textScale) async {
  tester.view.physicalSize = const Size(360 * 3, 640 * 3);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(MaterialApp(
    theme: ThemeData(colorSchemeSeed: Colors.teal, useMaterial3: true),
    builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context).copyWith(textScaler: TextScaler.linear(textScale)), child: child!),
    home: screen,
  ));
  await tester.pumpAndSettle();
}

Future<void> expectAccessible(WidgetTester tester) async {
  expect(tester.takeException(), isNull, reason: 'layout overflowed or threw');
  await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
  await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
  await expectLater(tester, meetsGuideline(textContrastGuideline));
}

void main() {
  final screens = <String, Widget Function()>{
    'language choice': () => LanguageScreen(title: 'Hela Service', onChosen: (_) {}),
    'sign in': () => PhoneSignInScreen(api: fakeApi(), title: 'Sign in', onSignedIn: () {}),
    'report a problem': () => ReportProblemScreen(api: fakeApi()),
    'my data': () => MyDataScreen(api: fakeApi()),
  };
  for (final scale in [1.0, 2.0]) {
    for (final entry in screens.entries) {
      testWidgets('${entry.key} is accessible at ${(scale * 100).round()}% text', (tester) async {
        final handle = tester.ensureSemantics();
        await pumpAt(tester, entry.value(), scale);
        await expectAccessible(tester);
        handle.dispose();
      });
    }
  }

  testWidgets('screen titles are headings and status chips are spelled out', (tester) async {
    final handle = tester.ensureSemantics();
    await pumpAt(tester, const Scaffold(body: Column(children: [ScreenTitle('My jobs'), StatusChip('incident_hold')])), 1);
    expect(tester.getSemantics(find.text('My jobs')), matchesSemantics(label: 'My jobs', isHeader: true));
    expect(find.bySemanticsLabel('Status: incident hold'), findsOneWidget);
    handle.dispose();
  });

  testWidgets('a busy button keeps its name for screen readers', (tester) async {
    final handle = tester.ensureSemantics();
    await pumpAt(tester, Scaffold(body: ActionButton(label: 'Accept', onPressed: () => Future.delayed(const Duration(seconds: 1)))), 1);
    await tester.tap(find.text('Accept'));
    await tester.pump();
    expect(find.bySemanticsLabel(RegExp('Accept, in progress')), findsOneWidget);
    await tester.pump(const Duration(seconds: 1));
    expect(find.text('Accept'), findsOneWidget);
    handle.dispose();
  });

  testWidgets('typing coordinates is not interrupted by the field rewriting itself', (tester) async {
    LatLng? last;
    await pumpAt(tester, Scaffold(body: MapPinPicker(showMap: false, onChanged: (p) => last = p)), 1);
    await tester.enterText(find.byKey(const Key('pin-lat')), '7.2');
    await tester.pump();
    expect(tester.widget<TextField>(find.byKey(const Key('pin-lat'))).controller!.text, '7.2');
    expect(last!.latitude, 7.2);
  });
}
