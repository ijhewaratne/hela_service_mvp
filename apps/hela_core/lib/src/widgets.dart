import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'api_client.dart';
import 'l10n.dart';

/// Shows server errors with the support reference; never a stack trace.
class ErrorBanner extends StatelessWidget {
  const ErrorBanner(this.error, {super.key});
  final Object? error;

  @override
  Widget build(BuildContext context) {
    if (error == null) return const SizedBox.shrink();
    final e = error;
    final s = Strings.of(context);
    final text = e is ApiException
        ? '${e.code == 'OFFLINE' ? s.t('error.offline') : e.message}'
            '${e.reasonCodes.isEmpty ? '' : ' (${e.reasonCodes.join(', ')})'}\n${s.t('common.ref')}: ${e.correlationId}'
        : e.toString();
    return Semantics(
      liveRegion: true,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 8),
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
            color: Theme.of(context).colorScheme.errorContainer, borderRadius: BorderRadius.circular(8)),
        child: Text(text, style: TextStyle(color: Theme.of(context).colorScheme.onErrorContainer)),
      ),
    );
  }
}

class InfoBanner extends StatelessWidget {
  const InfoBanner(this.text, {super.key});
  final String text;

  @override
  Widget build(BuildContext context) => Container(
        margin: const EdgeInsets.symmetric(vertical: 8),
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
            color: Theme.of(context).colorScheme.tertiaryContainer, borderRadius: BorderRadius.circular(8)),
        child: Text(text, style: TextStyle(color: Theme.of(context).colorScheme.onTertiaryContainer)),
      );
}

/// Banner reminding testers this is synthetic, plus the translation-pending notice for si/ta.
class AppNotices extends StatelessWidget {
  const AppNotices({super.key});

  @override
  Widget build(BuildContext context) {
    final s = Strings.of(context);
    return Column(children: [
      InfoBanner(s.t('app.synthetic')),
      if (s.translationPending) InfoBanner(s.t('lang.pending')),
    ]);
  }
}

class StatusChip extends StatelessWidget {
  const StatusChip(this.status, {super.key});
  final String? status;

  @override
  Widget build(BuildContext context) {
    final v = status ?? '—';
    final scheme = Theme.of(context).colorScheme;
    final bad = RegExp(r'cancel|disput|hold|unfulfilled|expired|suspend|denied|revoked').hasMatch(v);
    final good = RegExp(r'complet|accepted|approved|confirmed|clean|sent|active').hasMatch(v);
    return Chip(
      // Long states ("completed pending confirmation") wrap instead of overflowing narrow phones.
      label: Text(v.replaceAll('_', ' '), softWrap: true),
      backgroundColor: bad ? scheme.errorContainer : good ? scheme.primaryContainer : scheme.surfaceContainerHighest,
      visualDensity: VisualDensity.compact,
    );
  }
}

/// Loads data once and on demand. Keeps the last good data visible during a refresh.
class AsyncSection<T> extends StatefulWidget {
  const AsyncSection({super.key, required this.load, required this.builder});
  final Future<T> Function() load;
  final Widget Function(BuildContext context, T data, VoidCallback reload) builder;

  @override
  State<AsyncSection<T>> createState() => _AsyncSectionState<T>();
}

class _AsyncSectionState<T> extends State<AsyncSection<T>> {
  T? _data;
  Object? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _run();
  }

  Future<void> _run() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final d = await widget.load();
      if (mounted) setState(() => _data = d);
    } catch (e) {
      if (mounted) setState(() => _error = e);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_data == null && _loading) return const Padding(padding: EdgeInsets.all(24), child: Center(child: CircularProgressIndicator()));
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      ErrorBanner(_error),
      if (_error != null) TextButton(onPressed: _run, child: Text(Strings.of(context).t('common.retry'))),
      if (_data != null) widget.builder(context, _data as T, _run),
    ]);
  }
}

