/// Secure sign-in: email + password (+ optional clinic slug), exactly the
/// `POST /auth/login` contract of the API. Failures show the server's
/// `detail` verbatim; success routes to the role-aware home shell.
library;

import 'package:flutter/material.dart';

import '../main.dart';
import 'home_screen.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, required this.graph});

  final AppGraph graph;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _slug = TextEditingController();
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    _email.dispose();
    _password.dispose();
    _slug.dispose();
    super.dispose();
  }

  Future<void> _signIn() async {
    setState(() {
      _error = null;
      _busy = true;
    });
    try {
      await widget.graph.client.login(
        email: _email.text.trim(),
        password: _password.text,
        organizationSlug: _slug.text.trim(),
      );
      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => HomeScreen(graph: widget.graph),
        ),
      );
    } catch (err) {
      setState(() => _error = err.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 420),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  'Aditya BioNova Smart HMS',
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.headlineSmall,
                ),
                const SizedBox(height: 4),
                Text(
                  'Doctor & field staff app',
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
                const SizedBox(height: 24),
                TextField(
                  key: const Key('login-email'),
                  controller: _email,
                  keyboardType: TextInputType.emailAddress,
                  autofillHints: const [AutofillHints.email],
                  decoration: const InputDecoration(labelText: 'Email'),
                ),
                const SizedBox(height: 12),
                TextField(
                  key: const Key('login-password'),
                  controller: _password,
                  obscureText: true,
                  autofillHints: const [AutofillHints.password],
                  decoration: const InputDecoration(labelText: 'Password'),
                  onSubmitted: (_) => _signIn(),
                ),
                const SizedBox(height: 12),
                TextField(
                  key: const Key('login-slug'),
                  controller: _slug,
                  decoration: const InputDecoration(
                    labelText: 'Clinic slug (optional)',
                  ),
                ),
                const SizedBox(height: 20),
                if (_error != null)
                  Text(
                    _error!,
                    key: const Key('login-error'),
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                const SizedBox(height: 8),
                FilledButton(
                  key: const Key('login-submit'),
                  onPressed: _busy ? null : _signIn,
                  child: Text(_busy ? 'Signing in…' : 'Sign in'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
