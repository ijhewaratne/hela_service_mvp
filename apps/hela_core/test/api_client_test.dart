import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:hela_core/hela_core.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

http.Response json(int status, Object body) =>
    http.Response(jsonEncode(body), status, headers: {'content-type': 'application/json'});

Map<String, dynamic> envelope(String code, [Map<String, dynamic> details = const {}]) => {
      'error': {'code': code, 'message_key': 'k', 'message': code, 'correlation_id': 'c_t', 'retryable': false, 'details': details}
    };

void main() {
  test('verifyOtp keeps access token in memory and only the refresh token in the store', () async {
    final store = MemoryTokenStore();
    final api = HelaApi(store: store, baseUrl: 'http://api', client: MockClient((req) async {
      return json(200, {'access_token': 'A1', 'refresh_token': 'R1', 'session_id': 's'});
    }));
    await api.verifyOtp('+94770000001', 'cid', '123456');
    expect(api.hasSession, isTrue);
    expect(store.token, 'R1');
  });

  test('refreshes once on TOKEN_INVALID, shared by concurrent calls, then retries', () async {
    var refreshes = 0;
    final store = MemoryTokenStore()..token = 'R1';
    final api = HelaApi(store: store, baseUrl: 'http://api', client: MockClient((req) async {
      if (req.url.path == '/v1/auth/refresh') {
        refreshes++;
        expect(jsonDecode(req.body)['refresh_token'], 'R1');
        return json(200, {'access_token': 'A2', 'refresh_token': 'R2', 'session_id': 's'});
      }
      return req.headers['Authorization'] == 'Bearer A2' ? json(200, {'ok': true}) : json(401, envelope('TOKEN_INVALID'));
    }));
    final results = await Future.wait([api.get('/a'), api.get('/b'), api.get('/c')]);
    expect(results, everyElement({'ok': true}));
    expect(refreshes, 1); // a rotated token is never replayed (server would revoke the session)
    expect(store.token, 'R2');
  });

  test('a wrong code is a plain error: no refresh, no sign-out', () async {
    var calls = 0;
    var signedOut = false;
    final api = HelaApi(store: MemoryTokenStore()..token = 'R1', baseUrl: 'http://api', client: MockClient((req) async {
      calls++;
      return json(400, envelope('OTP_INVALID'));
    }))..onSignedOut = () => signedOut = true;
    await expectLater(api.verifyOtp('+9477', 'c', '000000'), throwsA(isA<ApiException>().having((e) => e.code, 'code', 'OTP_INVALID')));
    expect(calls, 1);
    expect(signedOut, isFalse);
  });

  test('revoked session clears the stored refresh token', () async {
    final store = MemoryTokenStore();
    final api = HelaApi(store: store, baseUrl: 'http://api', client: MockClient((req) async {
      if (req.url.path == '/v1/auth/otp/verify') return json(200, {'access_token': 'A', 'refresh_token': 'R', 'session_id': 's'});
      return json(401, envelope('SESSION_REVOKED'));
    }));
    await api.verifyOtp('+9477', 'c', '123456');
    await expectLater(api.get('/v1/me'), throwsA(isA<ApiException>()));
    expect(store.token, isNull);
    expect(api.hasSession, isFalse);
  });

  test('network failure is OFFLINE and never looks like success', () async {
    final api = HelaApi(store: MemoryTokenStore(), baseUrl: 'http://api', client: MockClient((req) async {
      throw http.ClientException('no route');
    }));
    await expectLater(api.post('/v1/requests', {}, 'k'), throwsA(isA<ApiException>().having((e) => e.code, 'code', 'OFFLINE')));
  });

  test('idempotency key is sent as given, so a retry after a lost response reuses it', () async {
    final keys = <String?>[];
    final api = HelaApi(store: MemoryTokenStore(), baseUrl: 'http://api', client: MockClient((req) async {
      keys.add(req.headers['Idempotency-Key']);
      return json(201, {'request_id': 'r1'});
    }));
    final key = HelaApi.newIdempotencyKey();
    await api.post('/v1/requests', {'x': 1}, key);
    await api.post('/v1/requests', {'x': 1}, key);
    expect(keys, [key, key]);
    expect(key, matches(RegExp(r'^app-[0-9a-f]{32}$')));
  });

  test('reason codes from the envelope are exposed for display', () async {
    final api = HelaApi(store: MemoryTokenStore(), baseUrl: 'http://api', client: MockClient((req) async {
      return json(409, envelope('PROVIDER_INELIGIBLE', {'reason_codes': ['EVIDENCE_EXPIRED']}));
    }));
    try {
      await api.post('/v1/offers/x/accept', null, 'k');
      fail('expected error');
    } on ApiException catch (e) {
      expect(e.reasonCodes, ['EVIDENCE_EXPIRED']);
      expect(e.correlationId, 'c_t');
    }
  });

  test('money and time formatting', () {
    expect(lkr(240000), 'LKR 2,400.00');
    expect(lkr(123456789), 'LKR 1,234,567.89');
    expect(formatLocal('2026-11-07T04:30:00Z'), '2026-11-07 10:00');               // UTC+05:30
    expect(colomboToUtcIso(DateTime(2026, 11, 7, 10, 0)), '2026-11-07T04:30:00.000Z');
  });

  test('si and ta are flagged as pending review and fall back to English', () {
    expect(Strings('si').translationPending, isTrue);
    expect(Strings('en').translationPending, isFalse);
    expect(Strings('ta').t('common.continue'), 'Continue');
    expect(Strings.languageNames['si'], 'සිංහල');
  });
}
