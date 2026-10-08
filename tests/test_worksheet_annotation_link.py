"""Worksheets linked to answer annotations (M2 -> M1). Every value is synthetic.

Rule: a worksheet subject of kind ``annotation`` must name an ``annotation_id`` present
in the same answers. It is checked on every save and every submission, exactly like a
defect link, so removing an annotation that a worksheet still links to is refused
rather than silently unlinked or flagged later.
"""
import json

import pytest

from evidence_review import FileStore, answer_range, export_json
from evidence_review.contracts import AnswerAnnotation
from evidence_review.store import subject_ids, validate_answers
from test_reviewer_worksheet import answers, revenue_worksheet, worksheet_bundle

PHRASE = "Revenue fell 4.2%."


def annotation(b, ident="ann:revenue"):
    text = b.fields[0].text
    start = text.index(PHRASE)
    return AnswerAnnotation(annotation_id=ident, answer_range=answer_range(b, "summary", start, start + len(PHRASE)),
                            disposition="cannot_verify", reason="Prior-year revenue is not stated in prose.")


def linked(b, *annotations, ident="ann:revenue"):
    out = answers(b, revenue_worksheet(b, subject={"kind": "annotation", "id": ident}))
    out["annotations"] = [a.model_dump(mode="json") for a in annotations]
    return out


def test_subject_ids_list_the_answers_annotations():
    b = worksheet_bundle()
    a = annotation(b)
    assert subject_ids(b, [], [a])["annotation"] == {"ann:revenue"}
    assert subject_ids(b, [], [a.model_dump(mode="json")])["annotation"] == {"ann:revenue"}
    assert subject_ids(b, [])["annotation"] == set()


def test_worksheet_may_link_an_annotation_in_the_same_answers(tmp_path):
    b = worksheet_bundle()
    store = FileStore(tmp_path)
    store.register(b)
    draft = store.save_snapshot(b, 0, "draft", linked(b, annotation(b)), "Ada")
    assert draft["answers"]["worksheets"][0]["subject"] == {"kind": "annotation", "id": "ann:revenue"}
    state = store.save_submission(b, 1, "submit", linked(b, annotation(b)), "Ada")
    exported = json.loads(export_json(store, b.bundle_id, state["last_submission"]))
    assert exported["worksheets"][0]["subject"] == {"kind": "annotation", "id": "ann:revenue"}
    assert exported["annotations"][0]["annotation_id"] == "ann:revenue"
    assert FileStore(tmp_path).export_submission(b.bundle_id, state["last_submission"]).model_dump(mode="json") == exported


@pytest.mark.parametrize("complete", [False, True])
def test_dangling_annotation_link_is_rejected_in_drafts_and_submissions(complete):
    b = worksheet_bundle()
    with pytest.raises(ValueError, match="unknown worksheet subject: annotation ann:missing"):
        validate_answers(b, linked(b, annotation(b), ident="ann:missing"), complete=complete)
    with pytest.raises(ValueError, match="unknown worksheet subject: annotation ann:revenue"):
        validate_answers(b, linked(b), complete=complete)


def test_removing_a_linked_annotation_is_refused_until_the_link_is_dropped(tmp_path):
    b = worksheet_bundle()
    store = FileStore(tmp_path)
    store.register(b)
    store.save_snapshot(b, 0, "linked", linked(b, annotation(b)), "Ada")
    for save in (lambda key, a: store.save_snapshot(b, 1, key, a, "Ada"),
                 lambda key, a: store.save_submission(b, 1, key, a, "Ada")):
        with pytest.raises(ValueError, match="unknown worksheet subject: annotation ann:revenue"):
            save("remove-" + str(id(save)), linked(b))
    # Nothing was written: the annotation and its worksheet link survive.
    kept = FileStore(tmp_path).load_task(b.bundle_id)
    assert kept["revision"] == 1 and kept["answers"]["annotations"][0]["annotation_id"] == "ann:revenue"
    # Unlinking the worksheet first makes the removal acceptable; the calculation is kept.
    unlinked = answers(b, revenue_worksheet(b, subject={"kind": "claim", "id": "revenue"}))
    state = store.save_snapshot(b, 1, "unlinked", unlinked, "Ada")
    assert "annotations" not in state["answers"]
    assert state["answers"]["worksheets"][0]["formula"] == revenue_worksheet(b)["formula"]
