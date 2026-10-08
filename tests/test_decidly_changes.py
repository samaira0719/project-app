"""End-to-end checks for the Decidly changes.

Runs the real FastAPI app against a throw-away SQLite file, with AI and usage
tracking switched off so nothing leaves the machine.

    python -m pytest tests -q
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="decidly-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["GEMINI_API_KEY"] = ""
os.environ["TRACKING_ENABLED"] = "false"
os.environ["SECRET_KEY"] = "test-secret"

from fastapi.testclient import TestClient  # noqa: E402

from backend import decision_tempo, scoring  # noqa: E402
from backend.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def auth(client):
    notice = client.get("/api/privacy/notice").json()
    response = client.post("/api/auth/register", json={
        "name": "Test Student",
        "email": "student@example.com",
        "password": "secret123",
        "consent": {
            "privacy": True,
            "personalization": True,
            "ai_processing": False,
            "age_confirmed": True,
            "policy_version": notice.get("version", ""),
        },
    })
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _due(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


# --------------------------------------------------------------- naming

def test_app_is_named_decidly(client):
    assert app.title == "Decidly"
    html = client.get("/").text
    assert "Decidly" in html
    assert "Decide Well" not in html


# ------------------------------------------- study error + importance

def test_study_task_requires_estimate(client, auth):
    response = client.post("/api/tasks", headers=auth, json={
        "title": "Which book should I read today? A or B",
        "category": "Study",
        "due_date": _due(0),
    })
    assert response.status_code == 422
    assert "estimated time" in response.text


def test_study_task_with_estimate_and_importance(client, auth):
    response = client.post("/api/tasks", headers=auth, json={
        "title": "Which book should I read today? A or B",
        "category": "Study",
        "due_date": _due(0),
        "estimated_minutes": 60,
        "importance": 5,
    })
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["importance"] == 5
    assert body["estimated_minutes"] == 60


def test_importance_drives_the_matrix(client, auth):
    """Without a survey, category importance alone is 0.50, so the old rule
    put everything in Delete. Stated importance must now place tasks."""
    a_lot = client.post("/api/tasks", headers=auth, json={
        "title": "Psychology or marketing?", "category": "Career",
        "due_date": _due(20), "importance": 5,
    }).json()
    a_little = client.post("/api/tasks", headers=auth, json={
        "title": "Goa or Kerala?", "category": "Travel",
        "due_date": _due(20), "importance": 1,
    }).json()
    unsaid = client.post("/api/tasks", headers=auth, json={
        "title": "Legacy task without the question", "category": "Personal",
        "due_date": _due(20),
    }).json()

    data = client.get("/api/tasks/prioritized", headers=auth).json()
    by_id = {t["id"]: t for t in data["tasks"]}
    assert by_id[a_lot["id"]]["important"] is True
    assert by_id[a_lot["id"]]["quadrant"] == "schedule"      # not urgent, important
    assert by_id[a_little["id"]]["important"] is False
    assert by_id[a_little["id"]]["quadrant"] == "eliminate"
    assert by_id[unsaid["id"]]["important"] is False          # falls back to C >= 0.70
    # The stated answer also moves the importance factor itself.
    assert by_id[a_lot["id"]]["factors"]["importance"] > by_id[unsaid["id"]]["factors"]["importance"]
    assert by_id[a_little["id"]]["factors"]["importance"] < by_id[unsaid["id"]]["factors"]["importance"]
    assert "matters a lot" in by_id[a_lot["id"]]["reason"]


def test_matrix_buckets_agree_with_tasks(client, auth):
    data = client.get("/api/tasks/prioritized", headers=auth).json()
    buckets = {q["key"]: set(q["task_ids"]) for q in data["matrix"]}
    for task in data["tasks"]:
        assert task["id"] in buckets[task["quadrant"]]


# ------------------------------------------------ edit in place (Back)

def test_edit_task_in_place(client, auth):
    task = client.post("/api/tasks", headers=auth, json={
        "title": "Laptop or tablet", "category": "Purchases",
        "due_date": _due(5), "importance": 3,
    }).json()

    edited = client.patch(f"/api/tasks/{task['id']}", headers=auth, json={
        "title": "Laptop or tablet for uni?", "importance": 5,
    })
    assert edited.status_code == 200, edited.text
    assert edited.json()["title"] == "Laptop or tablet for uni?"
    assert edited.json()["importance"] == 5
    assert edited.json()["category"] == "Purchases"   # untouched fields kept

    # Switching to Study without an estimate hits the same rule as creation.
    bad = client.patch(f"/api/tasks/{task['id']}", headers=auth, json={"category": "Study"})
    assert bad.status_code == 422
    assert "estimated time" in bad.text

    good = client.patch(f"/api/tasks/{task['id']}", headers=auth,
                        json={"category": "Study", "estimated_minutes": 30})
    assert good.status_code == 200, good.text
    assert good.json()["estimated_minutes"] == 30

    # And back to a non-Study category drops the estimate again.
    back = client.patch(f"/api/tasks/{task['id']}", headers=auth, json={"category": "Travel"})
    assert back.status_code == 200
    assert back.json()["estimated_minutes"] is None


def test_edit_rejects_other_users_task(client, auth):
    assert client.patch("/api/tasks/999999", headers=auth, json={"title": "x"}).status_code == 404


# ------------------------------- user confidence: a separate construct

def test_user_confidence_is_separate_from_robustness(client, auth):
    task = client.post("/api/tasks", headers=auth, json={
        "title": "Which elective?", "category": "Study",
        "due_date": _due(3), "estimated_minutes": 45, "importance": 5,
    }).json()

    # Nothing to be confident about yet.
    early = client.patch(f"/api/tasks/{task['id']}/decision/confidence",
                         headers=auth, json={"confidence": 50})
    assert early.status_code == 404

    decided = client.post(f"/api/tasks/{task['id']}/decide", headers=auth, json={
        "options": ["Statistics", "Philosophy"],
        "criteria": [
            {"name": "Interest", "weight": 5, "tag": "interest"},
            {"name": "Career value", "weight": 4, "tag": "importance"},
            {"name": "Workload", "weight": 2, "tag": "effort"},
        ],
        "ratings": {
            "Statistics": {"Interest": 3, "Career value": 5, "Workload": 2},
            "Philosophy": {"Interest": 5, "Career value": 3, "Workload": 4},
        },
        "deliberation_seconds": 42.0,
    })
    assert decided.status_code == 200, decided.text
    result = decided.json()
    assert result["user_confidence"] is None
    robustness = result["assessment"]["recommendation_robustness"]["score"]

    saved = client.patch(f"/api/tasks/{task['id']}/decision/confidence",
                         headers=auth, json={"confidence": 80})
    assert saved.status_code == 200, saved.text
    assert saved.json()["user_confidence"] == 80
    # Recording the self-report never changes the robustness score.
    assert saved.json()["assessment"]["recommendation_robustness"]["score"] == robustness

    again = client.get(f"/api/tasks/{task['id']}/decision", headers=auth).json()
    assert again["user_confidence"] == 80

    assert client.patch(f"/api/tasks/{task['id']}/decision/confidence",
                        headers=auth, json={"confidence": 150}).status_code == 422

    # Satisfaction is the third, independent measurement.
    rated = client.post(f"/api/feedback/decision/{task['id']}", headers=auth, json={
        "outcome": "followed", "satisfaction": 4,
    })
    assert rated.status_code == 200, rated.text
    assert rated.json()["predicted_satisfaction"] is None    # nothing is predicted
    final = client.get(f"/api/tasks/{task['id']}/decision", headers=auth).json()
    assert final["user_confidence"] == 80
    assert final["feedback"]["satisfaction"] == 4


# ---------------------------------------- scoring unit checks

def test_classify_uses_stated_importance():
    neutral = {"urgency": 0.1, "importance": 0.5, "aging": 0.0, "effort": 0.0}
    assert scoring.classify(neutral, 5) == (False, True, "schedule")
    assert scoring.classify(neutral, 1) == (False, False, "eliminate")
    assert scoring.classify(neutral, 3) == (False, False, "eliminate")
    assert scoring.classify(neutral, None) == (False, False, "eliminate")
    assert scoring.classify({**neutral, "importance": 0.75}, None) == (False, True, "schedule")
    # "A little" overrides even a boosted category.
    assert scoring.classify({**neutral, "importance": 0.75}, 1) == (False, False, "eliminate")
    assert scoring.classify({**neutral, "urgency": 0.9}, 5) == (True, True, "do")


def test_stated_importance_mapping():
    assert scoring.stated_importance_value(None) is None
    assert scoring.stated_importance_value(1) == 0.0
    assert scoring.stated_importance_value(3) == 0.5
    assert scoring.stated_importance_value(5) == 1.0


# -------------------------------- tempo: options as a covariate

def test_tempo_reports_raw_seconds_and_option_covariate():
    now = datetime.now(timezone.utc)
    events = []
    for day in range(6):
        for seconds, options in ((30.0, 2), (60.0, 6)):
            events.append(decision_tempo.DecisionEvent(
                at=now - timedelta(days=5 - day), seconds=seconds, options=options,
            ))
    report = decision_tempo.compute_tempo(events, "day")
    assert report["status"] == "ready"
    first = report["series"][0]
    # No Hick-Hyman division: the charted value is the raw median.
    assert first["adjusted_seconds"] == first["median_seconds"] == 45.0
    assert first["median_options"] == 4.0
    cov = report["options_covariate"]
    assert cov["median"] == 4.0 and cov["min"] == 2 and cov["max"] == 6
    assert not hasattr(decision_tempo, "hick_normalise")
