import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'api_client.dart';
import 'format.dart';
import 'l10n.dart';
import 'widgets.dart';

/// "My data" (FR-ID-08, Q66): see past privacy requests, file a new one, or download your own data.
/// The export needs a sign-in within the last 15 minutes; the server enforces it.
class MyDataScreen extends StatefulWidget {
  const MyDataScreen({super.key, required this.api});
  final HelaApi api;

  @override
  State<MyDataScreen> createState() => _MyDataScreenState();
}

class _MyDataScreenState extends State<MyDataScreen> {
  String _kind = 'correction';
  final _details = TextEditingController();
  String? _export;
  int _reload = 0;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('My data and privacy')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        const InfoBanner('You can download what Hela holds about you, or ask us to correct or delete it. '
            'A person reviews every request. Some records must be kept for legal reasons.'),
        ActionButton(
          label: 'Download my data',
          onPressed: () async {
            final data = await widget.api.get('/v1/me/export');
            setState(() => _export = const JsonEncoder.withIndent('  ').convert(data));
          },
        ),
        if (_export != null) ...[
          Row(children: [
            const Expanded(child: Text('Your data (you can copy it)')),
            TextButton.icon(
              icon: const Icon(Icons.copy),
              label: const Text('Copy'),
              onPressed: () async {
                await Clipboard.setData(ClipboardData(text: _export!));
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(Strings.of(context).t('common.copied'))));
                }
              },
            ),
          ]),
          Container(
            constraints: const BoxConstraints(maxHeight: 300),
            padding: const EdgeInsets.all(8),
            color: Theme.of(context).colorScheme.surfaceContainerHighest,
            child: SingleChildScrollView(child: SelectableText(_export!, key: const Key('export-json'))),
          ),
        ],
        const Divider(height: 32),
        DropdownButtonFormField<String>(
          isExpanded: true,  // long labels ellipsise instead of overflowing narrow phones
          initialValue: _kind,
          decoration: const InputDecoration(labelText: 'What would you like us to do?'),
          items: const {
            'correction': 'Correct something',
            'deletion': 'Delete my data',
            'objection': 'Object to how my data is used',
            'other': 'Something else',
          }.entries.map((e) => DropdownMenuItem(value: e.key, child: Text(e.value))).toList(),
          onChanged: (v) => setState(() => _kind = v!),
        ),
        TextField(controller: _details, maxLength: 2000, maxLines: 3, decoration: const InputDecoration(labelText: 'Details')),
        ActionButton(
          label: 'Send request',
          onPressed: () async {
            await widget.api.post('/v1/me/privacy-requests', {
              'kind': _kind,
              if (_details.text.trim().isNotEmpty) 'details': _details.text.trim(),
            });
            _details.clear();
            setState(() => _reload++);
          },
        ),
        const SizedBox(height: 16),
        Text('Your requests', style: Theme.of(context).textTheme.titleMedium),
        AsyncSection<List<dynamic>>(
          key: ValueKey(_reload),
          load: () async => await widget.api.get('/v1/me/privacy-requests') as List,
          builder: (context, list, _) => Column(children: [
            if (list.isEmpty) const Text('None yet.'),
            for (final r in list.cast<Map<String, dynamic>>())
              StatusTile(
                title: Text((r['kind'] as String).replaceAll('_', ' ')),
                subtitle: Text('Sent ${formatLocal(r['created_at'] as String)}'),
                status: r['status'] as String,
              ),
          ]),
        ),
      ]),
    );
  }
}
