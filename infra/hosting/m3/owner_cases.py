"""Credential-free exact owner Cilium rule emission for upstream sanitizer."""
import json
from owner_prepare import policies, emit

inputs = {'ownerId': '11111111-1111-4111-8111-111111111111',
          'stateId': '22222222-2222-4222-8222-222222222222',
          'providerHosts': ['auth.example.invalid', 'model.example.invalid'],
          'procedureRef': 'synthetic-not-operating-input'}
rules = [value['spec'] for value in policies(inputs)['items']]
rules.append(emit(__import__('test_owner_prepare').snapshot.__wrapped__(), inputs, 'init-policy')['spec'])
print(json.dumps([{'name': 'owner-runner-and-api-bridge-init-deny', 'rules': rules, 'expectedError': ''}]))
