"""Protect complete numeric accounting and source integrity, not semantic truth."""
import copy
import pytest
from evidence_review import validate_bundle
from evidence_review.example import example_bundle
from evidence_review.evidence import numeric_spans, calculate, passage


def test_numbers_keep_repeated_offsets_and_unicode():
    spans = numeric_spans('summary', '£5.2m fell -3% from 5.2m in 2026; 10–12 bps, ID42.')
    assert [s.text for s in spans] == ['£5.2m', '-3%', '5.2m', '2026', '10–12 bps', '42']
    assert len({s.span_id for s in spans}) == 6
    assert spans[-1].state == 'identifier'


def test_validation_rejects_source_changes_and_unaccounted_prose():
    bundle = example_bundle().model_dump(mode='json')
    bundle['sources'][0]['text'] += 'changed'
    with pytest.raises(ValueError, match='hash'):
        validate_bundle(bundle)
    bundle = example_bundle().model_dump(mode='json')
    bundle['fields'][0]['text'] += ' Extra 99%.'
    with pytest.raises(ValueError, match='numeric inventory'):
        validate_bundle(bundle)


def test_citation_bounds_are_not_clamped():
    b = example_bundle()
    with pytest.raises(ValueError, match='bounds'):
        passage(b.sources[0], 1, 999)
    view = passage(b.sources[0], 4, 4)
    assert '2025' in view['text']
    assert 'Period' in view['context']
    assert 'Footnote' in view['full_text']


def test_decimal_recomputation_does_not_certify_support():
    result = calculate('(current-prior)*100', {'current':'24.6', 'prior':'22.8'}, '180', '0.01')
    assert result['result'] == '180.0'
    assert result['status'] == 'match'
    assert result['evidence_verified'] is False
    assert calculate('unknown+1', {}, '2', '0')['status'] == 'unresolved'
    assert calculate('__import__("os")', {}, '2', '0')['status'] == 'unresolved'


def test_second_consumer_shape_and_unknown_claim_link():
    b = example_bundle().model_dump(mode='json')
    b['bundle_id'] = 'different-task'
    b['task_kind'] = 'reference'
    b['claims'] = []
    for field in b['fields']: field['claim_ids'] = []
    for span in b['spans']:
        span['claim_ids'] = []
        if span['state'] not in ('identifier', 'uncited'):
            span['state'] = 'uncited'
    assert validate_bundle(b).task_kind == 'reference'
    b['spans'][0]['claim_ids'] = ['missing']
    with pytest.raises(ValueError, match='unknown claim'):
        validate_bundle(b)
