<<<<<<< HEAD
# QuizLab

Quiz website with a Python (Flask + SQLite) backend.

## Run locally
```bash
pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000 (set `PORT=8000` to change the port).

## Files
- `app.py` – backend and JSON API (quiz building, timer enforcement, scoring, history)
- `questions.json` – question bank and fact tables used to build quizzes
- `static/index.html` – the website (works on its own; the Exit Quiz button returns to the home page)
- `quiz.db` – created automatically on first run (anonymous per-browser history)

## API
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/topics` | Topics, allowed counts, difficulties |
| POST | `/api/quiz` | `{topic, difficulty, count}` -> exactly `count` questions (no answers) |
| GET | `/api/quiz/<id>` | Resume an in-progress quiz (deadline never resets) |
| POST | `/api/quiz/<id>/hint` | Removes one wrong option |
| POST | `/api/quiz/<id>/answer` | Locks an answer, returns feedback |
| POST | `/api/quiz/<id>/submit` | Final score and review |
| POST | `/api/quiz/<id>/exit` | Exit Quiz: abandon without saving a result |
| GET | `/api/history`, `/api/stats` | Past attempts, best score, topic performance |
| DELETE | `/api/history` | Reset saved history |

## Time limits
10 questions = 20 minutes, 15 questions = 30 minutes, 30 questions = 60 minutes (120 seconds per question, enforced by the server). Questions can be answered in any order using the numbered buttons at the bottom of the quiz.
=======
# DYNAMIC_QUIZ
>>>>>>> 5af395f8071ae5bd0fa2b57cbc2c4949cc555f64
