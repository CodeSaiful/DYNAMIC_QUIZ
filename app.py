"""QuizLab backend: Flask + SQLite.

Serves the front-end from ./static and a JSON API under /api.
The server builds quizzes, keeps the answer key, enforces the timer and scores results,
so the client never receives correct answers before it answers a question.
No accounts: each browser gets an anonymous `client_id` cookie that scopes its history.
"""
import json
import os
import random
import sqlite3
import time
import uuid
from pathlib import Path

from flask import Flask, g, jsonify, make_response, request, send_from_directory

BASE = Path(__file__).parent
DB_PATH = os.environ.get("QUIZ_DB", str(BASE / "quiz.db"))
SECONDS_PER_QUESTION = 120  # 10 questions = 20 min, 15 = 30 min, 30 = 60 min
POINTS_PER_CORRECT = 10
ALLOWED_COUNTS = (10, 15, 30)
DIFFICULTIES = {"easy": 0, "medium": 1, "hard": 2}
DATA = json.loads((BASE / "questions.json").read_text(encoding="utf-8"))
TOPIC_NAMES = {t["id"]: t["name"] for t in DATA["topics"]} | {"mix": "Mixed Topics"}

app = Flask(__name__, static_folder=str(BASE / "static"), static_url_path="")


# ───────────────────────── question generation ─────────────────────────
def candidates(topic: str, difficulty: str) -> list[dict]:
    """All possible questions for a selection, each with a difficulty-distance weight `w`.

    Hand-written questions and forward fact questions rank by how close their level is to the
    chosen difficulty. Reverse-direction fact questions rank last, so a fact is only asked a
    second time when a quiz needs more questions than there are unique facts.
    """
    di = DIFFICULTIES[difficulty]
    keys = list(DATA["bank"]) if topic == "mix" else [topic]
    out = []
    for k in keys:
        for level, items in enumerate(DATA["bank"][k]):
            out += [{"k": k, "w": abs(level - di) * 10, "x": x} for x in items]
        for i, fact in enumerate(DATA["facts"][k]):
            w = abs(i // 5 - di) * 10
            out.append({"k": k, "w": w, "f": fact, "rev": False})
            out.append({"k": k, "w": w + 100, "f": fact, "rev": True})
    return out


def make_question(c: dict) -> dict | None:
    """Turn a candidate into {question, options, answer, explanation}, or None if invalid."""
    if "x" in c:
        q, *opts, expl = c["x"]  # opts[0] is the correct answer
        correct, text = opts[0], q
    else:
        term, desc = c["f"]
        others = [f for f in DATA["facts"][c["k"]] if f is not c["f"] and f != c["f"]]
        rev = c["rev"]
        idx = 1 if rev else 0
        correct = desc if rev else term
        opts = [correct] + [f[idx] for f in random.sample(others, 3)]
        text = f"What best describes {term}?" if rev else f"Which term matches this description: {desc}"
        expl = f"{term} \u2014 {desc}."
    random.shuffle(opts)
    # Validation: four distinct options, exactly one correct, non-empty text.
    if len(opts) != 4 or len(set(opts)) != 4 or opts.count(correct) != 1 or not text.strip():
        return None
    return {"question": text, "options": opts, "answer": opts.index(correct), "explanation": expl}


def build_quiz(topic: str, difficulty: str, count: int) -> list[dict]:
    ranked = sorted(random.sample(pool := candidates(topic, difficulty), len(pool)), key=lambda c: c["w"])
    chosen, seen = [], set()
    for c in ranked:
        q = make_question(c)
        if q and q["question"] not in seen:  # never repeat a question inside one quiz
            seen.add(q["question"])
            chosen.append(q)
        if len(chosen) == count:
            break
    if len(chosen) != count:
        raise ValueError("Question bank too small for this selection.")
    random.shuffle(chosen)
    for i, q in enumerate(chosen):
        q["id"] = i
    return chosen


# ───────────────────────── scoring helpers ─────────────────────────
def performance_label(p: int) -> tuple[str, str]:
    if p >= 90:
        return "Excellent", "Outstanding work!"
    if p >= 75:
        return "Great Job", "You really know your stuff."
    if p >= 50:
        return "Good Effort", "Solid progress \u2014 keep going."
    if p >= 30:
        return "Keep Practicing", "Review the answers below and try again."
    return "Try Again", "Every attempt builds knowledge."


def finalize(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    """Score a quiz once and store the result. Safe to call repeatedly (idempotent)."""
    if row["submitted"]:
        return json.loads(row["result"])
    qs, answers = json.loads(row["questions"]), json.loads(row["answers"])
    n = row["count"]
    correct = sum(1 for q in qs if answers.get(str(q["id"])) == q["answer"])
    unanswered = n - len(answers)
    percent = round(correct / n * 100)
    label, message = performance_label(percent)
    now = time.time()
    used = max(0.0, min(now, row["deadline"]) - row["created"])  # never beyond the time limit
    total = n * SECONDS_PER_QUESTION
    result = {
        "quiz_id": row["id"], "topic": row["topic"], "topic_name": TOPIC_NAMES[row["topic"]],
        "difficulty": row["difficulty"], "total": n, "correct": correct,
        "incorrect": n - correct - unanswered, "unanswered": unanswered,
        "points": correct * POINTS_PER_CORRECT, "percent": percent, "label": label, "message": message,
        "time_taken_seconds": round(used), "time_remaining_seconds": max(0, round(total - used)),
        "avg_seconds_per_question": round(used / n, 1), "auto_submitted": now >= row["deadline"],
        "completed_at": now,
        "review": [{
            "id": q["id"], "question": q["question"], "options": q["options"],
            "selected": answers.get(str(q["id"])), "correct_index": q["answer"],
            "explanation": q["explanation"],
        } for q in qs],
    }
    db.execute("UPDATE quizzes SET submitted=1, result=? WHERE id=?", (json.dumps(result), row["id"]))
    db.commit()
    return result


# ───────────────────────── database & client identity ─────────────────────────
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute(
            """CREATE TABLE IF NOT EXISTS quizzes (
                id TEXT PRIMARY KEY, client_id TEXT NOT NULL, topic TEXT, difficulty TEXT, count INTEGER,
                created REAL, deadline REAL, questions TEXT, answers TEXT DEFAULT '{}', hints TEXT DEFAULT '{}',
                submitted INTEGER DEFAULT 0, result TEXT)"""
        )
        g.db.execute("CREATE INDEX IF NOT EXISTS idx_client ON quizzes(client_id, submitted, created)")
    return g.db


@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db:
        db.close()


@app.before_request
def identify():
    g.new_client = "client_id" not in request.cookies
    g.client_id = request.cookies.get("client_id") or uuid.uuid4().hex


@app.after_request
def set_cookie(resp):
    if g.get("new_client"):
        resp.set_cookie("client_id", g.client_id, max_age=60 * 60 * 24 * 365, httponly=True, samesite="Lax")
    return resp


def err(msg: str, code: int = 400, **extra):
    return make_response(jsonify(error=msg, **extra), code)


def load_quiz(quiz_id: str):
    row = get_db().execute(
        "SELECT * FROM quizzes WHERE id=? AND client_id=?", (quiz_id, g.client_id)
    ).fetchone()
    return row


def parse_int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# ───────────────────────── API ─────────────────────────
@app.get("/api/topics")
def topics():
    return jsonify(topics=DATA["topics"] + [{"id": "mix", "name": "Mixed Topics", "icon": "\U0001F3B2"}],
                   counts=list(ALLOWED_COUNTS), difficulties=list(DIFFICULTIES),
                   seconds_per_question=SECONDS_PER_QUESTION)


@app.post("/api/quiz")
def create_quiz():
    body = request.get_json(silent=True) or {}
    topic, difficulty, count = body.get("topic"), body.get("difficulty"), parse_int(body.get("count"))
    if topic != "mix" and topic not in DATA["bank"]:
        return err("Unknown topic.")
    if difficulty not in DIFFICULTIES:
        return err("Difficulty must be easy, medium or hard.")
    if count not in ALLOWED_COUNTS:
        return err(f"Question count must be one of {list(ALLOWED_COUNTS)}.")
    try:
        qs = build_quiz(topic, difficulty, count)
    except ValueError as e:
        return err(str(e), 503)
    now, quiz_id = time.time(), uuid.uuid4().hex
    db = get_db()
    db.execute(
        "INSERT INTO quizzes (id, client_id, topic, difficulty, count, created, deadline, questions) VALUES (?,?,?,?,?,?,?,?)",
        (quiz_id, g.client_id, topic, difficulty, count, now, now + count * SECONDS_PER_QUESTION, json.dumps(qs)),
    )
    db.commit()
    return jsonify(quiz_id=quiz_id, topic=topic, difficulty=difficulty, count=count,
                   duration_seconds=count * SECONDS_PER_QUESTION, deadline=now + count * SECONDS_PER_QUESTION,
                   questions=[{"id": q["id"], "question": q["question"], "options": q["options"]} for q in qs]), 201


@app.get("/api/quiz/<quiz_id>")
def resume_quiz(quiz_id):
    """Lets the client restore an in-progress quiz after a refresh; the deadline never resets."""
    row = load_quiz(quiz_id)
    if not row:
        return err("Quiz not found.", 404)
    if row["submitted"] or time.time() >= row["deadline"]:
        return jsonify(finished=True, result=finalize(get_db(), row))
    qs, answers = json.loads(row["questions"]), json.loads(row["answers"])
    return jsonify(finished=False, quiz_id=row["id"], topic=row["topic"], difficulty=row["difficulty"],
                   count=row["count"], deadline=row["deadline"], remaining_seconds=round(row["deadline"] - time.time()),
                   hints=json.loads(row["hints"]),
                   questions=[{"id": q["id"], "question": q["question"], "options": q["options"]} for q in qs],
                   answered={k: {"selected": v, "correct_index": qs[int(k)]["answer"],
                                 "explanation": qs[int(k)]["explanation"]} for k, v in answers.items()})


def _active_quiz(quiz_id):
    """Return (row, error_response). Auto-submits and reports an expired quiz."""
    row = load_quiz(quiz_id)
    if not row:
        return None, err("Quiz not found.", 404)
    if row["submitted"]:
        return None, err("Quiz already submitted.", 409, result=json.loads(row["result"]))
    if time.time() >= row["deadline"]:
        return None, err("Time is up. The quiz was submitted automatically.", 410, result=finalize(get_db(), row))
    return row, None


@app.post("/api/quiz/<quiz_id>/hint")
def hint(quiz_id):
    row, e = _active_quiz(quiz_id)
    if e:
        return e
    qid = parse_int((request.get_json(silent=True) or {}).get("question_id"))
    qs = json.loads(row["questions"])
    if qid is None or not 0 <= qid < len(qs):
        return err("Invalid question_id.")
    answers, hints = json.loads(row["answers"]), json.loads(row["hints"])
    if str(qid) in answers:
        return err("Question already answered.", 409)
    if str(qid) not in hints:  # one hint per question; it removes a wrong option, never the answer
        hints[str(qid)] = random.choice([i for i in range(4) if i != qs[qid]["answer"]])
        get_db().execute("UPDATE quizzes SET hints=? WHERE id=?", (json.dumps(hints), quiz_id))
        get_db().commit()
    return jsonify(eliminate_index=hints[str(qid)])


@app.post("/api/quiz/<quiz_id>/answer")
def answer(quiz_id):
    row, e = _active_quiz(quiz_id)
    if e:
        return e
    body = request.get_json(silent=True) or {}
    qid, sel = parse_int(body.get("question_id")), parse_int(body.get("selected"))
    qs = json.loads(row["questions"])
    if qid is None or not 0 <= qid < len(qs) or sel is None or not 0 <= sel <= 3:
        return err("Invalid question_id or selected option.")
    answers, hints = json.loads(row["answers"]), json.loads(row["hints"])
    if str(qid) in answers:
        return err("This question is already locked.", 409)  # blocks double submissions
    if hints.get(str(qid)) == sel:
        return err("That option was removed by the hint.")
    answers[str(qid)] = sel
    get_db().execute("UPDATE quizzes SET answers=? WHERE id=?", (json.dumps(answers), quiz_id))
    get_db().commit()
    q = qs[qid]
    return jsonify(correct=sel == q["answer"], correct_index=q["answer"], explanation=q["explanation"])


@app.post("/api/quiz/<quiz_id>/submit")
def submit(quiz_id):
    row = load_quiz(quiz_id)
    if not row:
        return err("Quiz not found.", 404)
    return jsonify(finalize(get_db(), row))


@app.post("/api/quiz/<quiz_id>/exit")
def exit_quiz(quiz_id):
    """Abandon an in-progress quiz (the 'Exit Quiz' button). It is deleted and never counted in history or stats."""
    row = load_quiz(quiz_id)
    if not row:
        return err("Quiz not found.", 404)
    if row["submitted"]:
        return err("Quiz already submitted.", 409)
    db = get_db()
    db.execute("DELETE FROM quizzes WHERE id=? AND submitted=0", (quiz_id,))
    db.commit()
    return jsonify(exited=True)


@app.get("/api/history")
def history():
    rows = get_db().execute(
        "SELECT result FROM quizzes WHERE client_id=? AND submitted=1 ORDER BY created DESC LIMIT 50", (g.client_id,)
    ).fetchall()
    items = []
    for r in rows:
        res = json.loads(r["result"])
        res.pop("review", None)  # keep the list light; fetch /api/history/<id> for the full review
        items.append(res)
    return jsonify(history=items)


@app.get("/api/history/<quiz_id>")
def history_item(quiz_id):
    row = load_quiz(quiz_id)
    if not row or not row["submitted"]:
        return err("Result not found.", 404)
    return jsonify(json.loads(row["result"]))


@app.get("/api/stats")
def stats():
    rows = [json.loads(r["result"]) for r in get_db().execute(
        "SELECT result FROM quizzes WHERE client_id=? AND submitted=1 ORDER BY created DESC", (g.client_id,))]
    by_topic: dict[str, list[int]] = {}
    for r in rows:
        by_topic.setdefault(r["topic"], []).append(r["percent"])
    return jsonify(
        completed=len(rows), best_score=max((r["percent"] for r in rows), default=None),
        recent=[{k: r[k] for k in ("quiz_id", "topic_name", "difficulty", "correct", "total", "percent", "completed_at")}
                for r in rows[:5]],
        by_topic=[{"topic": k, "topic_name": TOPIC_NAMES[k], "attempts": len(v), "average_percent": round(sum(v) / len(v))}
                  for k, v in by_topic.items()],
    )


@app.delete("/api/history")
def reset_history():
    db = get_db()
    cur = db.execute("DELETE FROM quizzes WHERE client_id=?", (g.client_id,))
    db.commit()
    return jsonify(deleted=cur.rowcount)


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=True)
