import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hela_core/hela_core.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

void main() {
  testWidgets('My data: export shows own data; a request is filed and listed; recent sign-in error is explained', (tester) async {
    final filed = <Map<String, dynamic>>[];
    var exportCalls = 0;
    final api = HelaApi(store: MemoryTokenStore(), baseUrl: 'http://api', client: MockClient((req) async {
      final key = '${req.method} ${req.url.path}';
      http.Response j(Object b, [int s = 200]) => http.Response(jsonEncode(b), s, headers: {'content-type': 'application/json'});
      switch (key) {
        case 'GET /v1/me/export':
          exportCalls++;
          if (exportCalls == 1) {
            return j({'error': {'code': 'RECENT_SIGN_IN_REQUIRED', 'message_key': 'privacy.recent_sign_in',
              'message': 'For your security, sign in again before downloading your data.', 'correlation_id': 'c1',
              'retryable': false, 'details': {}}}, 403);
          }
          return j({'account': {'phone': '+94770000001'}, 'family_members': [{'phone_masked': '*******001'}]});
        case 'POST /v1/me/privacy-requests':
          filed.add(jsonDecode(req.body) as Map<String, dynamic>);
          return j({'id': 'p1', 'status': 'received'}, 201);
        case 'GET /v1/me/privacy-requests':
          return j([for (final f in filed) {'kind': f['kind'], 'status': 'received', 'created_at': '2026-11-07T04:30:00Z'}]);
      }
      return j({}, 404);
    }));
    tester.view.physicalSize = const Size(1080, 2600);
    tester.view.devicePixelRatio = 2.5;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(MaterialApp(home: MyDataScreen(api: api)));
    await tester.pumpAndSettle();

    await tester.tap(find.text('Download my data'));
    await tester.pumpAndSettle();
    expect(find.textContaining('sign in again'), findsOneWidget);       // honest, actionable error
    await tester.tap(find.text('Download my data'));
    await tester.pumpAndSettle();
    expect(find.byKey(const Key('export-json')), findsOneWidget);
    expect(find.textContaining('*******001'), findsOneWidget);

    await tester.enterText(find.byType(TextField).last, 'Please fix my display name');
    await tester.tap(find.text('Send request'));
    await tester.pumpAndSettle();
    expect(filed.single, {'kind': 'correction', 'details': 'Please fix my display name'});
    expect(find.text('correction'), findsOneWidget);
  });
}
