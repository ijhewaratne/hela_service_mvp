import 'package:flutter/material.dart';
import 'package:hela_core/hela_core.dart';
import 'package:url_launcher/url_launcher.dart';

/// A journey selection is a UI choice, never a change to the authenticated user's permissions.
/// Admin credentials and provider eligibility are independently validated by the API.
enum HelaRole { customer, provider, recipient, administrator }

class HelaRoleChooser extends StatelessWidget {
  const HelaRoleChooser({super.key, required this.onSelect});
  final ValueChanged<HelaRole> onSelect;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 24, 20, 32),
          children: [
            Semantics(
              header: true,
              child: Text('Welcome to Hela Service',
                  style: theme.textTheme.headlineMedium?.copyWith(fontWeight: FontWeight.bold)),
            ),
            const SizedBox(height: 8),
            Text('How would you like to use Hela today?', style: theme.textTheme.titleMedium),
            const SizedBox(height: 16),
            const AppNotices(),
            _RoleCard(
              key: const Key('role-customer'),
              title: 'Book a service',
              subtitle: 'Get help with household tasks for yourself or a family member.',
              icon: Icons.home_outlined,
              onTap: () => onSelect(HelaRole.customer),
            ),
            _RoleCard(
              key: const Key('role-provider'),
              title: 'Offer my services',
              subtitle: 'Apply to become a provider or manage approved jobs.',
              icon: Icons.handyman_outlined,
              onTap: () => onSelect(HelaRole.provider),
            ),
            _RoleCard(
              key: const Key('role-recipient'),
              title: 'I am receiving a service',
              subtitle: 'Understand how to confirm a booking arranged for you.',
              icon: Icons.volunteer_activism_outlined,
              onTap: () => onSelect(HelaRole.recipient),
            ),
            const SizedBox(height: 12),
            Text('For Hela staff', style: theme.textTheme.labelLarge),
            _RoleCard(
              key: const Key('role-admin'),
              title: 'Administrator',
              subtitle: 'Access the separate, protected Hela Operations web console.',
              icon: Icons.admin_panel_settings_outlined,
              onTap: () => onSelect(HelaRole.administrator),
            ),
          ],
        ),
      ),
    );
  }
}

class _RoleCard extends StatelessWidget {
  const _RoleCard({super.key, required this.title, required this.subtitle, required this.icon, required this.onTap});
  final String title;
  final String subtitle;
  final IconData icon;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(12),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 18),
          child: Row(
            children: [
              Icon(icon, size: 28, color: Theme.of(context).colorScheme.primary),
              const SizedBox(width: 14),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(title, style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: 4),
                  Text(subtitle, style: Theme.of(context).textTheme.bodySmall, softWrap: true),
                ]),
              ),
              const SizedBox(width: 4),
              const Icon(Icons.chevron_right),
            ],
          ),
        ),
      ),
    );
  }
}

/// Both sign-in and registration verify ownership of the same phone number.
/// A new number is *automatically* registered by the current backend after OTP verification.
class HelaAuthChoice extends StatelessWidget {
  const HelaAuthChoice({super.key, required this.role, required this.onSignIn,
    required this.onCreateAccount, required this.onBack});
  final HelaRole role;
  final VoidCallback onSignIn;
  final VoidCallback onCreateAccount;
  final VoidCallback onBack;

  @override
  Widget build(BuildContext context) {
    final description = role == HelaRole.provider ? 'service provider' : 'customer';
    return Scaffold(
      appBar: AppBar(title: Text('Continue as a $description')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(24),
          children: [
            const ScreenTitle('Welcome'),
            const SizedBox(height: 12),
            Text('Sign in to continue, or create a Hela account with your mobile number.',
                style: Theme.of(context).textTheme.bodyLarge),
            const SizedBox(height: 24),
            FilledButton(
              key: const Key('entry-sign-in'),
              onPressed: onSignIn,
              child: const Padding(padding: EdgeInsets.all(12), child: Text('Sign in')),
            ),
            const SizedBox(height: 12),
            OutlinedButton(
              key: const Key('entry-create-account'),
              onPressed: onCreateAccount,
              child: const Padding(padding: EdgeInsets.all(12), child: Text('Create account')),
            ),
            const SizedBox(height: 16),
            const InfoBanner('No password is needed for customer or provider access. '
                'We verify your mobile number with a one-time SMS code. '
                'If that number has never been used before, verification creates a customer account.'),
            if (role == HelaRole.provider)
              const InfoBanner('Registering an account does not authorize you to work. '
                  'You must submit a separate provider application and pass human verification.'),
            const SizedBox(height: 8),
            TextButton.icon(onPressed: onBack,
                icon: const Icon(Icons.arrow_back), label: const Text('Choose a different role')),
          ],
        ),
      ),
    );
  }
}

