import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:hela_core/hela_core.dart';

/// P06 list of jobs where this provider is the provider of record.
class JobsList extends StatelessWidget {
  const JobsList({super.key, required this.api});
  final HelaApi api;

  @override
  Widget build(BuildContext context) {
    return ListView(padding: const EdgeInsets.all(16), children: [
      ScreenTitle('My jobs'),
      AsyncSection<List<dynamic>>(
        load: () async => await api.get('/v1/provider/bookings') as List,
        builder: (context, list, reload) => Column(children: [
          if (list.isEmpty) const Padding(padding: EdgeInsets.all(24), child: Text('No jobs yet.')),
          for (final j in list.cast<Map<String, dynamic>>())
            Card(
              child: StatusTile(
                title: Text((j['category_code'] as String).replaceAll('_', ' ')),
                subtitle: Text('${formatLocal(j['start_at'] as String)} · ${j['area_label']}'),
                status: j['status'] as String,
                onTap: () async {
                  await Navigator.of(context).push(MaterialPageRoute(builder: (_) => JobScreen(api: api, bookingId: j['id'] as String)));
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

/// P06–P09: address after assignment, en route, arrival, PIN check-in, completion, safe exit, payment acknowledgement.
class JobScreen extends StatefulWidget {
  const JobScreen({super.key, required this.api, required this.bookingId});
  final HelaApi api;
  final String bookingId;

  @override
  State<JobScreen> createState() => _JobScreenState();
}

class _JobScreenState extends State<JobScreen> {
  final _pin = TextEditingController();
  final _reason = TextEditingController();

  Future<void> _move(Map<String, dynamic> b, String target, VoidCallback reload,
      {String? pin, bool safety = false}) async {
    await widget.api.post('/v1/bookings/${widget.bookingId}/transitions', {
      'target_status': target,
      'expected_version': b['version'],
      'reason': _reason.text.trim(),
      'checkin_pin': ?pin,
      if (safety) 'safety_cancellation': true,
    }, HelaApi.newIdempotencyKey());
    _pin.clear();
    reload();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Job'), actions: [
        IconButton(
          tooltip: 'Report a problem',
          icon: const Icon(Icons.support_agent),
          onPressed: () => Navigator.of(context)
              .push(MaterialPageRoute(builder: (_) => ReportProblemScreen(api: widget.api, bookingId: widget.bookingId))),
        ),
      ]),
      body: SingleChildScrollView(child: AsyncSection<Map<String, dynamic>>(
        load: () async => await widget.api.get('/v1/bookings/${widget.bookingId}') as Map<String, dynamic>,
        builder: (context, b, reload) {
          final status = b['status'] as String;
          final address = b['address'] as Map<String, dynamic>?;
          final price = b['price'] as Map<String, dynamic>;
          return ListView(shrinkWrap: true, physics: const NeverScrollableScrollPhysics(), padding: const EdgeInsets.all(16), children: [
            Row(children: [
              Expanded(child: Align(alignment: Alignment.centerLeft, child: StatusChip(status))),
              TextButton(onPressed: reload, child: const Text('Refresh')),
            ]),
            ListTile(title: const Text('When'), subtitle: Text('${formatLocal(b['start_at'] as String)} – ${formatLocal(b['end_at'] as String)}')),
            ListTile(title: const Text('Tasks'), subtitle: Text((b['task_codes'] as List).join(', ').replaceAll('_', ' '))),
            if (address != null) ...[
              ListTile(key: const Key('address'), title: const Text('Address'), subtitle: Text(address['text'] as String)),
              ListTile(title: const Text('Map pin'), subtitle: Text(address['pin'] as String)),
            ],
            if (b['service_recipient'] != null) ListTile(title: const Text('Service is for'), subtitle: Text(b['service_recipient'] as String)),
            if (b['task_note'] != null) ListTile(title: const Text('Customer note'), subtitle: Text(b['task_note'] as String)),
            ListTile(title: const Text('Price'), subtitle: Text('${lkr(price['amount_minor'] as num)} · payment ${(price['payment_status'] as String).replaceAll('_', ' ').toLowerCase()}')),
            if (status == 'accepted') ActionButton(label: 'I am on my way', onPressed: () => _move(b, 'en_route', reload)),
            if (status == 'en_route') ActionButton(label: 'I have arrived', onPressed: () => _move(b, 'arrived', reload)),
            if (status == 'arrived') ...[
              const Text('Ask the customer (or the person receiving the service) for the 4-digit check-in code.'),
              TextField(
                key: const Key('checkin-pin'),
                controller: _pin,
                keyboardType: TextInputType.number,
                maxLength: 4,
                inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                decoration: const InputDecoration(labelText: 'Check-in code'),
              ),
              ActionButton(label: 'Start work', onPressed: () => _move(b, 'in_progress', reload, pin: _pin.text)),
            ],
            if (status == 'in_progress') ActionButton(label: 'Work is finished', onPressed: () => _move(b, 'completed_pending_confirmation', reload)),
            if (const {'accepted', 'en_route', 'arrived'}.contains(status)) ...[
              const Divider(),
              const Text('Feel unsafe, or cannot get in? You can leave. A person from Hela will follow up.'),
              TextField(controller: _reason, decoration: const InputDecoration(labelText: 'What happened? (required)')),
              ActionButton(label: 'Leave for safety / cannot attend', danger: true, onPressed: () => _move(b, 'cancelled', reload, safety: true)),
            ],
            if (status == 'in_progress') const InfoBanner('If you feel unsafe during the job, leave and use "Report a problem". '
                'Hela is not an emergency service; contact local emergency services if you are in danger.'),
            if (price['payment_status'] == 'CUSTOMER_REPORTED_PAID') ...[
              const Divider(),
              const Text('The customer says they paid you. Did you receive it?'),
              ActionButton(label: 'Yes, I received the payment', onPressed: () async {
                await widget.api.post('/v1/bookings/${widget.bookingId}/payment-ack', {'received': true});
                reload();
              }),
              ActionButton(label: 'No, I was not paid', danger: true, onPressed: () async {
                await widget.api.post('/v1/bookings/${widget.bookingId}/payment-ack', {'received': false});
                reload();
              }),
            ],
          ]);
        },
      ))
    );
  }
}
