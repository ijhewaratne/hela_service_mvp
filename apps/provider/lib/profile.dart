import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:hela_core/hela_core.dart';
import 'package:latlong2/latlong.dart';

/// P01 onboarding: legal name and date of birth. Age is only pre-checked here; a reviewer verifies it against ID.
class ApplyScreen extends StatefulWidget {
  const ApplyScreen({super.key, required this.api, required this.onApplied});
  final HelaApi api;
  final VoidCallback onApplied;

  @override
  State<ApplyScreen> createState() => _ApplyScreenState();
}

class _ApplyScreenState extends State<ApplyScreen> {
  final _name = TextEditingController();
  DateTime? _dob;

  @override
  Widget build(BuildContext context) {
    return ListView(padding: const EdgeInsets.all(24), children: [
      ScreenTitle('Work with Hela'),
      const AppNotices(),
      const Text('You must be an adult. A reviewer checks your identity and age against your ID before you can get jobs.'),
      TextField(key: const Key('legal-name'), controller: _name, decoration: const InputDecoration(labelText: 'Legal name (as on your ID)')),
      ListTile(
        key: const Key('dob'),
        title: const Text('Date of birth'),
        subtitle: Text(_dob == null ? 'Choose' : '${_dob!.year}-${two(_dob!.month)}-${two(_dob!.day)}'),
        trailing: const Icon(Icons.cake_outlined),
        onTap: () async {
          final d = await showDatePicker(
              context: context, initialDate: DateTime(1990), firstDate: DateTime(1940), lastDate: DateTime.now());
          if (d != null) setState(() => _dob = d);
        },
      ),
      ActionButton(
        label: 'Apply',
        enabled: _dob != null,
        onPressed: () async {
          await widget.api.post('/v1/provider/apply', {
            'legal_name': _name.text.trim(),
            'date_of_birth': '${_dob!.year}-${two(_dob!.month)}-${two(_dob!.day)}',
          });
          widget.onApplied();
        },
      ),
    ]);
  }
}

/// P02/P03/P04: documents, verification status, category requests, service area and weekly hours.
class ProfileScreen extends StatelessWidget {
  const ProfileScreen({super.key, required this.api, required this.showMaps, required this.onChanged});
  final HelaApi api;
  final bool showMaps;
  final VoidCallback onChanged;

  @override
  Widget build(BuildContext context) {
    return ListView(padding: const EdgeInsets.all(16), children: [
      ScreenTitle('Profile'),
      AsyncSection<Map<String, dynamic>>(
        load: () async => await api.get('/v1/provider/me') as Map<String, dynamic>,
        builder: (context, p, reload) {
          final evidence = (p['evidence'] as List).cast<Map<String, dynamic>>();
          final approvals = (p['approvals'] as List).cast<Map<String, dynamic>>();
          final state = p['application_state'] as String;
          return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            StatusTile(title: const Text('Application'), status: state),
            StatusTile(title: const Text('Account'), status: p['account_state'] as String),
            ListTile(
                title: const Text('Identity and age'),
                subtitle: Text(p['identity_verified'] == true ? 'Verified by a reviewer' : 'Not verified yet')),
            const Divider(),
            Text('Documents', style: Theme.of(context).textTheme.titleMedium),
            for (final e in evidence)
              StatusTile(
                title: Text((e['evidence_type'] as String).replaceAll('_', ' ')),
                subtitle: Text([
                  'scan: ${e['scan_state']}',
                  if (e['review_reason'] != null) 'reviewer: ${e['review_reason']}',
                ].join(' · ')),
                status: e['review_state'] as String,
              ),
            if (const {'draft', 'needs_more_info', 'submitted', 'approved'}.contains(state))
              UploadEvidenceButton(api: api, onUploaded: reload),
            if (const {'draft', 'needs_more_info'}.contains(state))
              ActionButton(label: 'Submit application for review', onPressed: () async {
                await api.post('/v1/provider/application/submit');
                reload();
              }),
            const Divider(),
            Text('Service categories', style: Theme.of(context).textTheme.titleMedium),
            const Text('Each category is approved separately. Approval for cleaning does not cover any other work.'),
            for (final a in approvals)
              StatusTile(
                title: Text((a['category_code'] as String).replaceAll('_', ' ')),
                subtitle: Text(a['valid_until'] == null ? 'policy v${a['policy_version']}' : 'valid until ${formatLocal(a['valid_until'] as String)}'),
                status: a['status'] as String,
              ),
            RequestCategoryButton(api: api, existing: approvals.map((a) => a['category_code'] as String).toSet(), onDone: reload),
            const Divider(),
            ListTile(
              leading: const Icon(Icons.map_outlined),
              title: const Text('Where and when I work'),
              onTap: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => AvailabilityScreen(api: api, showMap: showMaps))),
            ),
            ListTile(
              leading: const Icon(Icons.privacy_tip_outlined),
              title: const Text('My data and privacy'),
              onTap: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => MyDataScreen(api: api))),
            ),
            ListTile(
              leading: const Icon(Icons.support_agent),
              title: const Text('Report a problem'),
              onTap: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => ReportProblemScreen(api: api))),
            ),
            OutlinedButton(onPressed: api.logout, child: const Text('Sign out')),
          ]);
        },
      ),
    ]);
  }
}

