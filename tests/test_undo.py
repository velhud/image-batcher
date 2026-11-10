from app.models import SessionData, GlobalSettings, RowData
from app.undo_manager import UndoManager


def test_undo_does_not_reopen_a_consumed_generation_receipt():
    from app.models import GlobalSettings, ImageResult, RowData, RowStatus
    session = SessionData('session', GlobalSettings(), [RowData('row', prompt='before')])
    manager = UndoManager()
    manager.push(session)
    session.rows[0].prompt = 'after'
    manager.push(session)
    session.rows[0].images = [ImageResult('image', 'row', '/tmp/result.png')]
    session.rows[0].applied_job_ids = ['durable-job']
    session.rows[0].status = RowStatus.COMPLETED
    restored = manager.undo(session)
    assert restored.rows[0].prompt == 'before'
    assert restored.rows[0].images == []
    assert restored.rows[0].applied_job_ids == ['durable-job']
    redone = manager.redo(restored)
    assert redone.rows[0].applied_job_ids == ['durable-job']


def test_undo_marks_historical_active_state_interrupted():
    from app.models import GlobalSettings, RowData, RowStatus
    session = SessionData('session', GlobalSettings(), [RowData('row', prompt='demo', status=RowStatus.GENERATING)])
    manager = UndoManager()
    manager.push(session)
    session.rows[0].status = RowStatus.COMPLETED
    manager.push(session)
    restored = manager.undo(session)
    assert restored.rows[0].status == RowStatus.INTERRUPTED


def test_undo_redo_roundtrip():
    session = SessionData(id="1", global_settings=GlobalSettings(), rows=[])
    mgr = UndoManager()
    mgr.push(session)
    session.rows.append(RowData(id="r1"))
    undoed = mgr.undo()
    assert isinstance(undoed, SessionData)
    redone = mgr.redo()
    assert isinstance(redone, SessionData)
