
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




