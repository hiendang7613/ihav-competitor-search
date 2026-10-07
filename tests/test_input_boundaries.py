"""Offline acceptance checks; failing checks expose current source defects.

No queue, browser, worker, provider call or native-room mutation is allowed.
All child launch calls are captured in memory; no installed child is needed.
"""
import json
import socket
from pathlib import Path

import pytest

from ihav_competitor_search.chatbots import WebChatChild, ask, collect, preview
from ihav_competitor_search import cli
from ihav_competitor_search.manual import import_answer
from ihav_competitor_search.merge import MAX_JSON_DEPTH, empty_table, merge_round, parse_answer

@pytest.fixture
def child_cli(tmp_path):
    # A local file is sufficient: launch is captured in memory, never executed.
    path = tmp_path / 'fake_child.py'
    path.write_text('# fixture; execution forbidden')
    return path


@pytest.fixture(autouse=True)
def deny_in_process_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('network forbidden in offline checks')
    monkeypatch.setattr(socket, 'socket', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


def raw_answer(columns=None, values=None):
    return {'columns': columns or [], 'candidates': [
        {'name': 'Fixture product', 'homepage': 'https://example.com', 'values': values or {}}
    ]}


def merge(raw):
    return merge_round(empty_table(), [{'provider': 'fixture', 'raw': raw}], 1)


@pytest.mark.parametrize('bad_type', [['number'], {'value': 'number'}])
def test_unhashable_column_type_is_one_issue_not_whole_round_failure(bad_type):
    table = merge(raw_answer(columns=[{'key': 'price', 'meaning': 'price', 'type': bad_type}]))
    assert len(table['rows']) == 1
    assert table['issues'][0]['reason'] == 'invalid_column'


def test_huge_valid_json_integer_preserves_raw_without_float_overflow():
    huge = 10 ** 400
    # Text round-trip confirms this is legal JSON, rather than a Python-only input.
    table = merge(json.dumps(raw_answer(values={'founded': huge})))
    cell = table['rows'][0]['cells']['founded']
    assert cell['value'] == huge
    assert cell['raw_value'] == huge and cell['reason'] is None


def make_run(project):
    directory = project / '.ihav_space/ihav-competitor-search/runs/fixture'
    directory.mkdir(parents=True)
    (directory / 'request.json').write_text(json.dumps({
        'domain': 'Fixture domain', 'providers': ['chatgpt'], 'options': {'rounds': 2}
    }))
    return directory


def test_parent_passes_saved_request_key_to_child(monkeypatch, tmp_path, child_cli):
    directory = make_run(tmp_path)
    child = WebChatChild(child_cli, tmp_path)
    monkeypatch.setattr(child, 'gate', lambda: {
        'version': 'fixture', 'providers': ['chatgpt'],
        'default_state_dir': str(tmp_path / 'state'),
        'contracts': {'run_lookup_admission': 1, 'launch_outcome': 1},
    })
    calls = []
    def capture(*args):
        calls.append(args)
        run_id = '20261006T000000-00000001'
        path = tmp_path / '.ihav_space/ihav-web-chat/runs' / run_id / 'request.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'schema_version': 1, 'run_id': run_id,
                                    'providers': ['chatgpt'], 'created_at': '2026-10-06',
                                    'prompt': Path(args[args.index('--prompt-file') + 1]).read_text(),
                                    'request_key': args[args.index('--request-key') + 1]}))
        return json.dumps({'run_id': run_id, 'state_dir': str(tmp_path / 'state')})
    monkeypatch.setattr(child, 'call', capture)
    launch = ask(directory, child, opt_in=True)
    assert len(calls) == 1 and calls[0][0] == 'run'
    assert '--request-key' in calls[0]
    assert calls[0][calls[0].index('--request-key') + 1] == launch['request_key']


def test_default_unsupported_providers_are_refused_without_send():
    with pytest.raises(ValueError, match='not supported'):
        preview({'domain': 'Fixture domain'}, ['chatgpt'])


def test_invalid_scalar_column_type_is_preserved_as_issue():
    table = merge(raw_answer(columns=[{'key': 'price', 'meaning': 'price', 'type': 'invalid'}]))
    assert len(table['rows']) == 1
    assert table['issues'][0]['reason'] == 'invalid_column'


def test_completed_status_remains_only_child_claim(tmp_path):
    directory = make_run(tmp_path)
    (tmp_path / 'fake-chat-config.json').write_text(json.dumps({'answers': {'chatgpt': None}}))
    child = WebChatChild(Path(__file__).parent / 'fixtures/fake_web_chat.py', tmp_path)
    ask(directory, child, opt_in=True)
    record = collect(directory, child)
    assert record['completed_answers'] == 1
    assert record['parsed_answers'] == 0 and record['state'] == 'partial'
    assert not list((directory / 'rounds/1/answers').glob('*.json'))


