"""Offline versioned source schema checks. No transport, credential access or Git effects.

Install the adjacent requirements in a disposable environment, then run this
file. Shape examples do NOT validate signatures, policy, TLS or worker safety.
"""
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator, FormatChecker


def main():
    root = Path(__file__).resolve().parent
    schema = json.loads((root / 'schemas.json').read_text())
    contract = json.loads((root / 'contract.json').read_text())
    examples = json.loads((root / 'examples.json').read_text())
    examples += json.loads((root / 'read-v2-fixtures.json').read_text())
    Draft202012Validator.check_schema(schema)
    definitions = schema['$defs']

    def check_tree(node):
        if isinstance(node, dict):
            if '$ref' in node:
                reference = node['$ref']
                assert reference.startswith('#/$defs/'), reference
                assert reference.removeprefix('#/$defs/') in definitions, reference
            if node.get('type') == 'object':
                assert node['additionalProperties'] is False
                assert set(node['required']) == set(node['properties'])
            if node.get('type') == 'array':
                assert 0 < node['maxItems'] <= 128
            for value in node.values():
                check_tree(value)
        elif isinstance(node, list):
            for value in node:
                check_tree(value)

    check_tree(schema)
    assert contract['runtimeActivation'] is False
    operations = contract['operations']
    assert len(operations) == len({o['operation'] for o in operations}) == 10
    assert len({(o['method'], o['path']) for o in operations}) == 10
    for operation in operations:
        assert type(operation['implemented']) is bool
        if operation['implemented']:
            assert operation['kind'] == 'read'
            assert operation['logicalOperation'] not in ('check_windows_history', 'execute_windows_pull')
        assert operation['defaultPolicy'] == 'DENY'
        assert operation['version'] == '2'
        assert operation['path'].startswith('/gateway/v2/')
        assert re.fullmatch('[a-z_]+_v2', operation['operation'])
        assert operation['method'] == ('GET' if operation['kind'] == 'read' else 'POST')
        for field in ('params', 'result'):
            check_tree(operation[field])
        params = definitions[operation['params']['$ref'].split('/')[-1]]
        forbidden = {'command', 'url', 'path', 'origin', 'actor', 'policy', 'approved', 'proof'}
        assert not forbidden.intersection(params['properties'])
    by_name = {o['logicalOperation']: o for o in operations}
    assert by_name['execute_windows_pull']['intendedPolicy'] == 'ASK'
    assert by_name['check_windows_history']['intendedPolicy'] == 'DENY'
    assert 'get_repository' not in {o['operation'] for o in operations}

    for example in examples:
        check = {**schema, '$ref': '#/$defs/' + example['schema']}
        validator = Draft202012Validator(check, format_checker=FormatChecker())
        actual = validator.is_valid(example['value'])
        assert actual == example['valid'], example['name']
    print(f'SOURCE_SCHEMA_VALID: {len(operations)} operations, {len(examples)} examples')
    print('TLS/ASK/signatures/Git effects/runtime: NOT_TESTED')


if __name__ == '__main__':
    main()
