import 'package:flutter/material.dart';
import 'package:hela_core/hela_core.dart';
import 'package:latlong2/latlong.dart';

/// Account: language, addresses, family members, notifications, support, sign out (C04, C12, C13, FR-NOT-03).
class AccountScreen extends StatelessWidget {
  const AccountScreen({super.key, required this.api, required this.showMaps});
  final HelaApi api;
  final bool showMaps;

  @override
  Widget build(BuildContext context) {
    final s = Strings.of(context);
    return ListView(padding: const EdgeInsets.all(16), children: [
      ScreenTitle('Account'),
      AsyncSection<Map<String, dynamic>>(
        load: () async => await api.get('/v1/me') as Map<String, dynamic>,
        builder: (context, me, _) => ListTile(title: Text(me['display_name'] as String? ?? 'Customer'), subtitle: Text(me['phone_masked'] as String)),
      ),
      ListTile(
        title: const Text('Language'),
        subtitle: Text(Strings.languageNames[s.language]!),
        trailing: DropdownButton<String>(
          value: s.language,
          items: Strings.languageNames.entries.map((e) => DropdownMenuItem(value: e.key, child: Text(e.value))).toList(),
          onChanged: (v) => LanguageScope.setterOf(context)(v!),
        ),
      ),
      if (s.translationPending) InfoBanner(s.t('lang.pending')),
      ListTile(
        leading: const Icon(Icons.home_outlined),
        title: const Text('Add an address'),
        onTap: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => AddAddressScreen(api: api, showMap: showMaps))),
      ),
      ListTile(
        leading: const Icon(Icons.family_restroom),
        title: const Text('Family members I book for'),
        onTap: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => AddBeneficiaryScreen(api: api))),
      ),
      ListTile(
        leading: const Icon(Icons.notifications_outlined),
        title: const Text('Messages sent to me'),
        onTap: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => NotificationsScreen(api: api))),
      ),
      ListTile(
        leading: const Icon(Icons.privacy_tip_outlined),
        title: const Text('My data and privacy'),
        onTap: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => MyDataScreen(api: api))),
      ),
      ListTile(
        leading: const Icon(Icons.support_agent),
        title: Text(s.t('support.title')),
        onTap: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => ReportProblemScreen(api: api))),
      ),
      const SizedBox(height: 16),
      OutlinedButton(onPressed: api.logout, child: Text(s.t('common.sign_out'))),
    ]);
  }
}

/// C04: label, address text and a confirmed map pin. The server rejects places outside the served region.
class AddAddressScreen extends StatefulWidget {
  const AddAddressScreen({super.key, required this.api, this.showMap = true});
  final HelaApi api;
  final bool showMap;

  @override
  State<AddAddressScreen> createState() => _AddAddressScreenState();
}

class _AddAddressScreenState extends State<AddAddressScreen> {
  final _label = TextEditingController(text: 'Home');
  final _text = TextEditingController();
  LatLng _pin = const LatLng(7.2906, 80.6337);
  bool _confirmed = false;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Add an address')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        TextField(key: const Key('addr-label'), controller: _label, maxLength: 40, decoration: const InputDecoration(labelText: 'Label')),
        TextField(key: const Key('addr-text'), controller: _text, maxLines: 2,
            decoration: const InputDecoration(labelText: 'Address (house, street, landmark)')),
        const SizedBox(height: 12),
        const Text('Tap the map where the provider should arrive.'),
        MapPinPicker(showMap: widget.showMap, onChanged: (p) => setState(() {
          _pin = p;
          _confirmed = false;
        })),
        CheckboxListTile(
          key: const Key('pin-confirm'),
          value: _confirmed,
          title: const Text('The pin marks the right place'),
          onChanged: (v) => setState(() => _confirmed = v!),
        ),
        ActionButton(
          label: 'Save address',
          enabled: _confirmed,
          onPressed: () async {
            await widget.api.post('/v1/addresses', {
              'label': _label.text.trim(),
              'address_text': _text.text.trim(),
              'lat': _pin.latitude,
              'lon': _pin.longitude,
              'pin_confirmed': _confirmed,
            });
            if (context.mounted) Navigator.of(context).pop();
          },
        ),
      ]),
    );
  }
}

/// C13: someone in Sri Lanka the customer books for. They confirm each booking by SMS (Q19); after saving, the
/// customer only ever sees the last three digits of their number (Q20).
class AddBeneficiaryScreen extends StatefulWidget {
  const AddBeneficiaryScreen({super.key, required this.api});
  final HelaApi api;

  @override
  State<AddBeneficiaryScreen> createState() => _AddBeneficiaryScreenState();
}

class _AddBeneficiaryScreenState extends State<AddBeneficiaryScreen> {
  final _name = TextEditingController();
  final _phone = TextEditingController();
  final _relationship = TextEditingController();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Family member')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        const InfoBanner('They will get an SMS for each booking and must confirm before any provider is contacted. '
            'Only add adults who can agree for themselves.'),
        TextField(controller: _name, decoration: const InputDecoration(labelText: 'Their name')),
        TextField(controller: _phone, keyboardType: TextInputType.phone, decoration: const InputDecoration(labelText: 'Their mobile (+94…)')),
        TextField(controller: _relationship, decoration: const InputDecoration(labelText: 'Relationship (e.g. parent)')),
        ActionButton(
          label: 'Save',
          onPressed: () async {
            await widget.api.post('/v1/beneficiaries',
                {'name': _name.text.trim(), 'phone': _phone.text.trim(), 'relationship': _relationship.text.trim()});
            if (context.mounted) Navigator.of(context).pop();
          },
        ),
      ]),
    );
  }
}

class NotificationsScreen extends StatelessWidget {
  const NotificationsScreen({super.key, required this.api});
  final HelaApi api;

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('Messages')),
        body: SingleChildScrollView(child: AsyncSection<List<dynamic>>(
          load: () async => await api.get('/v1/me/notifications') as List,
          builder: (context, list, _) => Column(children: [
            for (final n in list.cast<Map<String, dynamic>>())
              StatusTile(
                title: Text((n['template_key'] as String).replaceAll('.', ' · ')),
                subtitle: Text(formatLocal(n['created_at'] as String)),
                status: n['status'] as String,
              ),
          ]),
        ))
      );
}