/// Runs an action, shows progress and the error, and never reports success the server did not confirm.
class ActionButton extends StatefulWidget {
  const ActionButton({super.key, required this.label, required this.onPressed, this.danger = false, this.enabled = true});
  final String label;
  final Future<void> Function() onPressed;
  final bool danger;
  final bool enabled;

  @override
  State<ActionButton> createState() => _ActionButtonState();
}

class _ActionButtonState extends State<ActionButton> {
  bool _busy = false;
  Object? _error;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      FilledButton(
        style: widget.danger ? FilledButton.styleFrom(backgroundColor: scheme.error, foregroundColor: scheme.onError) : null,
        onPressed: !widget.enabled || _busy
            ? null
            : () async {
                setState(() {
                  _busy = true;
                  _error = null;
                });
                try {
                  await widget.onPressed();
                } catch (e) {
                  if (mounted) setState(() => _error = e);
                } finally {
                  if (mounted) setState(() => _busy = false);
                }
              },
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 12),
          child: _busy ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2)) : Text(widget.label),
        ),
      ),
      ErrorBanner(_error),
    ]);
  }
}

/// C01/P01 language choice. Large targets; language names in their own script.
class LanguageScreen extends StatelessWidget {
  const LanguageScreen({super.key, required this.onChosen, required this.title});
  final ValueChanged<String> onChosen;
  final String title;

  @override
  Widget build(BuildContext context) {
    final s = Strings.of(context);
    return Scaffold(
      body: SafeArea(
        child: ListView(padding: const EdgeInsets.all(24), children: [
          Text(title, style: Theme.of(context).textTheme.headlineMedium),
          const SizedBox(height: 8),
          Text(s.t('lang.choose'), style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 16),
          for (final e in Strings.languageNames.entries)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: OutlinedButton(
                key: Key('lang-${e.key}'),
                onPressed: () => onChosen(e.key),
                child: Padding(padding: const EdgeInsets.all(16), child: Text(e.value, style: const TextStyle(fontSize: 22))),
              ),
            ),
          const AppNotices(),
        ]),
      ),
    );
  }
}

/// C02/P01 phone OTP sign-in. The same response is shown whether or not an account exists.
class PhoneSignInScreen extends StatefulWidget {
  const PhoneSignInScreen({super.key, required this.api, required this.onSignedIn, required this.title});
  final HelaApi api;
  final VoidCallback onSignedIn;
  final String title;

  @override
  State<PhoneSignInScreen> createState() => _PhoneSignInScreenState();
}

class _PhoneSignInScreenState extends State<PhoneSignInScreen> {
  final _phone = TextEditingController();
  final _code = TextEditingController();
  String? _challenge;
  Object? _error;
  bool _busy = false;

  Future<void> _do(Future<void> Function() f) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await f();
    } catch (e) {
      setState(() => _error = e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final s = Strings.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(widget.title)),
      body: SafeArea(
        child: ListView(padding: const EdgeInsets.all(24), children: [
          const AppNotices(),
          TextField(
            key: const Key('phone'),
            controller: _phone,
            keyboardType: TextInputType.phone,
            autofillHints: const [AutofillHints.telephoneNumber],
            decoration: InputDecoration(labelText: s.t('auth.phone')),
            enabled: _challenge == null,
          ),
          const SizedBox(height: 12),
          if (_challenge == null)
            FilledButton(
              key: const Key('send-code'),
              onPressed: _busy ? null : () => _do(() async {
                final id = await widget.api.requestOtp(_phone.text.trim());
                setState(() => _challenge = id);
              }),
              child: Text(s.t('auth.send_code')),
            )
          else ...[
            Text(s.t('auth.code_sent')),
            TextField(
              key: const Key('code'),
              controller: _code,
              keyboardType: TextInputType.number,
              maxLength: 6,
              inputFormatters: [FilteringTextInputFormatter.digitsOnly],
              autofillHints: const [AutofillHints.oneTimeCode],
              decoration: InputDecoration(labelText: s.t('auth.code')),
            ),
            FilledButton(
              key: const Key('verify'),
              onPressed: _busy ? null : () => _do(() async {
                await widget.api.verifyOtp(_phone.text.trim(), _challenge!, _code.text);
                widget.onSignedIn();
              }),
              child: Text(s.t('auth.verify')),
            ),
            if (const String.fromEnvironment('HELA_DEV_INBOX_TOKEN').isNotEmpty)
              TextButton(
                key: const Key('dev-fill'),
                onPressed: () async {
                  final code = await widget.api.devLatestCode(_phone.text.trim());
                  if (code != null) setState(() => _code.text = code);
                },
                child: Text(s.t('auth.dev_fill')),
              ),
          ],
          ErrorBanner(_error),
        ]),
      ),
    );
  }
}

