import json
import socket
import struct
import threading

import pytest

from access_dns_diagnose import query, configuration
from access_diagnose_prepare import emit, DIAGNOSTIC_IMAGE
from test_access_web_prepare import package


@pytest.mark.parametrize('tcp,rcode,wrong_id', [(False, 0, False), (True, 5, False), (False, 0, True)])
def test_scoped_dns_udp_tcp_rcode_and_transaction_id(tcp, rcode, wrong_id):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM) as server:
        server.bind(('127.0.0.1', 0))
        if tcp:
            server.listen(1)
        def reply():
            if tcp:
                connection, _ = server.accept()
                with connection:
                    size = struct.unpack('!H', connection.recv(2))[0]
                    packet = connection.recv(size)
                    response = packet[:2]+struct.pack('!HHHHH', 0x8180|rcode, 1, 0, 0, 0)+packet[12:]
                    connection.sendall(struct.pack('!H', len(response))+response)
            else:
                packet, address = server.recvfrom(4096)
                response = (b'XX' if wrong_id else packet[:2])+struct.pack('!HHHHH', 0x8180|rcode, 1, 0, 0, 0)+packet[12:]
                server.sendto(response, address)
        worker = threading.Thread(target=reply, daemon=True); worker.start()
        result = query('chatgpt.com', '127.0.0.1', tcp=tcp, port=server.getsockname()[1])
        worker.join(timeout=3)
        assert result['status'] == ('failed' if wrong_id else 'reply')
        assert result['rcode'] == (None if wrong_id else rcode)
        assert '127.0.0.1' not in json.dumps(result) and 'chatgpt.com' not in json.dumps(result)


def test_only_two_names_and_configured_resolver_are_used():
    with pytest.raises(ValueError):
        query('new-host.example.invalid', '127.0.0.1')
    servers, searches, ndots = configuration('nameserver 10.0.0.10\nsearch codex-trial.svc.cluster.local svc.cluster.local\noptions ndots:5\n')
    assert servers == ['10.0.0.10'] and searches == 2 and ndots == 5


def test_ndots_contrast_changes_only_diagnostic_dns_config(package):
    snapshot, inputs, _ = package
    normal = emit(snapshot, inputs, DIAGNOSTIC_IMAGE, 'dns')
    contrast = emit(snapshot, inputs, DIAGNOSTIC_IMAGE, 'dns-ndots1')
    contrast['metadata']['name'] = normal['metadata']['name']
    value = contrast['spec']['template']['spec'].pop('dnsConfig')
    assert value == {'options': [{'name': 'ndots', 'value': '1'}]}
    assert contrast == normal
    assert normal['spec']['template']['spec']['volumes'] == []
