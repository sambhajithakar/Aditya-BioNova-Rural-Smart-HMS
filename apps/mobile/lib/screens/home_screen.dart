/// Role-aware home shell.
///
/// One binary serves both apps the blueprint asks for: the **Staff app**
/// (register → queue → billing) and the **Doctor app** (queue → Fast Rx),
/// switched by the role the server reports on `/auth/me`. Queue and Sync
/// are common to both.
library;

import 'package:flutter/material.dart';

import '../main.dart';
import 'billing_tab.dart';
import 'login_screen.dart';
import 'queue_tab.dart';
import 'register_tab.dart';
import 'rx_tab.dart';
import 'sync_tab.dart';

/// Roles allowed to write prescriptions (mirrors `prescription.sign` /
/// `prescription.write` on the server).
const Set<String> kPrescribingRoles = {'doctor', 'owner', 'admin'};

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.graph});

  final AppGraph graph;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int _index = 0;
  String? _role;

  @override
  void initState() {
    super.initState();
    _loadRole();
  }

  bool get _canPrescribe =>
      _role != null && kPrescribingRoles.contains(_role);

  Future<void> _loadRole() async {
    try {
      final me = await widget.graph.client.me();
      if (!mounted) return;
      setState(() => _role = (me['role'] as String?) ?? 'member');
    } catch (_) {
      // Offline: the shell still works; role shows once we sync.
      if (!mounted) return;
      setState(() => _role = 'offline');
    }
  }

  Future<void> _signOut() async {
    await widget.graph.session.clear();
    if (!mounted) return;
    Navigator.of(context).pushReplacement(
      MaterialPageRoute<void>(
        builder: (_) => LoginScreen(graph: widget.graph),
      ),
    );
  }

  List<Widget> get _tabs => [
        QueueTab(graph: widget.graph),
        RegisterTab(graph: widget.graph),
        if (_canPrescribe) RxTab(graph: widget.graph),
        BillingTab(graph: widget.graph),
        SyncTab(graph: widget.graph),
      ];

  List<NavigationDestination> get _destinations => [
        const NavigationDestination(
          icon: Icon(Icons.queue_outlined),
          label: 'Queue',
        ),
        const NavigationDestination(
          icon: Icon(Icons.person_add_outlined),
          label: 'Register',
        ),
        if (_canPrescribe)
          const NavigationDestination(
            icon: Icon(Icons.medication_outlined),
            label: 'Rx',
          ),
        const NavigationDestination(
          icon: Icon(Icons.receipt_long_outlined),
          label: 'Billing',
        ),
        const NavigationDestination(
          icon: Icon(Icons.sync_outlined),
          label: 'Sync',
        ),
      ];

  @override
  Widget build(BuildContext context) {
    final tabs = _tabs;
    final index = _index < tabs.length ? _index : 0;
    return Scaffold(
      appBar: AppBar(
        title: Text('HMS · ${_role ?? '…'}'),
        actions: [
          IconButton(
            key: const Key('sign-out'),
            tooltip: 'Sign out',
            icon: const Icon(Icons.logout),
            onPressed: () => _signOut(),
          ),
        ],
      ),
      body: IndexedStack(
        index: index,
        children: tabs,
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: _destinations,
      ),
    );
  }
}
