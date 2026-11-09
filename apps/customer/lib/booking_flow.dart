import 'dart:async';

import 'package:flutter/material.dart';
import 'package:hela_core/hela_core.dart';

import 'account.dart';
import 'bookings.dart';

/// C04–C07 + C13: address, who it is for, tasks, time, then the price and terms the customer accepts.
class BookingFlow extends StatefulWidget {
  const BookingFlow({super.key, required this.api, required this.category, required this.showMaps});
  final HelaApi api;
  final Map<String, dynamic> category;
  final bool showMaps;

  @override
  State<BookingFlow> createState() => _BookingFlowState();
}

class _BookingFlowState extends State<BookingFlow> {
  int _step = 0;
  String? _addressId;
  String? _beneficiaryId; // null = for me
  final Set<String> _tasks = {};
  final _note = TextEditingController();
  DateTime _day = DateTime.now().add(const Duration(days: 1));
  TimeOfDay _time = const TimeOfDay(hour: 10, minute: 0);
  late int _minutes = (widget.category['min_duration_minutes'] as int).clamp(60, 480);
  Map<String, dynamic>? _quote;
  bool _termsAccepted = false;
  // One key per confirmation, reused if the network drops and the customer taps again (no duplicate requests).
  String? _idempotencyKey;

  Map<String, dynamic> get c => widget.category;
  List<String> get _allowed => (c['allowed_tasks'] as List).cast<String>();

  Future<void> _previewQuote() async {
    final start = DateTime(_day.year, _day.month, _day.day, _time.hour, _time.minute);
    final q = await widget.api.post('/v1/quotes/preview', {
      'category_code': c['code'],
      'address_id': _addressId,
      'start_at': colomboToUtcIso(start),
      'duration_minutes': _minutes,
    }) as Map<String, dynamic>;
    setState(() {
      _quote = q;
      _termsAccepted = false;
      _idempotencyKey = null;
      _step = 4;
    });
  }

  // Returning to an earlier step invalidates the quote; it must be re-priced before any submission.
  // An uncertain response on the review step does NOT invalidate the idempotency key.
  void _editPreviousStep() {
    if (_step < 1) return;
    setState(() {
      _step -= 1;
      _quote = null;
      _termsAccepted = false;
      _idempotencyKey = null;
    });
  }

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  Future<void> _confirm() async {
    final q = _quote!;
    await widget.api.post('/v1/quotes/${q['quote_id']}/accept', {'policy_version': q['policy_version']});
    _idempotencyKey ??= HelaApi.newIdempotencyKey();
    final r = await widget.api.post('/v1/requests', {
      'quote_id': q['quote_id'],
      'task_codes': _tasks.toList()..sort(),
      if (_note.text.trim().isNotEmpty) 'task_note': _note.text.trim(),
      if (_beneficiaryId != null) 'beneficiary_id': _beneficiaryId,
    }, _idempotencyKey) as Map<String, dynamic>;
    if (!mounted) return;
    Navigator.of(context).pushReplacement(MaterialPageRoute(
        builder: (_) => FindingProviderScreen(api: widget.api, requestId: r['request_id'] as String)));
  }

