import copy
import json

import pytest

import access_node_proxy_prepare as prep
from test_access_node_proxy import bundle, replacement
from test_access_runner_dns_prepare import runner_snapshot
from test_access_web_prepare import package


def populated(b):
    policy = b['endpoints']['api']['policy']
    for side in ('spec', 'realized'):
        policy[side]['allowed-egress-identities'] = [11, 22]
        policy[side]['build'] = 1
        for direction, ports in (('ingress', (8080, 8443)), ('egress', (53, 443, 8080))):
            policy[side]['l4'][direction] = [
                {'rule': json.dumps({'tier': 200, 'port': port, 'protocol': 'TCP',
                                     'l7-rules': [{'selector': {'dns': [{'matchName': 'chatgpt.com'}]}}]}),
                 'derived-from-rules': [['policy=original', 'uid='+str(port)]],
                 'rules-by-selector': {'selector': [['first', 'second']]},
                 'other-field': {'preserved': True}}
                for port in ports]
    return b


@pytest.mark.parametrize('direction', ['ingress', 'egress', 'both'])
def test_only_outer_l4_order_ignored_and_raw_evidence_stays_intact(bundle, direction):
    b = populated(bundle)
    original = prep.preflight(b)
    for key in ('ingress', 'egress') if direction == 'both' else (direction,):
        b['endpoints']['api']['policy']['realized']['l4'][key].reverse()
    before = copy.deepcopy(b)
    reordered = prep.preflight(b)
    assert reordered['endpointWitnesses'] == original['endpointWitnesses']
    assert b == before
    replacement(b)
    # Baseline hashes stay stable on the fresh rollback agent despite row order.
    assert prep.health(original, b, 'false', [original['initialAgent']['uid']])['uid'] == 'replacement'


@pytest.mark.parametrize('change', ['missing', 'extra', 'duplicate_count', 'rule_port', 'rule_protocol',
    'rule_dns', 'derived_uid', 'nested_order', 'opaque', 'other_policy_field', 'identity_order',
    'revision', 'boolean_revision', 'non_list', 'non_row'])
def test_content_multiplicity_and_all_other_policy_fields_remain_exact(bundle, change):
    b = populated(bundle); p = b['endpoints']['api']['policy']
    entries = p['realized']['l4']['egress']
    if change == 'missing': entries.pop()
    elif change == 'extra': entries.append({'rule': 'foreign'})
    elif change == 'duplicate_count': entries[1] = copy.deepcopy(entries[0])
    elif change in ('rule_port', 'rule_protocol', 'rule_dns'):
        rule = json.loads(entries[0]['rule'])
        if change == 'rule_port': rule['port'] = 1234
        elif change == 'rule_protocol': rule['protocol'] = 'UDP'
        else: rule['l7-rules'][0]['selector']['dns'][0]['matchName'] = 'other.example'
        entries[0]['rule'] = json.dumps(rule)
    elif change == 'derived_uid': entries[0]['derived-from-rules'][0][1] = 'uid=foreign'
    elif change == 'nested_order': entries[0]['rules-by-selector']['selector'][0].reverse()
    elif change == 'opaque': entries[0]['other-field']['preserved'] = False
    elif change == 'other_policy_field': p['realized']['build'] = 2
    elif change == 'identity_order': p['realized']['allowed-egress-identities'].reverse()
    elif change == 'revision': p['realized']['policy-revision'] += 1
    elif change == 'boolean_revision': p['realized']['policy-revision'] = True
    elif change == 'non_list': p['realized']['l4']['egress'] = {}
    else: entries[0] = 'invalid'
    with pytest.raises(ValueError): prep.preflight(b)


def test_same_duplicate_multiset_keeps_count_in_baseline_hash(bundle):
    b = populated(bundle)
    r = prep.preflight(b)
    p = b['endpoints']['api']['policy']
    for side in ('spec', 'realized'):
        p[side]['l4']['egress'].append(copy.deepcopy(p[side]['l4']['egress'][0]))
    duplicated = prep.preflight(b)
    assert duplicated['endpointWitnesses']['api']['realizedHash'] != r['endpointWitnesses']['api']['realizedHash']
    replacement(b)
    with pytest.raises(ValueError): prep.health(r, b, 'false', [r['initialAgent']['uid']])
