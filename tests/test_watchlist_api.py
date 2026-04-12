from fastapi.testclient import TestClient
import api


def test_watchlist_crud():
    client = TestClient(api.app)
    api.watchlist.clear()

    resp = client.get('/api/watchlist')
    assert resp.status_code == 200
    assert resp.json() == []

    resp = client.post('/api/watchlist', json={'frequency': 123.0})
    assert resp.status_code == 200
    assert api.watchlist == [123.0]

    resp = client.get('/api/watchlist')
    assert resp.json() == [123.0]

    # Duplicate add should not create duplicates
    resp = client.post('/api/watchlist', json={'frequency': 123.0})
    assert resp.status_code == 200
    assert api.watchlist == [123.0]

    resp = client.delete('/api/watchlist/123.0')
    assert resp.status_code == 200
    assert api.watchlist == []
