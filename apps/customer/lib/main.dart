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
      ServicesHome(api: widget.api, showMaps: widget.showMaps, onViewBookings: () => setState(() => _tab = 1)),
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

/// C03: Discover services that the API currently allows to be requested.
/// Category cards are server-driven: no invented price, provider availability or ETA.
class ServicesHome extends StatefulWidget {
  const ServicesHome({super.key, required this.api, required this.showMaps, this.onViewBookings});
  final HelaApi api;
  final bool showMaps;
  final VoidCallback? onViewBookings;

  @override
  State<ServicesHome> createState() => _ServicesHomeState();
}

class _ServicesHomeState extends State<ServicesHome> {
  String _search = '';

  static IconData _iconFor(String code) {
    return switch (code) {
      'household_cleaning' => Icons.cleaning_services_outlined,
      'laundry_kitchen' => Icons.local_laundry_service_outlined,
      'errands' => Icons.shopping_basket_outlined,
      _ => Icons.handyman_outlined,
    };
  }

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final texts = Theme.of(context).textTheme;
    return ListView(
      key: const Key('customer-home-scroll'),
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
      children: [
        Row(children: [
          Expanded(child: ScreenTitle('Hela Service')),
          IconButton(
            tooltip: 'Report a problem',
            icon: const Icon(Icons.support_agent_outlined),
            onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                builder: (_) => ReportProblemScreen(api: widget.api))),
          ),
        ]),
        const AppNotices(),
        Container(
          padding: const EdgeInsets.all(20),
          decoration: BoxDecoration(
            color: colors.primaryContainer,
            borderRadius: BorderRadius.circular(20),
          ),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('Everyday help for your household', style: texts.headlineSmall?.copyWith(
                color: colors.onPrimaryContainer, fontWeight: FontWeight.w700)),
            const SizedBox(height: 8),
            Text('Choose a service, set the tasks and time, then request an eligible local provider.',
                style: texts.bodyMedium?.copyWith(color: colors.onPrimaryContainer)),
          ]),
        ),
        const SizedBox(height: 20),
        Text('What do you need help with?', style: texts.titleLarge),
        const SizedBox(height: 12),
        TextField(
          key: const Key('service-search'),
          onChanged: (value) => setState(() => _search = value.trim().toLowerCase()),
          decoration: const InputDecoration(
            hintText: 'Search available services',
            prefixIcon: Icon(Icons.search),
            border: OutlineInputBorder(),
          ),
          textInputAction: TextInputAction.search,
        ),
        const SizedBox(height: 12),
        AsyncSection<List<dynamic>>(
          load: () async => [
            await widget.api.get('/v1/regions'),
            await widget.api.get('/v1/categories'),
          ],
          builder: (context, data, reload) {
            final regions = (data[0] as List).cast<Map<String, dynamic>>();
            final categories = (data[1] as List).cast<Map<String, dynamic>>();
            // Defense-in-depth in the presentation layer, not a substitute for the server's hard gates.
            final enabled = categories.where((c) => c['enabled'] != false).toList();
            final matches = enabled.where((c) {
              if (_search.isEmpty) return true;
              final name = (c['display_name'] ?? '').toString().toLowerCase();
              final code = (c['code'] ?? '').toString().replaceAll('_', ' ').toLowerCase();
              final tasks = ((c['allowed_tasks'] as List?) ?? const [])
                  .map((t) => t.toString().replaceAll('_', ' ').toLowerCase()).join(' ');
              return name.contains(_search) || code.contains(_search) || tasks.contains(_search);
            }).toList();
            return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Row(children: [
                    Icon(Icons.location_on_outlined, color: colors.primary),
                    const SizedBox(width: 12),
                    Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text('Supported service areas', style: texts.labelLarge),
                      Text(regions.isEmpty
                          ? 'No service areas are currently available.'
                          : regions.map((r) => r['name'].toString()).join(' · '),
                          style: texts.bodySmall),
                    ])),
                  ]),
                ),
              ),
              const SizedBox(height: 12),
              if (enabled.isEmpty)
                const InfoBanner('There are no enabled service categories at this time. Please check back later.')
              else if (matches.isEmpty)
                const InfoBanner('No services match your search. Try a different word.')
              else
                for (final c in matches)
                  Card(
                    clipBehavior: Clip.antiAlias,
                    margin: const EdgeInsets.only(bottom: 10),
                    child: ListTile(
                      key: Key('cat-${c['code']}'),
                      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                      leading: CircleAvatar(
                        backgroundColor: colors.primaryContainer,
                        foregroundColor: colors.onPrimaryContainer,
                        child: Icon(_iconFor(c['code'] as String)),
                      ),
                      title: Text(c['display_name'] as String, style: texts.titleMedium),
                      subtitle: Text(
                        '${c['min_duration_minutes']}–${c['max_duration_minutes']} minutes'
                        '${c['approval_status'] == 'UNAPPROVED' ? ' · terms not yet approved' : ''}',
                        softWrap: true,
                      ),
                      trailing: const Icon(Icons.chevron_right),
                      onTap: () => Navigator.of(context).push(MaterialPageRoute(
                          builder: (_) => BookingFlow(
                            api: widget.api, category: c, showMaps: widget.showMaps))),
                    ),
                  ),
              TextButton.icon(
                onPressed: reload,
                icon: const Icon(Icons.refresh),
                label: const Text('Refresh services'),
              ),
            ]);
          },
        ),
        const SizedBox(height: 12),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('Booking for a family member?', style: texts.titleMedium),
              const SizedBox(height: 8),
              const Text('You can request help for someone else. They must approve before any provider is contacted.'),
              const SizedBox(height: 8),
              TextButton.icon(
                onPressed: widget.onViewBookings,
                icon: const Icon(Icons.event_note_outlined),
                label: const Text('See my bookings'),
              ),
            ]),
          ),
        ),
      ],
    );
  }
}
