import pytest
from evidence_review.example import example_bundle
from evidence_review.store import FileStore
from evidence_review.hooks import Hooks, run_hook
from test_store import answer


def test_hook_is_immutable_idempotent_and_failure_stays_visible(tmp_path):
    b=example_bundle();s=FileStore(tmp_path);s.register(b)
    state=s.save_submission(b,0,'submit',answer(),'Ada')
    calls=[]
    def success(submission):
        calls.append(submission['revision'])
        with pytest.raises(TypeError):submission['assessor']='Changed'
        with pytest.raises(TypeError):submission['judgments']['report_complete']['value']=False
        return {'status':'succeeded','identifier':'record-1','reason':'stored'}
    assert run_hook(s,b.bundle_id,1,Hooks(on_submission=success))['hook']['status']=='succeeded'
    run_hook(s,b.bundle_id,1,Hooks(on_submission=success))
    assert calls==[1]
    amended=s.save_submission(b,1,'amend',answer(),'Ada',0,'revision')
    def fail(submission):raise OSError('backup unavailable')
    result=run_hook(s,b.bundle_id,2,Hooks(on_revision=fail))
    assert result['hook']['status']=='failed'
    assert 'backup unavailable' in result['hook']['reason']
    assert s.export_submission(b.bundle_id,2).complete
