"""Read-only review counterexamples; all child effects are local fixture writes."""
import json
from pathlib import Path

import pytest

from ihav_competitor_search import cli
from ihav_competitor_search.chatbots import WebChatChild, ask, collect
from ihav_competitor_search.homepages import confirm_homepage
from ihav_competitor_search.manual import import_answer
from ihav_competitor_search.survey import create_run
from ihav_competitor_search.verification import record_cell_evidence

CANDIDATE = Path(__file__).resolve().parents[1]
FAKE = CANDIDATE / 'tests/fixtures/fake_web_chat.py'


def answer(name):
    return json.dumps({'columns': [], 'candidates': [
        {'name': name, 'homepage': f'https://{name.lower()}.example', 'values': {}}
    ]})


def configure(project, **values):
    (project / 'fake-chat-config.json').write_text(json.dumps(values))


def sends(project):
    path = project / 'chat-sends.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def context_names(project):
    return {item['name'] for item in json.loads(sends(project)[-1]['prompt'].splitlines()[-1])['candidates']}


def invoke_round(project, run, number):
    return cli.main(['--project', str(project), 'ask', run.name,
                     '--web-chat', str(FAKE), '--round', str(number), '--opt-in'])


def test_manual_replacement_after_preview_must_not_dispatch_stale_context(tmp_path, monkeypatch):
    run = create_run(tmp_path, 'OCR', run_id='manual-race', providers=['chatgpt'])
    import_answer(run, 1, 'manual', answer('Alpha'))
    original_prepare = cli.prepare

    def replace_between_preview_and_lock(*args, **kwargs):
        result = original_prepare(*args, **kwargs)
        # A second valid CLI writer completed before ask takes the lock.
        import_answer(run, 1, 'manual', answer('Beta'), replace=True)
        return result

    monkeypatch.setattr(cli, 'prepare', replace_between_preview_and_lock)
    rc = invoke_round(tmp_path, run, 2)
    current_names = {row['cells']['name']['value'] for row in cli.synthesize(run, through_round=1)[0]['rows']}
    assert current_names == {'Beta'}
    # Recompute after acquiring the lock or refuse the stale launch.
    assert rc != 0 or context_names(tmp_path) == current_names


def test_collect_after_preview_must_not_dispatch_missing_provider_context(tmp_path, monkeypatch):
    run = create_run(tmp_path, 'OCR', run_id='collect-race', providers=['chatgpt', 'gemini'])
    child = WebChatChild(FAKE, tmp_path)
    answers = {'chatgpt': answer('Alpha'), 'gemini': answer('Beta')}
    configure(tmp_path, answers=answers, statuses={
        'chatgpt': {'status': 'completed'}, 'gemini': {'status': 'running'}})
    ask(run, child, opt_in=True)
    assert collect(run, child)['state'] == 'waiting'
    original_prepare = cli.prepare

    def collect_between_preview_and_lock(*args, **kwargs):
        result = original_prepare(*args, **kwargs)
        configure(tmp_path, answers=answers)
        assert collect(run, child)['state'] == 'completed'
        return result

    monkeypatch.setattr(cli, 'prepare', collect_between_preview_and_lock)
    rc = invoke_round(tmp_path, run, 2)
    current_names = {row['cells']['name']['value'] for row in cli.synthesize(run, through_round=1)[0]['rows']}
    assert current_names == {'Alpha', 'Beta'}
    assert rc != 0 or context_names(tmp_path) == current_names


def test_direct_ask_must_stop_after_automated_round_adds_nothing(tmp_path):
    run = create_run(tmp_path, 'OCR', run_id='automated-stop', providers=['chatgpt'], rounds=3)
    child = WebChatChild(FAKE, tmp_path)
    configure(tmp_path, answers={'chatgpt': answer('Alpha')})
    ask(run, child, opt_in=True)
    collect(run, child)
    ask(run, child, 2, table=cli.synthesize(run)[0])
    collect(run, child, 2)
    table = cli.synthesize(run)[0]
    assert table['rounds'][-1]['stop'] is True
    invoke_round(tmp_path, run, 3)
    assert len(sends(tmp_path)) == 2


def test_fresh_manual_replacement_is_supported(tmp_path):
    run = create_run(tmp_path, 'OCR', run_id='manual-control', providers=['chatgpt'])
    import_answer(run, 1, 'manual', answer('Alpha'))
    import_answer(run, 1, 'manual', answer('Beta'), replace=True)
    assert invoke_round(tmp_path, run, 2) == 0
    assert context_names(tmp_path) == {'Beta'}