  @override
  Widget build(BuildContext context) {
    final stepMin = c['min_duration_minutes'] as int;
    final stepMax = c['max_duration_minutes'] as int;
    final steps = <Step>[
          Step(
            title: const Text('Address'),
            content: AsyncSection<List<dynamic>>(
              load: () async => await widget.api.get('/v1/addresses') as List,
              builder: (context, list, reload) => Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                RadioGroup<String>(
                  groupValue: _addressId,
                  onChanged: (v) => setState(() => _addressId = v),
                  child: Column(children: [
                    for (final a in list.cast<Map<String, dynamic>>())
                      RadioListTile<String>(
                        key: Key('addr-${a['label']}'),
                        value: a['id'] as String,
                        title: Text(a['label'] as String),
                        subtitle: Text('${a['address_text']}\n${a['area_label']}'),
                      ),
                  ]),
                ),
                TextButton.icon(
                  icon: const Icon(Icons.add_location_alt_outlined),
                  label: const Text('Add an address'),
                  onPressed: () async {
                    await Navigator.of(context).push(MaterialPageRoute(
                        builder: (_) => AddAddressScreen(api: widget.api, showMap: widget.showMaps)));
                    reload();
                  },
                ),
                FilledButton(
                    key: const Key('next-address'),
                    onPressed: _addressId == null ? null : () => setState(() => _step = 1),
                    child: const Text('Next')),
              ]),
            ),
          ),
          Step(
            title: const Text('Who is the service for?'),
            content: AsyncSection<List<dynamic>>(
              load: () async => await widget.api.get('/v1/beneficiaries') as List,
              builder: (context, list, reload) => Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                RadioGroup<String?>(
                  groupValue: _beneficiaryId,
                  onChanged: (v) => setState(() => _beneficiaryId = v),
                  child: Column(children: [
                    const RadioListTile<String?>(key: Key('for-me'), value: null, title: Text('For me')),
                    for (final b in list.cast<Map<String, dynamic>>())
                      RadioListTile<String?>(
                        value: b['id'] as String,
                        title: Text('${b['name']} (${b['relationship']})'),
                        subtitle: Text('${b['phone_masked']} · they must confirm by SMS before we look for a provider'),
                      ),
                  ]),
                ),
                TextButton.icon(
                  icon: const Icon(Icons.person_add_alt_outlined),
                  label: const Text('Book for a family member'),
                  onPressed: () async {
                    await Navigator.of(context).push(MaterialPageRoute(builder: (_) => AddBeneficiaryScreen(api: widget.api)));
                    reload();
                  },
                ),
                FilledButton(key: const Key('next-who'), onPressed: () => setState(() => _step = 2), child: const Text('Next')),
              ]),
            ),
          ),
          Step(
            title: const Text('What needs doing?'),
            content: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              for (final t in _allowed)
                CheckboxListTile(
                  key: Key('task-$t'),
                  value: _tasks.contains(t),
                  title: Text(t.replaceAll('_', ' ')),
                  onChanged: (v) => setState(() => v! ? _tasks.add(t) : _tasks.remove(t)),
                ),
              Text('Not included: ${(c['prohibited_tasks'] as List).join(', ').replaceAll('_', ' ')}',
                  style: Theme.of(context).textTheme.bodySmall),
              TextField(
                  controller: _note,
                  maxLength: 500,
                  decoration: const InputDecoration(
                      labelText: 'Note for the provider (optional)',
                      helperText: 'Shown only to the assigned provider. Do not include health details or ID numbers.')),
              FilledButton(
                  key: const Key('next-tasks'), onPressed: _tasks.isEmpty ? null : () => setState(() => _step = 3), child: const Text('Next')),
            ]),
          ),
          Step(
            title: const Text('When?'),
            content: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              ListTile(
                title: const Text('Date'),
                subtitle: Text('${_day.year}-${two(_day.month)}-${two(_day.day)}'),
                trailing: const Icon(Icons.calendar_today),
                onTap: () async {
                  final d = await showDatePicker(
                      context: context,
                      initialDate: _day,
                      firstDate: DateTime.now(),
                      lastDate: DateTime.now().add(const Duration(days: 30)));
                  if (d != null) setState(() => _day = d);
                },
              ),
              ListTile(
                title: const Text('Start time (Sri Lanka time)'),
                subtitle: Text(_time.format(context)),
                trailing: const Icon(Icons.schedule),
                onTap: () async {
                  final t = await showTimePicker(context: context, initialTime: _time);
                  if (t != null) setState(() => _time = TimeOfDay(hour: t.hour, minute: t.minute < 30 ? 0 : 30));
                },
              ),
              Text('Duration: ${_minutes ~/ 60} h ${_minutes % 60} min'),
              Slider(
                value: _minutes.toDouble(),
                min: stepMin.toDouble(),
                max: stepMax.toDouble(),
                divisions: (stepMax - stepMin) ~/ 30,
                label: '$_minutes min',
                onChanged: (v) => setState(() => _minutes = v.round()),
              ),
              ActionButton(label: 'See price', onPressed: _previewQuote),
            ]),
          ),
          Step(
            title: const Text('Price and terms'),
            content: _quote == null
                ? const SizedBox.shrink()
                : Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                    for (final comp in (_quote!['components'] as List).cast<Map<String, dynamic>>())
                      ListTile(dense: true, title: Text((comp['code'] as String).replaceAll('_', ' ')), trailing: Text(lkr(comp['amount_minor'] as num))),
                    ListTile(
                      title: const Text('Estimated total', style: TextStyle(fontWeight: FontWeight.bold)),
                      subtitle: Text('${_quote!['basis']}'.replaceAll('_', ' ')),
                      trailing: Text(lkr(_quote!['total_minor'] as num), key: const Key('total'), style: const TextStyle(fontWeight: FontWeight.bold)),
                    ),
                    if (_quote!['price_approval_status'] == 'UNAPPROVED') const InfoBanner('Synthetic test price. Not an approved tariff.'),
                    for (final a in (_quote!['assumptions'] as List).cast<String>()) Text('• $a'),
                    Text('• Cancellation fee: ${lkr((_quote!['cancellation_terms'] as Map)['fee_lkr_minor'] as num? ?? 0)}'),
                    Text('• Price valid until ${formatLocal(_quote!['expires_at'] as String)}'),
                    CheckboxListTile(
                      key: const Key('accept-terms'),
                      value: _termsAccepted,
                      title: Text('I accept these terms (policy version ${_quote!['policy_version']})'),
                      onChanged: (v) => setState(() => _termsAccepted = v!),
                    ),
                    ActionButton(label: 'Request this service', enabled: _termsAccepted, onPressed: _confirm),
                  ]),
          ),
    ];
    return Scaffold(
      appBar: AppBar(title: Text(c['display_name'] as String)),
      body: SafeArea(
        child: ListView(
          // A different key for every stage resets scroll and avoids reusing AsyncSection state
          // (an address-list fetch must not be confused with a beneficiary-list fetch).
          key: ValueKey('guided-booking-step-$_step'),
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
          children: [
            Text('Step ${_step + 1} of ${steps.length}', style: Theme.of(context).textTheme.labelLarge),
            const SizedBox(height: 8),
            LinearProgressIndicator(
              key: const Key('booking-progress'),
              value: (_step + 1) / steps.length,
              minHeight: 6,
              semanticsLabel: 'Booking step ${_step + 1} of ${steps.length}',
            ),
            const SizedBox(height: 20),
            Semantics(header: true, child: DefaultTextStyle(
              style: Theme.of(context).textTheme.headlineSmall!,
              child: steps[_step].title,
            )),
            const SizedBox(height: 14),
            if (_step == 1 && _beneficiaryId != null)
              const InfoBanner('The person receiving the service must approve by SMS before a provider is contacted.'),
            steps[_step].content,
            if (_step > 0) ...[
              const SizedBox(height: 20),
              OutlinedButton.icon(
                key: const Key('booking-back'),
                onPressed: _editPreviousStep,
                icon: const Icon(Icons.arrow_back),
                label: const Text('Back to previous step'),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// C08: honest status only. "Accepted" appears only once the server has a booking (NFR-08).
class FindingProviderScreen extends StatefulWidget {
  const FindingProviderScreen({super.key, required this.api, required this.requestId, this.pollEvery = const Duration(seconds: 3)});
  final HelaApi api;
  final String requestId;
  final Duration pollEvery;

  @override
  State<FindingProviderScreen> createState() => _FindingProviderScreenState();
}

class _FindingProviderScreenState extends State<FindingProviderScreen> {
  Map<String, dynamic>? _r;
  Object? _error;
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _poll();
    _timer = Timer.periodic(widget.pollEvery, (_) => _poll());
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _poll() async {
    try {
      final r = await widget.api.get('/v1/requests/${widget.requestId}') as Map<String, dynamic>;
      if (!mounted) return;
      setState(() {
        _r = r;
        _error = null;
      });
      if (r['booking_id'] != null || const {'unfulfilled', 'expired', 'cancelled'}.contains(r['status'])) _timer?.cancel();
    } catch (e) {
      if (mounted) setState(() => _error = e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final r = _r;
    final status = r?['status'] as String?;
    final message = switch (status) {
      'awaiting_beneficiary' => 'Waiting for your family member to confirm by SMS. We will not contact any provider until they agree.',
      'requested' || 'offering' => 'Looking for an approved provider. This is not confirmed yet.',
      'accepted' => 'A provider accepted your booking.',
      'unfulfilled' => 'No approved provider is available for this time. Start a new request for another time if needed.',
      'expired' => 'Nobody accepted in time. Start a new request for another time.',
      'cancelled' => 'This request was cancelled.',
      _ => 'Checking…',
    };
    return Scaffold(
      appBar: AppBar(title: const Text('Your request')),
      body: ListView(padding: const EdgeInsets.all(24), children: [
        if (status != null) StatusChip(status),
        const SizedBox(height: 12),
        Text(message, key: const Key('finding-message'), style: Theme.of(context).textTheme.titleMedium),
        if (const {'requested', 'offering', 'awaiting_beneficiary'}.contains(status) && _error == null)
          const Padding(padding: EdgeInsets.all(24), child: Center(child: CircularProgressIndicator())),
        if (_error != null)
          OutlinedButton.icon(
            key: const Key('refresh-request'),
            onPressed: _poll,
            icon: const Icon(Icons.refresh),
            label: const Text('Refresh request status'),
          ),
        ErrorBanner(_error),
        if (r?['booking_id'] != null)
          FilledButton(
            key: const Key('open-booking'),
            onPressed: () => Navigator.of(context).pushReplacement(MaterialPageRoute(
                builder: (_) => BookingScreen(api: widget.api, bookingId: r!['booking_id'] as String))),
            child: const Text('See booking'),
          ),
        if (const {'unfulfilled', 'expired', 'cancelled'}.contains(status))
          OutlinedButton.icon(
            key: const Key('new-request'),
            onPressed: () => Navigator.of(context).maybePop(),
            icon: const Icon(Icons.restart_alt_outlined),
            label: const Text('Start a new request'),
          ),
        if (const {'requested', 'offering', 'awaiting_beneficiary'}.contains(status))
          ActionButton(
            label: 'Cancel request',
            onPressed: () async {
              await widget.api.post('/v1/requests/${widget.requestId}/cancel',
                  {'reason': 'Cancelled by customer'});
              await _poll();
            },
          ),
      ]),
    );
  }
}
