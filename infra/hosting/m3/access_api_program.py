"""Emit reviewed public diagnostic program for one stdin exec in existing API Pod."""
from pathlib import Path

root = Path(__file__).parent
network = (root/'access_network_diagnose.py').read_text()
api = (root/'access_api_diagnose.py').read_text()
print('import sys, types')
print('module = types.ModuleType("access_network_diagnose")')
print('sys.modules["access_network_diagnose"] = module')
print('exec(compile('+repr(network)+', "reviewed-network", "exec"), module.__dict__)')
print('exec(compile('+repr(api)+', "reviewed-api", "exec"), {"__name__":"__main__"})')
