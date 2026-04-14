from fastapi.testclient import TestClient
import api


def _reset_board(tmp_path):
    api.execution_board = None
    api.EXECUTION_BOARD_FILE = tmp_path / "execution_board_test.json"


def test_execution_board_bootstraps_default(tmp_path):
    _reset_board(tmp_path)
    client = TestClient(api.app)

    resp = client.get('/api/execution-board')
    assert resp.status_code == 200
    data = resp.json()
    assert data['board_name'] == 'BladeEye Evolution Execution Board'
    assert len(data['tasks']) >= 1
    assert api.EXECUTION_BOARD_FILE.exists()


def test_execution_board_task_patch_updates_and_persists(tmp_path):
    _reset_board(tmp_path)
    client = TestClient(api.app)

    first_task_id = client.get('/api/execution-board').json()['tasks'][0]['id']

    resp = client.patch(
        f'/api/execution-board/tasks/{first_task_id}',
        json={'status': 'in_progress', 'owner': 'rf-team', 'notes': 'Kickoff complete'},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload['status'] == 'in_progress'
    assert payload['owner'] == 'rf-team'
    assert payload['notes'] == 'Kickoff complete'

    board = client.get('/api/execution-board').json()
    updated = [t for t in board['tasks'] if t['id'] == first_task_id][0]
    assert updated['status'] == 'in_progress'


def test_execution_board_rejects_invalid_status(tmp_path):
    _reset_board(tmp_path)
    client = TestClient(api.app)

    first_task_id = client.get('/api/execution-board').json()['tasks'][0]['id']
    resp = client.patch(f'/api/execution-board/tasks/{first_task_id}', json={'status': 'started'})

    assert resp.status_code == 422


def test_execution_board_returns_404_for_unknown_task(tmp_path):
    _reset_board(tmp_path)
    client = TestClient(api.app)

    client.get('/api/execution-board')
    resp = client.patch('/api/execution-board/tasks/UNKNOWN', json={'status': 'done'})

    assert resp.status_code == 404