def test_manual_invalid_raw_is_retained_with_explicit_failure(tmp_path):
    directory = make_run(tmp_path)
    raw = 'not JSON\r\n'
    record = import_answer(directory, 1, 'fixture', raw)
    assert record['status'] == 'parse_failed' and record['raw'] == raw


def test_exponent_overflow_is_retained_as_parse_failed(tmp_path):
    directory = make_run(tmp_path)
    raw = json.dumps(raw_answer(values={'founded': 0})).replace('"founded": 0', '"founded": 1e400')
    record = import_answer(directory, 1, 'fixture', raw)
    assert record['status'] == 'parse_failed' and record['raw'] == raw


def test_dict_nonfinite_number_is_an_explicit_parse_failure():
    table = merge(raw_answer(values={'founded': float('inf')}))
    assert not table['rows']
    assert table['rounds'][0]['providers'][0]['status'] == 'parse_failed'


def test_container_depth_boundary_is_independent_of_python_recursion_limit():
    accepted = raw_answer()
    accepted['metadata'] = json.loads('[' * (MAX_JSON_DEPTH - 1) + '0' + ']' * (MAX_JSON_DEPTH - 1))
    assert parse_answer(accepted) is accepted
    rejected = raw_answer()
    rejected['metadata'] = [accepted['metadata']]
    with pytest.raises(ValueError, match='64 container levels'):
        parse_answer(rejected)


@pytest.mark.parametrize('location', ['candidate_metadata', 'invalid_column_type'])
def test_deep_manual_answer_preserves_raw_and_renders_failure(tmp_path, location):
    directory = make_run(tmp_path)
    nested = json.loads('[' * MAX_JSON_DEPTH + '0' + ']' * MAX_JSON_DEPTH)
    answer = raw_answer()
    if location == 'candidate_metadata':
        answer['candidates'][0]['metadata'] = nested
    else:
        answer['columns'] = [{'key': 'deep', 'meaning': 'deep', 'type': nested}]
    raw = json.dumps(answer)
    record = import_answer(directory, 1, 'fixture', raw)
    source = directory / 'rounds/1/answers/fixture.json'
    before = source.read_bytes()
    assert record['status'] == 'parse_failed' and record['raw'] == raw
    assert '64 container levels' in record['reason']
    assert cli.main(['--project', str(tmp_path), 'render', 'fixture']) == 0
    outcome = json.loads((directory / 'table.json').read_text())['rounds'][0]['providers'][0]
    assert outcome['status'] == 'parse_failed' and outcome['raw'] == raw
    assert source.read_bytes() == before


def test_deep_legacy_object_has_writable_failure_and_unchanged_source(tmp_path):
    directory = make_run(tmp_path)
    answer = raw_answer()
    answer['candidates'][0]['metadata'] = json.loads('[' * MAX_JSON_DEPTH + '0' + ']' * MAX_JSON_DEPTH)
    source = directory / 'rounds/1/answers/legacy.json'
    source.parent.mkdir(parents=True)
    before = json.dumps({'provider': 'legacy', 'raw': answer}).encode('utf-8')
    source.write_bytes(before)
    assert cli.main(['--project', str(tmp_path), 'render', 'fixture']) == 0
    table = json.loads((directory / 'table.json').read_text())
    outcome = table['rounds'][0]['providers'][0]
    assert outcome['status'] == 'parse_failed' and not table['rows']
    assert outcome['raw_representation'] == 'ascii_escaped_json_text'
    assert json.loads(outcome['raw']) == answer
    assert source.read_bytes() == before


@pytest.mark.parametrize('depth,rejected', [(2, False), (MAX_JSON_DEPTH + 1, True), (400, True)])
def test_legacy_tuple_arrays_obey_depth_limit_and_allow_next_round(depth, rejected):
    nested = 0
    for _ in range(depth):
        nested = (nested,)
    answer = raw_answer()
    answer['candidates'][0]['metadata'] = nested
    first = merge(answer)
    outcome = first['rounds'][0]['providers'][0]
    assert answer['candidates'][0]['metadata'] is nested
    if rejected:
        assert outcome['status'] == 'parse_failed' and not first['rows']
        assert outcome['raw_representation'] == 'ascii_escaped_json_text'
        assert json.loads(outcome['raw']) == json.loads(json.dumps(answer))
    else:
        assert outcome['status'] == 'completed' and len(first['rows']) == 1
    second = merge_round(first, [], 2)
    assert len(second['rows']) == len(first['rows'])
    json.dumps(second, ensure_ascii=False, allow_nan=False).encode('utf-8')


