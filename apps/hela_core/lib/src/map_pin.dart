import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:latlong2/latlong.dart';

import 'l10n.dart';

/// Manual map pin (FR-LOC-01, C04/P04). Tiles: OpenStreetMap for synthetic development only, with attribution;
/// a licensed tile/routing vendor is a DEC-08 decision. Tests and offline use pass [showMap] = false and get
/// coordinate fields instead, so a refused permission or no network never blocks manual entry.
class MapPinPicker extends StatefulWidget {
  const MapPinPicker({super.key, required this.onChanged, this.initial = const LatLng(7.2906, 80.6337), this.showMap = true});
  final ValueChanged<LatLng> onChanged;
  final LatLng initial;
  final bool showMap;

  @override
  State<MapPinPicker> createState() => _MapPinPickerState();
}

class _MapPinPickerState extends State<MapPinPicker> {
  late LatLng _pin = widget.initial;
  late final _lat = TextEditingController(text: widget.initial.latitude.toStringAsFixed(5));
  late final _lon = TextEditingController(text: widget.initial.longitude.toStringAsFixed(5));

  /// A tap on the map moves the pin and rewrites both fields.
  void _tapped(LatLng p) {
    _lat.text = p.latitude.toStringAsFixed(5);
    _lon.text = p.longitude.toStringAsFixed(5);
    _typed(p);
  }

  /// Typing moves the pin but never rewrites the field being typed in (the cursor would jump to the end).
  void _typed(LatLng p) {
    setState(() => _pin = p);
    widget.onChanged(p);
  }

  @override
  Widget build(BuildContext context) {
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      if (widget.showMap)
        Semantics(
          label: Strings.of(context).t('map.hint'),
          child: SizedBox(
          height: 260,
          child: ClipRRect(
            borderRadius: BorderRadius.circular(8),
            child: FlutterMap(
              options: MapOptions(initialCenter: widget.initial, initialZoom: 15, onTap: (_, p) => _tapped(p)),
              children: [
                TileLayer(urlTemplate: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', userAgentPackageName: 'lk.hela.synthetic'),
                MarkerLayer(markers: [
                  Marker(point: _pin, width: 40, height: 40, child: const Icon(Icons.location_pin, size: 40, color: Colors.red)),
                ]),
                const RichAttributionWidget(attributions: [TextSourceAttribution('OpenStreetMap contributors')]),
              ],
            ),
          ),
        )),
      Row(children: [
        Expanded(
          child: TextField(
            key: const Key('pin-lat'),
            controller: _lat,
            decoration: const InputDecoration(labelText: 'Latitude'),
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            onChanged: (v) => double.tryParse(v) != null ? _typed(LatLng(double.parse(v), _pin.longitude)) : null,
          ),
        ),
        const SizedBox(width: 12),
        Expanded(
          child: TextField(
            key: const Key('pin-lon'),
            controller: _lon,
            decoration: const InputDecoration(labelText: 'Longitude'),
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            onChanged: (v) => double.tryParse(v) != null ? _typed(LatLng(_pin.latitude, double.parse(v))) : null,
          ),
        ),
      ]),
    ]);
  }
}
