import 'dart:async';

import 'package:flutter/material.dart';
import 'package:hela_core/hela_core.dart';

import 'jobs.dart';
import 'profile.dart';

void main() => runApp(ProviderApp(api: HelaApi(store: SecureTokenStore()), languagePref: LanguagePref()));

/// Hela Service provider app (P01–P10). Synthetic development build only.
class ProviderApp extends StatefulWidget {
  const ProviderApp({super.key, required this.api, this.languagePref, this.showMaps = true, this.offerPoll = const Duration(seconds: 5)});
  final HelaApi api;
  final LanguagePref? languagePref;
  final bool showMaps;
  final Duration offerPoll;

  @override
  State<ProviderApp> createState() => _ProviderAppState();
}

enum _Stage { loading, language, signIn, home }

class _ProviderAppState extends State<ProviderApp> {
  _Stage _stage = _Stage.loading;
  String _language = 'en';
  final _navigator = GlobalKey<NavigatorState>();

  @override
  void initState() {
    super.initState();
    widget.api.onSignedOut = () => setState(() => _stage = _Stage.signIn);
    _start();
  }

  Future<void> _start() async {
    final saved = await widget.languagePref?.read();
    final resumed = await widget.api.resume();
    setState(() {
      _language = saved ?? 'en';
      _stage = saved == null ? _Stage.language : resumed ? _Stage.home : _Stage.signIn;
    });
  }

  Future<void> _setLanguage(String lang) async {
    await widget.languagePref?.write(lang);
    setState(() => _language = lang);
    if (widget.api.hasSession) {
      try {
        await widget.api.patch('/v1/me', {'preferred_language': lang});
      } catch (_) {}
    }
  }

  @override
  Widget build(BuildContext context) {
    return LanguageScope(
      strings: Strings(_language),
      setLanguage: _setLanguage,
      child: MaterialApp(
        navigatorKey: _navigator,
        title: 'Hela Provider',
        theme: ThemeData(colorSchemeSeed: const Color(0xFF1F4E79), useMaterial3: true),
        darkTheme: ThemeData(colorSchemeSeed: const Color(0xFF1F4E79), brightness: Brightness.dark, useMaterial3: true),
        home: switch (_stage) {
          _Stage.loading => const Scaffold(body: Center(child: CircularProgressIndicator())),
          _Stage.language => LanguageScreen(
              title: 'Hela Provider',
              onChosen: (l) async {
                await _setLanguage(l);
                setState(() => _stage = _Stage.signIn);
              }),
          _Stage.signIn => PhoneSignInScreen(
              title: 'Hela Provider',
              api: widget.api,
              onSignedIn: () async {
                setState(() => _stage = _Stage.home);
                await _setLanguage(_language);
                final ctx = _navigator.currentContext;
                if (ctx != null && ctx.mounted) await ensureNoticesAccepted(ctx, widget.api, _language, 'provider_signup');
              }),
          _Stage.home => ProviderHome(api: widget.api, showMaps: widget.showMaps, offerPoll: widget.offerPoll),
        },
      ),
    );
  }
}

/// Decides between onboarding (no provider profile yet) and the working app.
class ProviderHome extends StatefulWidget {
  const ProviderHome({super.key, required this.api, required this.showMaps, required this.offerPoll});
  final HelaApi api;
  final bool showMaps;
  final Duration offerPoll;

  @override
  State<ProviderHome> createState() => _ProviderHomeState();
}

class _ProviderHomeState extends State<ProviderHome> {
  int _tab = 0;
  Map<String, dynamic>? _profile;
  bool _checked = false;
  Object? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final p = await widget.api.get('/v1/provider/me') as Map<String, dynamic>;
      setState(() {
        _profile = p;
        _checked = true;
      });
    } on ApiException catch (e) {
      setState(() {
        _checked = true;
        _profile = null;
        _error = e.code == 'PROVIDER_NOT_FOUND' ? null : e;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    if (!_checked) return const Scaffold(body: Center(child: CircularProgressIndicator()));
    if (_profile == null) {
      return Scaffold(body: SafeArea(child: Column(children: [ErrorBanner(_error), Expanded(child: ApplyScreen(api: widget.api, onApplied: _load))])));
    }
    final pages = [
      OffersScreen(api: widget.api, pollEvery: widget.offerPoll),
      JobsList(api: widget.api),
      ProfileScreen(api: widget.api, showMaps: widget.showMaps, onChanged: _load),
    ];
    return Scaffold(
      body: SafeArea(child: pages[_tab]),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: (i) => setState(() => _tab = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.notifications_active_outlined), label: 'Offers'),
          NavigationDestination(icon: Icon(Icons.work_outline), label: 'My jobs'),
          NavigationDestination(icon: Icon(Icons.badge_outlined), label: 'Profile'),
        ],
      ),
    );
  }
}