@pytest.mark.parametrize('value', [float('inf'), '\ud800'])
def test_rejected_legacy_object_remains_exportable_and_source_unchanged(tmp_path, value):
    directory = make_run(tmp_path)
    raw = raw_answer(values={'description': value})
    source = directory / 'rounds/1/answers/legacy.json'
    source.parent.mkdir(parents=True)
    # The loader refuses literal Infinity/NaN; exponent overflow is valid JSON
    # that reaches the legacy-object boundary and needs safe failure evidence.
    original = json.dumps({'provider': 'legacy', 'raw': raw}).replace('Infinity', '1e400').encode('utf-8')
    source.write_bytes(original)
    table, _ = cli.synthesize(directory)
    outcome = table['rounds'][0]['providers'][0]
    assert outcome['status'] == 'parse_failed' and not table['rows']
    assert outcome['raw_representation'] == 'ascii_escaped_json_text'
    assert isinstance(outcome['raw'], str)
    restored = json.loads(outcome['raw'])['candidates'][0]['values']['description']
    assert restored == value
    assert cli.main(['--project', str(tmp_path), 'render', 'fixture']) == 0
    assert source.read_bytes() == original
    json.dumps(json.loads((directory / 'table.json').read_text()), ensure_ascii=False, allow_nan=False).encode('utf-8')


@pytest.mark.parametrize('value', [float('inf'), float('nan'), '\ud800'])
def test_rejected_memory_json_object_retains_labelled_text_and_writable_failure(value):
    table = merge(raw_answer(values={'description': value}))
    outcome = table['rounds'][0]['providers'][0]
    assert outcome['status'] == 'parse_failed' and not table['rows']
    assert isinstance(outcome['raw'], str) and outcome['raw_representation'] == 'ascii_escaped_json_text'
    json.dumps(table, ensure_ascii=False, allow_nan=False).encode('utf-8')


def test_unsupported_memory_object_reports_unavailable_raw_without_accepted_facts():
    raw = raw_answer(values={'description': object()})
    table = merge(raw)
    outcome = table['rounds'][0]['providers'][0]
    assert outcome['status'] == 'parse_failed' and not table['rows']
    assert outcome['raw'] is None and outcome['raw_representation'] == 'unavailable_unserializable_object'
    assert outcome['raw_serialization_error']
    json.dumps(table, ensure_ascii=False, allow_nan=False).encode('utf-8')


@pytest.mark.parametrize('value', ['\ud800', '\udfff'])
def test_unpaired_surrogate_answer_preserves_raw_as_parse_failed(tmp_path, value):
    directory = make_run(tmp_path)
    raw = json.dumps(raw_answer(values={'description': value}))
    record = import_answer(directory, 1, 'fixture', raw)
    assert record['status'] == 'parse_failed' and record['raw'] == raw
    table, _ = cli.synthesize(directory)
    assert not table['rows']
    assert table['rounds'][0]['providers'][0]['status'] == 'parse_failed'
    assert cli.main(['--project', str(tmp_path), 'render', 'fixture']) == 0


def test_valid_utf8_answer_keeps_supported_non_ascii_values(tmp_path):
    directory = make_run(tmp_path)
    value = 'Tiếng Việt 中文 \U0001f30f'
    raw = json.dumps(raw_answer(values={'description': value}), ensure_ascii=False)
    record = import_answer(directory, 1, 'fixture', raw)
    assert record['status'] == 'completed' and record['raw'] == raw
    assert cli.synthesize(directory)[0]['rows'][0]['cells']['description']['value'] == value


def test_valid_number_and_boolean_cells_keep_supported_behavior():
    table = merge(raw_answer(values={'founded': 2000, 'open_source': True}))
    cells = table['rows'][0]['cells']
    assert cells['founded']['value'] == 2000
    assert cells['open_source']['value'] is True


@pytest.mark.parametrize('method', ['page', 'official_search'])
def test_empty_confirmation_url_is_refused_without_consuming_search(tmp_path, capsys, method):
    directory = make_run(tmp_path)
    import_answer(directory, 1, 'fixture', json.dumps(raw_answer()))
    table, _ = cli.synthesize(directory)
    candidate_id = table['rows'][0]['candidate_id']
    before = (directory / 'request.json').read_bytes()
    args = ['--project', str(tmp_path), 'confirm', 'fixture', candidate_id]
    assert cli.main(args + ['--url', '', '--method', method]) == 2
    assert (directory / 'request.json').read_bytes() == before
    capsys.readouterr()
    assert cli.main(args + ['--url', 'https://example.com', '--method', 'official_search']) == 0
    capsys.readouterr()
    assert cli.main(args + ['--url', 'https://example.com', '--method', 'official_search']) == 2


def test_empty_page_url_preserves_existing_confirmation(tmp_path, capsys):
    directory = make_run(tmp_path)
    import_answer(directory, 1, 'fixture', json.dumps(raw_answer()))
    table, _ = cli.synthesize(directory)
    candidate_id = table['rows'][0]['candidate_id']
    args = ['--project', str(tmp_path), 'confirm', 'fixture', candidate_id]
    assert cli.main(args + ['--url', 'https://example.com', '--method', 'official_search']) == 0
    capsys.readouterr()
    before = (directory / 'request.json').read_bytes()
    assert cli.main(args + ['--url', '']) == 2
    assert (directory / 'request.json').read_bytes() == before
    assert cli.synthesize(directory)[0]['rows'][0]['homepage_status'] == 'confirmed'
