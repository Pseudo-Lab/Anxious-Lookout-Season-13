import copy

import pytest

import access_node_proxy_prepare as prep
from test_access_node_proxy import bundle, replacement
from test_access_runner_dns_prepare import runner_snapshot
from test_access_web_prepare import package
from test_node_proxy_endpoint_order import populated


def revisions(b, revision):
    for endpoint in b['endpoints'].values():
        for side in ('spec', 'realized'):
            endpoint['policy'][side].update(build=revision, **{'policy-revision': revision})


def test_new_agent_local_build_alias_changes_preserve_policy_and_raw_inputs(bundle):
    b = populated(bundle); revisions(b, 19)
    baseline = prep.preflight(b)
    replacement(b); revisions(b, 9)
    before = copy.deepcopy(b)
    observed = prep.health(baseline, b, 'false', [baseline['initialAgent']['uid']])
    assert observed['uid'] == 'replacement'
    assert prep.endpoint_witnesses(b['endpoints'], 'replacement') == baseline['endpointWitnesses']
    assert b == before


@pytest.mark.parametrize('change', ['missing', 'bool', 'float', 'zero', 'negative', 'wrong_alias', 'side_mismatch', 'revision_mismatch'])
def test_build_alias_must_be_valid_and_consistent_in_each_capture(bundle, change):
    b = populated(bundle); revisions(b, 19)
    p = b['endpoints']['api']['policy']
    if change == 'missing': p['realized'].pop('build')
    elif change == 'bool': p['realized']['build'] = True
    elif change == 'float': p['realized']['build'] = 19.0
    elif change == 'zero': p['realized']['build'] = 0
    elif change == 'negative': p['realized']['build'] = -1
    elif change == 'wrong_alias': p['realized']['build'] = 9
    elif change == 'side_mismatch': p['spec'].update(build=9, **{'policy-revision': 9})
    else: p['realized']['policy-revision'] = 9
    with pytest.raises(ValueError): prep.preflight(b)


@pytest.mark.parametrize('change', ['row', 'missing_row', 'duplicate', 'identity', 'allowed', 'denied', 'cidr', 'enforcement', 'unknown_field'])
def test_valid_restart_counters_cannot_hide_substantive_policy_drift(bundle, change):
    b = populated(bundle); revisions(b, 19)
    baseline = prep.preflight(b)
    replacement(b); revisions(b, 9)
    for side in ('spec', 'realized'):
        p = b['endpoints']['api']['policy'][side]
        if change == 'row': p['l4']['egress'][0]['other-field']['preserved'] = False
        elif change == 'missing_row': p['l4']['egress'].pop()
        elif change == 'duplicate': p['l4']['egress'].append(copy.deepcopy(p['l4']['egress'][0]))
        elif change == 'identity': p['id'] = 1234
        elif change == 'allowed': p['allowed-egress-identities'].append(1234)
        elif change == 'denied': p['denied-egress-identities'] = [1234]
        elif change == 'cidr': p['cidr-policy'] = {'egress': ['192.0.2.0/24']}
        elif change == 'enforcement': p['policy-enabled'] = 'none'
        else: p['unsupported-field'] = 'changed'
    with pytest.raises(ValueError): prep.health(baseline, b, 'false', [baseline['initialAgent']['uid']])


@pytest.mark.parametrize('format_value', [None, 'legacy', 'cilium-1.20.2-stable-policy-v1'])
def test_legacy_endpoint_hash_receipts_require_full_baseline_regeneration(bundle, format_value):
    baseline = prep.preflight(bundle)
    if format_value is None: baseline.pop('endpointWitnessFormat')
    else: baseline['endpointWitnessFormat'] = format_value
    replacement(bundle)
    with pytest.raises(ValueError): prep.health(baseline, bundle, 'false', [baseline['initialAgent']['uid']])