/// A beneficiary is not silently enrolled as a customer or provider.
/// The current system requires their independent SMS approval before dispatch.
class HelaRecipientInfo extends StatelessWidget {
  const HelaRecipientInfo({super.key, required this.onBack, required this.onBook});
  final VoidCallback onBack;
  final VoidCallback onBook;

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Receiving a service')),
    body: SafeArea(child: ListView(padding: const EdgeInsets.all(24), children: [
      const ScreenTitle('Someone booked help for you?'),
      const SizedBox(height: 12),
      const Text('The person booking adds your contact details. You receive a one-time SMS '
          'request to approve or decline that particular service before any provider is contacted.'),
      const SizedBox(height: 12),
      const InfoBanner('There is currently no separate service-recipient account or password. '
          'Do not share a sign-in OTP or check-in code with an unverified caller. '
          'If no SMS arrives, ask the person who booked to check the request status.'),
      const SizedBox(height: 18),
      FilledButton(key: const Key('recipient-book-too'), onPressed: onBook,
          child: const Text('I also want to book a service')),
      TextButton(key: const Key('recipient-back'), onPressed: onBack, child: const Text('Back to roles')),
    ])),
  );
}

/// Admin authentication is password + TOTP in a separate web portal and must never
/// be granted by a choice in a consumer application.
class HelaAdminInfo extends StatelessWidget {
  const HelaAdminInfo({super.key, required this.onBack, this.portalUrl = ''});
  final VoidCallback onBack;
  final String portalUrl;

  Uri? get _approvedPortal {
    final uri = Uri.tryParse(portalUrl);
    if (uri == null || uri.scheme != 'https' || uri.host.isEmpty ||
        uri.userInfo.isNotEmpty || uri.hasFragment) return null;
    return uri;
  }

  @override
  Widget build(BuildContext context) {
    final portal = _approvedPortal;
    return Scaffold(
      appBar: AppBar(title: const Text('Hela Operations')),
      body: SafeArea(child: ListView(padding: const EdgeInsets.all(24), children: [
        const ScreenTitle('Administrator access'),
        const SizedBox(height: 16),
        const Text('Administrators use a separate secure web console with an '
            'issued staff account, password, authenticator code, and server-managed permissions.'),
        const SizedBox(height: 12),
        const InfoBanner('Selecting this option does not create an administrator account or grant privileges. '
            'A designated system administrator must provision access.'),
        const SizedBox(height: 16),
        if (portal != null)
          FilledButton.icon(
            key: const Key('open-admin-portal'),
            icon: const Icon(Icons.open_in_browser),
            onPressed: () async {
              final opened = await launchUrl(portal, mode: LaunchMode.externalApplication);
              if (!opened && context.mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text('Could not open the administrator portal.')));
              }
            },
            label: const Text('Open administrator sign-in'),
          )
        else
          const InfoBanner('The administrator portal URL has not been configured for this build. '
              'Contact your organization’s system administrator for the approved web address.'),
        const SizedBox(height: 16),
        TextButton(key: const Key('admin-back'), onPressed: onBack,
            child: const Text('Back to roles')),
      ])),
    );
  }
}

/// Completes an initial customer profile after a new phone number is verified.
/// Provider legal-name/ID collection remains in the provider review workflow.
class HelaProfileSetup extends StatefulWidget {
  const HelaProfileSetup({super.key, required this.api, required this.onComplete});
  final HelaApi api;
  final VoidCallback onComplete;

  @override
  State<HelaProfileSetup> createState() => _HelaProfileSetupState();
}

class _HelaProfileSetupState extends State<HelaProfileSetup> {
  final _name = TextEditingController();
  bool _busy = false;
  Object? _error;

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final value = _name.text.trim();
    if (value.length < 2 || _busy) return;
    setState(() { _busy = true; _error = null; });
    try {
      await widget.api.patch('/v1/me', {'display_name': value});
      if (mounted) widget.onComplete();
    } catch (e) {
      if (mounted) setState(() => _error = e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Set up your account')),
    body: SafeArea(child: ListView(padding: const EdgeInsets.all(24), children: [
      const ScreenTitle('Your Hela account'),
      const SizedBox(height: 12),
      const InfoBanner('Your phone number was verified. If it was new, the system created '
          'a customer account. Provider eligibility is reviewed separately.'),
      const SizedBox(height: 18),
      TextField(
        key: const Key('setup-display-name'),
        controller: _name,
        maxLength: 80,
        textCapitalization: TextCapitalization.words,
        onChanged: (_) => setState(() {}),
        decoration: const InputDecoration(
          labelText: 'Name to display',
          helperText: 'Provider legal identity is checked separately during application.',
          border: OutlineInputBorder(),
        ),
      ),
      const SizedBox(height: 12),
      FilledButton(
        key: const Key('setup-save'),
        onPressed: _name.text.trim().length >= 2 && !_busy ? _save : null,
        child: const Padding(padding: EdgeInsets.all(12), child: Text('Save and continue')),
      ),
      const SizedBox(height: 8),
      TextButton(key: const Key('setup-skip'), onPressed: _busy ? null : widget.onComplete,
          child: const Text('Do this later')),
      ErrorBanner(_error),
    ])),
  );
}
