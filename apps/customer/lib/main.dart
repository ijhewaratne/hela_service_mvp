import 'package:flutter/material.dart';
import 'package:hela_core/hela_core.dart';

import 'account.dart';
import 'booking_flow.dart';
import 'bookings.dart';

void main() => runApp(CustomerApp(api: HelaApi(store: SecureTokenStore()), languagePref: LanguagePref()));

/// Hela Service customer app (C01–C13). Synthetic development build only.
class CustomerApp extends StatefulWidget {
  const CustomerApp({super.key, required this.api, this.languagePref, this.showMaps = true});
  final HelaApi api;
  final LanguagePref? languagePref;
  final bool showMaps;

  @override
  State<CustomerApp> createState() => _CustomerAppState();
}

enum _Stage { loading, language, signIn, recovery, home }

class _CustomerAppState extends State<CustomerApp> {
  _Stage _stage = _Stage.loading;
  String _language = 'en';
  final _navigator = GlobalKey<NavigatorState>();

  @override
  void initState() {
    super.initState();
    widget.api.onSignedOut = () => setState(() => _stage = _Stage.signIn);
    _start();
  }

  _Stage _stageForSession(SessionRestoreStatus result) => switch (result) {
        SessionRestoreStatus.authenticated => _Stage.home,
        SessionRestoreStatus.needsSignIn => _Stage.signIn,
        SessionRestoreStatus.temporarilyUnavailable => _Stage.recovery,
      };

  Future<void> _start() async {
    final saved = await widget.languagePref?.read();
    final result = await widget.api.resumeStatus();
    if (!mounted) return;
    setState(() {
      _language = saved ?? 'en';
      _stage = saved == null ? _Stage.language : _stageForSession(result);
    });
  }

  Future<void> _setLanguage(String lang) async {
    await widget.languagePref?.write(lang);
    setState(() => _language = lang);
    if (widget.api.hasSession) {
      try {
        await widget.api.patch('/v1/me', {'preferred_language': lang});
      } catch (_) {/* preference sync is best effort */}
    }
  }

  @override
  Widget build(BuildContext context) {
    return LanguageScope(
      strings: Strings(_language),
      setLanguage: _setLanguage,
      child: MaterialApp(
        navigatorKey: _navigator,
        title: 'Hela Service',
        theme: ThemeData(colorSchemeSeed: const Color(0xFF0B6E4F), useMaterial3: true),
        darkTheme: ThemeData(colorSchemeSeed: const Color(0xFF0B6E4F), brightness: Brightness.dark, useMaterial3: true),
        home: switch (_stage) {
          _Stage.loading => const Scaffold(body: Center(child: CircularProgressIndicator())),
          _Stage.language => LanguageScreen(
              title: 'Hela Service',
              onChosen: (l) async {
                await _setLanguage(l);
                final result = await widget.api.resumeStatus();
                if (mounted) setState(() => _stage = _stageForSession(result));
              }),
          _Stage.recovery => SessionRecoveryScreen(
              onRetry: () {
                setState(() => _stage = _Stage.loading);
                _start();
              },
              onChooseAccount: () async {
                await widget.api.forgetSession();   // ends it on the server too, now or once back online
                if (mounted) setState(() => _stage = _Stage.signIn);
              }),
          _Stage.signIn => PhoneSignInScreen(
              title: 'Hela Service',
              api: widget.api,
              onSignedIn: () async {
                setState(() => _stage = _Stage.home);
                await _setLanguage(_language);
                final ctx = _navigator.currentContext;
                if (ctx != null && ctx.mounted) await ensureNoticesAccepted(ctx, widget.api, _language, 'customer_signup');
              }),
          _Stage.home => HomeScreen(api: widget.api, showMaps: widget.showMaps),
        },
      ),
    );
  }
}

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.api, required this.showMaps});
  final HelaApi api;
  final bool showMaps;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int _tab = 0;

  @override
  Widget build(BuildContext context) {
    final pages = [
      ServicesHome(api: widget.api, showMaps: widget.showMaps),
      BookingsList(api: widget.api),
      AccountScreen(api: widget.api, showMaps: widget.showMaps),
    ];
    return Scaffold(
      body: SafeArea(child: pages[_tab]),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: (i) => setState(() => _tab = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.cleaning_services_outlined), label: 'Book'),
          NavigationDestination(icon: Icon(Icons.event_note_outlined), label: 'My bookings'),
          NavigationDestination(icon: Icon(Icons.person_outline), label: 'Account'),
        ],
      ),
    );
  }
}

/// C03: only enabled categories with an active policy are listed; the API enforces the same rule (FR-CAT-01).
class ServicesHome extends StatelessWidget {
  const ServicesHome({super.key, required this.api, required this.showMaps});
  final HelaApi api;
  final bool showMaps;

  @override
  Widget build(BuildContext context) {
    return ListView(padding: const EdgeInsets.all(16), children: [
      ScreenTitle('Book a service'),
      const AppNotices(),
      AsyncSection<List<dynamic>>(
        load: () async => [await api.get('/v1/regions'), await api.get('/v1/categories')],
        builder: (context, data, _) {
          final regions = (data[0] as List).cast<Map<String, dynamic>>();
          final cats = (data[1] as List).cast<Map<String, dynamic>>();
          return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text('Serving: ${regions.map((r) => r['name']).join(', ')}', style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 8),
            for (final c in cats)
              Card(
                child: ListTile(
                  key: Key('cat-${c['code']}'),
                  title: Text(c['display_name'] as String),
                  subtitle: Text('${c['min_duration_minutes']}–${c['max_duration_minutes']} minutes'
                      '${c['approval_status'] == 'UNAPPROVED' ? ' · terms not yet approved' : ''}'),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => BookingFlow(api: api, category: c, showMaps: showMaps))),
                ),
              ),
          ]);
        },
      ),
    ]);
  }
}