/// P02 controlled upload: PDF/JPEG/PNG only. The server re-checks the content, scans for malware and encrypts it.
class UploadEvidenceButton extends StatefulWidget {
  const UploadEvidenceButton({super.key, required this.api, required this.onUploaded});
  final HelaApi api;
  final VoidCallback onUploaded;

  @override
  State<UploadEvidenceButton> createState() => _UploadEvidenceButtonState();
}

class _UploadEvidenceButtonState extends State<UploadEvidenceButton> {
  String _type = 'national_id';
  static const _types = ['national_id', 'reference', 'training_record', 'address_proof', 'police_clearance', 'other'];
  static const _media = {'pdf': 'application/pdf', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png'};

  @override
  Widget build(BuildContext context) {
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      DropdownButtonFormField<String>(
        isExpanded: true,  // long labels ellipsise instead of overflowing narrow phones
        initialValue: _type,
        decoration: const InputDecoration(labelText: 'Document type'),
        items: _types.map((t) => DropdownMenuItem(value: t, child: Text(t.replaceAll('_', ' ')))).toList(),
        onChanged: (v) => setState(() => _type = v!),
      ),
      ActionButton(
        label: 'Upload a document (PDF, JPEG or PNG, max 10 MB)',
        onPressed: () async {
          final picked = await FilePicker.pickFiles(type: FileType.custom, allowedExtensions: _media.keys.toList());
          if (picked.isEmpty) return;
          final f = picked.single;
          final ext = (f.extension ?? '').toLowerCase();
          if (!_media.containsKey(ext)) return;
          // Original file names are not sent (they can contain personal data); only the extension is kept.
          await widget.api.upload('/v1/provider/evidence', {'evidence_type': _type}, await f.readAsBytes(), 'document.$ext', _media[ext]!);
          widget.onUploaded();
        },
      ),
    ]);
  }
}

class RequestCategoryButton extends StatelessWidget {
  const RequestCategoryButton({super.key, required this.api, required this.existing, required this.onDone});
  final HelaApi api;
  final Set<String> existing;
  final VoidCallback onDone;

  @override
  Widget build(BuildContext context) {
    return AsyncSection<List<dynamic>>(
      load: () async => await api.get('/v1/categories') as List,
      builder: (context, cats, _) => Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        for (final c in cats.cast<Map<String, dynamic>>().where((c) => !existing.contains(c['code'])))
          OutlinedButton(
            onPressed: () async {
              await api.post('/v1/provider/categories', {'category_code': c['code']});
              onDone();
            },
            child: Text('Ask to be approved for ${c['display_name']}'),
          ),
      ]),
    );
  }
}

/// P04: service area (rounded server-side; the exact home location is never stored) and weekly hours in Sri Lanka time.
class AvailabilityScreen extends StatefulWidget {
  const AvailabilityScreen({super.key, required this.api, this.showMap = true});
  final HelaApi api;
  final bool showMap;

  @override
  State<AvailabilityScreen> createState() => _AvailabilityScreenState();
}

class _AvailabilityScreenState extends State<AvailabilityScreen> {
  static const _days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  LatLng _base = const LatLng(7.2906, 80.6337);
  double _radius = 8;
  final _on = List<bool>.filled(7, true);
  TimeOfDay _start = const TimeOfDay(hour: 8, minute: 0);
  TimeOfDay _end = const TimeOfDay(hour: 18, minute: 0);

  String _hm(TimeOfDay t) => '${two(t.hour)}:${two(t.minute)}';

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Where and when I work')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        const Text('Tap roughly where you start from. It is rounded to about 500 m and never shown to customers.'),
        MapPinPicker(showMap: widget.showMap, onChanged: (p) => _base = p),
        Text('I travel up to ${_radius.round()} km (straight line)'),
        Slider(value: _radius, min: 1, max: 25, divisions: 24, label: '${_radius.round()} km', onChanged: (v) => setState(() => _radius = v)),
        ActionButton(label: 'Save area', onPressed: () async {
          await widget.api.put('/v1/provider/service-area', {'lat': _base.latitude, 'lon': _base.longitude, 'radius_km': _radius.roundToDouble()});
          if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Area saved')));
        }),
        const Divider(),
        const Text('Working days and hours (Sri Lanka time)'),
        Wrap(spacing: 8, children: [
          for (var d = 0; d < 7; d++)
            FilterChip(label: Text(_days[d]), selected: _on[d], onSelected: (v) => setState(() => _on[d] = v)),
        ]),
        ListTile(title: const Text('From'), trailing: Text(_hm(_start)), onTap: () async {
          final t = await showTimePicker(context: context, initialTime: _start);
          if (t != null) setState(() => _start = t);
        }),
        ListTile(title: const Text('Until'), trailing: Text(_hm(_end)), onTap: () async {
          final t = await showTimePicker(context: context, initialTime: _end);
          if (t != null) setState(() => _end = t);
        }),
        ActionButton(label: 'Save hours', onPressed: () async {
          await widget.api.put('/v1/provider/availability', {
            'slots': [
              for (var d = 0; d < 7; d++)
                if (_on[d]) {'weekday': d, 'start_local': _hm(_start), 'end_local': _hm(_end)},
            ],
          });
          if (context.mounted) ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Hours saved')));
        }),
      ]),
    );
  }
}