/// Notice acceptance (FR-ID-05): records version, language and processing context server-side.
Future<void> ensureNoticesAccepted(BuildContext context, HelaApi api, String language, String processingContext) async {
  final notices = (await api.get('/v1/notices/current?language=$language') as List).cast<Map<String, dynamic>>();
  for (final n in notices) {
    if (!context.mounted) return;
    final ok = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) => AlertDialog(
        title: Text(n['title'] as String),
        content: SingleChildScrollView(child: Text('${n['body']}\n\n(version ${n['version']}, ${n['approval_status']})')),
        actions: [
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: Text(Strings.of(context).t('notice.accept'))),
        ],
      ),
    );
    if (ok == true) await api.post('/v1/notices/${n['id']}/accept', {'processing_context': processingContext});
  }
}

/// Incident report (FR-SAF-01, C12/P10). Reachable without payment or review; never an emergency service.
class ReportProblemScreen extends StatefulWidget {
  const ReportProblemScreen({super.key, required this.api, this.bookingId});
  final HelaApi api;
  final String? bookingId;

  @override
  State<ReportProblemScreen> createState() => _ReportProblemScreenState();
}

class _ReportProblemScreenState extends State<ReportProblemScreen> {
  String _kind = 'service_mismatch';
  String _severity = 'medium';
  final _text = TextEditingController();
  Map<String, dynamic>? _receipt;

  @override
  Widget build(BuildContext context) {
    final s = Strings.of(context);
    return Scaffold(
      appBar: AppBar(title: Text(s.t('support.title'))),
      body: ListView(padding: const EdgeInsets.all(24), children: [
        InfoBanner(s.t('support.body')),
        InfoBanner(s.t('safety.guidance_pending')),
        if (_receipt != null)
          InfoBanner('${s.t('support.sent')} ${s.t('common.ref')}: ${(_receipt!['id'] as String).substring(0, 8)}')
        else ...[
          DropdownButtonFormField<String>(
            isExpanded: true,  // long labels ellipsise instead of overflowing narrow phones
            initialValue: _kind,
            decoration: InputDecoration(labelText: s.t('support.kind')),
            items: const ['safety', 'property_damage', 'harassment', 'service_mismatch', 'payment_dispute', 'no_entry', 'other']
                .map((k) => DropdownMenuItem(value: k, child: Text(k.replaceAll('_', ' '))))
                .toList(),
            onChanged: (v) => setState(() => _kind = v!),
          ),
          DropdownButtonFormField<String>(
            isExpanded: true,  // long labels ellipsise instead of overflowing narrow phones
            initialValue: _severity,
            decoration: InputDecoration(labelText: s.t('support.severity')),
            items: const ['low', 'medium', 'high', 'critical'].map((k) => DropdownMenuItem(value: k, child: Text(k))).toList(),
            onChanged: (v) => setState(() => _severity = v!),
          ),
          TextField(controller: _text, maxLines: 5, maxLength: 3000, decoration: InputDecoration(labelText: s.t('support.describe'))),
          ActionButton(
            label: s.t('support.title'),
            onPressed: () async {
              final r = await widget.api.post('/v1/incidents', {
                if (widget.bookingId != null) 'booking_id': widget.bookingId,
                'kind': _kind,
                'severity': _severity,
                'description': _text.text.trim(),
              }) as Map<String, dynamic>;
              setState(() => _receipt = r);
            },
          ),
        ],
      ]),
    );
  }
}