/// P05: time-limited offers with a countdown. Only area, distance band, time, tasks and price are shown before
/// accepting; the address appears only after the server confirms the assignment (FR-LOC-03).
class OffersScreen extends StatefulWidget {
  const OffersScreen({super.key, required this.api, required this.pollEvery});
  final HelaApi api;
  final Duration pollEvery;

  @override
  State<OffersScreen> createState() => _OffersScreenState();
}

class _OffersScreenState extends State<OffersScreen> {
  List<Map<String, dynamic>> _offers = [];
  Object? _error;
  Timer? _poll;
  Timer? _tick;
  DateTime _now = DateTime.now();
  // One idempotency key per offer: a retry after a dropped connection returns the same booking (AT-020).
  final _keys = <String, String>{};

  @override
  void initState() {
    super.initState();
    _load();
    _poll = Timer.periodic(widget.pollEvery, (_) => _load());
    _tick = Timer.periodic(const Duration(seconds: 1), (_) => setState(() => _now = DateTime.now()));
  }

  @override
  void dispose() {
    _poll?.cancel();
    _tick?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final list = (await widget.api.get('/v1/offers/me') as List).cast<Map<String, dynamic>>();
      if (mounted) {
        setState(() {
          _offers = list;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = e);
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListView(padding: const EdgeInsets.all(16), children: [
      Row(children: [
        Text('Job offers', style: Theme.of(context).textTheme.headlineSmall),
        const Spacer(),
        IconButton(tooltip: 'Refresh', onPressed: _load, icon: const Icon(Icons.refresh)),
      ]),
      const AppNotices(),
      ErrorBanner(_error),
      if (_offers.isEmpty) const Padding(padding: EdgeInsets.all(24), child: Text('No offers right now.')),
      for (final o in _offers)
        Builder(builder: (context) {
          final left = DateTime.parse(o['expires_at'] as String).difference(_now.toUtc());
          final expired = left.isNegative;
          return Card(
            key: Key('offer-${o['id']}'),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                Row(children: [
                  Expanded(child: Text((o['category_code'] as String).replaceAll('_', ' '), style: Theme.of(context).textTheme.titleMedium)),
                  Semantics(
                    label: 'time left to respond',
                    child: Text(expired ? 'expired' : '${left.inMinutes}:${two(left.inSeconds % 60)}',
                        key: const Key('countdown'), style: TextStyle(color: expired ? Theme.of(context).colorScheme.error : null)),
                  ),
                ]),
                Text('${formatLocal(o['start_at'] as String)} – ${formatLocal(o['end_at'] as String)}'),
                Text('${o['area_label']} · ${o['distance_band']} (approximate straight-line)'),
                Text('Tasks: ${(o['task_codes'] as List).join(', ').replaceAll('_', ' ')}'),
                Text('Price: ${lkr(o['total_minor'] as num)}, paid to you directly by the customer'),
                const SizedBox(height: 8),
                ActionButton(
                  label: 'Accept',
                  enabled: !expired,
                  onPressed: () async {
                    final id = o['id'] as String;
                    final r = await widget.api.post('/v1/offers/$id/accept', null, _keys.putIfAbsent(id, HelaApi.newIdempotencyKey))
                        as Map<String, dynamic>;
                    if (!context.mounted) return;
                    await Navigator.of(context).push(MaterialPageRoute(builder: (_) => JobScreen(api: widget.api, bookingId: r['booking_id'] as String)));
                    _load();
                  },
                ),
                TextButton(
                  onPressed: expired ? null : () async {
                    await widget.api.post('/v1/offers/${o['id']}/decline');
                    _load();
                  },
                  child: const Text('Decline'),
                ),
              ]),
            ),
          );
        }),
    ]);
  }
}