def test_fresh_completed_multi_provider_round_is_supported(tmp_path):
    run = create_run(tmp_path, 'OCR', run_id='collect-control', providers=['chatgpt', 'gemini'])
    child = WebChatChild(FAKE, tmp_path)
    configure(tmp_path, answers={'chatgpt': answer('Alpha'), 'gemini': answer('Beta')})
    ask(run, child, opt_in=True)
    assert collect(run, child)['parsed_answers'] == 2
    assert invoke_round(tmp_path, run, 2) == 0
    assert context_names(tmp_path) == {'Alpha', 'Beta'}


def test_no_progress_manual_round_prevents_next_child_launch(tmp_path):
    run = create_run(tmp_path, 'OCR', run_id='manual-stop-control', providers=['chatgpt'], rounds=3)
    import_answer(run, 1, 'manual', answer('Alpha'))
    import_answer(run, 2, 'manual', answer('Alpha'))
    assert cli.synthesize(run)[0]['rounds'][-1]['stop'] is True
    assert invoke_round(tmp_path, run, 3) == 2
    assert sends(tmp_path) == []


def confirmed_run(project):
    run = create_run(project, 'OCR', run_id='canonical-context', providers=['chatgpt'])
    raw = {'columns': [], 'candidates': [
        {'name': name, 'homepage': f'https://{name.lower()}.example',
         'values': {'description': 'Original ' + name}} for name in ('Alpha', 'Beta')]}
    import_answer(run, 1, 'manual', json.dumps(raw))
    for row in cli.synthesize(run)[0]['rows']:
        name = row['cells']['name']['value'].lower()
        confirm_homepage(cli.synthesize(run)[0], run, row['candidate_id'],
                         f'https://{name}.example/canonical', method='official_search')
    (run / 'visits.json').write_text(json.dumps({
        'alpha.example': {'exit_code': 0, 'result': {'kind': 'estimate', 'monthly_visits': 10}},
        'beta.example': {'exit_code': 0, 'result': {'kind': 'estimate', 'monthly_visits': 20}},
    }))
    return run


def corrected_cell(run, value):
    table = cli.synthesize(run)[0]
    alpha = next(row for row in table['rows'] if row['cells']['name']['value'] == 'Alpha')
    record_cell_evidence(table, run, {
        'schema_version': 1, 'candidate_id': alpha['candidate_id'], 'column': 'description',
        'value': value, 'type': 'text', 'source_url': 'https://alpha.example/about',
        'fetched_at': '2026-10-07T00:00:00Z', 'method': 'page', 'checked_by': 'host',
        'raw_excerpt': 'Synthetic page excerpt; no live page was fetched.',
    })


def test_canonical_annotations_survive_locked_context_validation(tmp_path):
    run = confirmed_run(tmp_path)
    corrected_cell(run, 'Corrected Alpha')
    assert invoke_round(tmp_path, run, 2) == 0
    context = json.loads(sends(tmp_path)[0]['prompt'].splitlines()[-1])
    assert [row['name'] for row in context['candidates']] == ['Beta', 'Alpha']
    alpha = context['candidates'][1]
    assert alpha['homepage'] == 'https://alpha.example/canonical'
    assert alpha['values']['description'] == 'Corrected Alpha'
    assert 'Synthetic page excerpt' not in sends(tmp_path)[0]['prompt']


@pytest.mark.parametrize('change', ['homepage', 'cell', 'traffic'])
def test_changed_canonical_projection_after_preview_refuses_launch(tmp_path, monkeypatch, change):
    run = confirmed_run(tmp_path)
    original_prepare = cli.prepare
    def change_after_preview(*args, **kwargs):
        outbound = original_prepare(*args, **kwargs)
        if change == 'cell':
            corrected_cell(run, 'Changed after preview')
        elif change == 'traffic':
            records = json.loads((run / 'visits.json').read_text())
            records['alpha.example']['result']['monthly_visits'] = 30
            (run / 'visits.json').write_text(json.dumps(records))
        else:
            # Simulate another valid confirmation writer before ask.lock.
            request = json.loads((run / 'request.json').read_text())
            alpha_id = next(row['candidate_id'] for row in cli.synthesize(run)[0]['rows']
                            if row['cells']['name']['value'] == 'Alpha')
            request['homepage_decisions'][alpha_id]['source_url'] = 'https://alpha.example/updated'
            (run / 'request.json').write_text(json.dumps(request))
        return outbound
    monkeypatch.setattr(cli, 'prepare', change_after_preview)
    assert invoke_round(tmp_path, run, 2) == 2
    assert sends(tmp_path) == []
    assert not (run / 'rounds/2/launch.json').exists()
