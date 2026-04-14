# Execution Board (Operațional)

Acest board transformă strategia BladeEye Evolution în task-uri executabile și urmărite prin API.

## Endpoint-uri

- `GET /api/execution-board` — returnează board-ul curent.
- `PATCH /api/execution-board/tasks/{task_id}` — actualizează `status`, `owner`, `notes`.

## Statusuri permise

- `todo`
- `in_progress`
- `blocked`
- `done`

## Exemplu

```bash
curl -s http://127.0.0.1:8000/api/execution-board | jq '.tasks[0]'

curl -X PATCH http://127.0.0.1:8000/api/execution-board/tasks/F1-T1 \
  -H 'Content-Type: application/json' \
  -d '{"status":"in_progress","owner":"rf-core","notes":"USB discovery merged"}'
```

## Persistență

Board-ul este salvat în `sessions/execution_board.json` și este bootstrap-uit automat cu task-urile implicite dacă fișierul lipsește sau este invalid.
