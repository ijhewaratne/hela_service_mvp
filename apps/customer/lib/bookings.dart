import 'package:flutter/material.dart';
import 'package:hela_core/hela_core.dart';

import 'booking_flow.dart';

/// C12 history.
class BookingsList extends StatelessWidget {
  const BookingsList({super.key, required this.api});
  final HelaApi api;

  @override
  Widget build(BuildContext context) {
    return ListView(padding: const EdgeInsets.all(16), children: [
      Text('My bookings', style: Theme.of(context).textTheme.headlineSmall),
      AsyncSection<List<dynamic>>(
        load: () async => await api.get('/v1/requests') as List,
        builder: (context, list, reload) => Column(children: [
          if (list.isEmpty) const Padding(padding: EdgeInsets.all(24), child: Text('No bookings yet.')),
          for (final r in list.cast<Map<String, dynamic>>())
            Card(
              child: ListTile(
                title: Text((r['category_code'] as String).replaceAll('_', ' ')),
                subtitle: Text(formatLocal(r['start_at'] as String)),
                trailing: StatusChip((r['booking_status'] ?? r['status']) as String),
                onTap: () async {
                  await Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => r['booking_id'] != null
                          ? BookingScreen(api: api, bookingId: r['booking_id'] as String)
                          : FindingProviderScreen(api: api, requestId: r['request_id'] as String)));
                  reload();
                },
              ),
            ),
          TextButton(onPressed: reload, child: const Text('Refresh')),
        ]),
      ),
    ]);
  }
}

/// C09–C11: provider identity, check-in PIN, status, completion/dispute, offline payment, review and support.
class BookingScreen extends StatefulWidget {
  const BookingScreen({super.key, required this.api, required this.bookingId});
  final HelaApi api;
  final String bookingId;

  @override
  State<BookingScreen> createState() => _BookingScreenState();
}

class _BookingScreenState extends State<BookingScreen> {
  int _rating = 5;
  final _review = TextEditingController();
  final _reference = TextEditingController();
  final _reason = TextEditingController();

  Future<void> _move(Map<String, dynamic> b, String target, VoidCallback reload) async {
    await widget.api.post('/v1/bookings/${widget.bookingId}/transitions',
        {'target_status': target, 'expected_version': b['version'], 'reason': _reason.text.trim()}, HelaApi.newIdempotencyKey());
    reload();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Booking'), actions: [
        IconButton(
          tooltip: 'Report a problem',
          icon: const Icon(Icons.support_agent),
          onPressed: () => Navigator.of(context).push(
              MaterialPageRoute(builder: (_) => ReportProblemScreen(api: widget.api, bookingId: widget.bookingId))),
        ),
      ]),
      body: AsyncSection<Map<String, dynamic>>(
        load: () async => await widget.api.get('/v1/bookings/${widget.bookingId}') as Map<String, dynamic>,
        builder: (context, b, reload) {
          final status = b['status'] as String;
          final price = b['price'] as Map<String, dynamic>;
          final provider = b['provider'] as Map<String, dynamic>?;
          return ListView(shrinkWrap: true, physics: const NeverScrollableScrollPhysics(), padding: const EdgeInsets.all(16), children: [
            Row(children: [
              Expanded(child: Align(alignment: Alignment.centerLeft, child: StatusChip(status))),
              TextButton(onPressed: reload, child: const Text('Refresh')),
            ]),
            ListTile(title: const Text('When'), subtitle: Text('${formatLocal(b['start_at'] as String)} – ${formatLocal(b['end_at'] as String)}')),
            if (provider != null)
              ListTile(
                  title: const Text('Your provider'),
                  subtitle: Text('${provider['display_name']} · approved for ${(provider['approved_category'] as String).replaceAll('_', ' ')}')),
            ListTile(
                title: const Text('Price (pay the provider directly)'),
                subtitle: Text('${lkr(price['amount_minor'] as num)} · payment: ${(price['payment_status'] as String).replaceAll('_', ' ').toLowerCase()}'),
            ),
            if (b['checkin_pin'] != null)
              Card(
                color: Theme.of(context).colorScheme.primaryContainer,
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(children: [
                    const Text('Check-in code'),
                    Text(b['checkin_pin'] as String, key: const Key('pin'),
                        style: Theme.of(context).textTheme.displaySmall?.copyWith(letterSpacing: 8)),
                    const Text('Give this code only to the provider at your door. If you do not recognise them, do not let them in '
                        'and report a problem.', textAlign: TextAlign.center),
                  ]),
                ),
              ),
            if (status == 'completed_pending_confirmation') ...[
              const Text('The provider says the work is finished.'),
              ActionButton(label: 'Confirm work is complete', onPressed: () => _move(b, 'completed', reload)),
              TextField(controller: _reason, decoration: const InputDecoration(labelText: 'What was not done? (for a dispute)')),
              ActionButton(label: 'Dispute completion', danger: true, onPressed: () => _move(b, 'disputed', reload)),
            ],
            if (const {'accepted', 'en_route', 'reassigning'}.contains(status)) ...[
              TextField(controller: _reason, decoration: const InputDecoration(labelText: 'Reason for cancelling')),
              ActionButton(label: 'Cancel booking (no fee)', danger: true, onPressed: () => _move(b, 'cancelled', reload)),
            ],
            if (const {'completed_pending_confirmation', 'completed', 'disputed'}.contains(status) &&
                price['payment_status'] == 'UNPAID') ...[
              const Divider(),
              const Text('Paid the provider in cash or by transfer? Let us know. Hela does not handle the money.'),
              TextField(controller: _reference, decoration: const InputDecoration(labelText: 'Reference (optional)')),
              ActionButton(
                label: 'I paid the provider',
                onPressed: () async {
                  await widget.api.post('/v1/bookings/${widget.bookingId}/payment-report',
                      {if (_reference.text.trim().isNotEmpty) 'reference': _reference.text.trim()});
                  reload();
                },
              ),
            ],
            if (status == 'completed') ...[
              const Divider(),
              const Text('Rate this service'),
              Wrap(children: [
                for (var i = 1; i <= 5; i++)
                  IconButton(
                    tooltip: '$i stars',
                    icon: Icon(i <= _rating ? Icons.star : Icons.star_border),
                    onPressed: () => setState(() => _rating = i),
                  ),
              ]),
              TextField(controller: _review, maxLength: 1000,
                  decoration: const InputDecoration(labelText: 'Comment (optional, checked before it is shown)')),
              ActionButton(
                label: 'Send review',
                onPressed: () async {
                  await widget.api.post('/v1/bookings/${widget.bookingId}/reviews', {
                    'rating': _rating,
                    if (_review.text.trim().isNotEmpty) 'text': _review.text.trim(),
                  });
                  if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Thank you')));
                },
              ),
            ],
          ]);
        },
      ),
    );
  }
}
