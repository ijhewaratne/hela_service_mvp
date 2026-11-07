/// Display helpers. Instants are stored and sent as UTC; display uses Asia/Colombo (UTC+05:30, no DST) per §10.4.
const _colombo = Duration(hours: 5, minutes: 30);

DateTime toColombo(DateTime utc) => utc.toUtc().add(_colombo);

String two(int n) => n.toString().padLeft(2, '0');

String formatLocal(String? iso) {
  if (iso == null) return '—';
  final d = toColombo(DateTime.parse(iso));
  return '${d.year}-${two(d.month)}-${two(d.day)} ${two(d.hour)}:${two(d.minute)}';
}

/// Integer minor units (cents) -> "LKR 2,400.00" (Q29).
String lkr(num? minor) {
  if (minor == null) return '—';
  final whole = (minor ~/ 100).toString().replaceAllMapped(RegExp(r'\B(?=(\d{3})+(?!\d))'), (_) => ',');
  return 'LKR $whole.${two((minor % 100).toInt())}';
}

/// A wall-clock time in Colombo -> UTC ISO-8601 for the API.
String colomboToUtcIso(DateTime localWallClock) =>
    DateTime.utc(localWallClock.year, localWallClock.month, localWallClock.day, localWallClock.hour, localWallClock.minute)
        .subtract(_colombo)
        .toIso8601String();
