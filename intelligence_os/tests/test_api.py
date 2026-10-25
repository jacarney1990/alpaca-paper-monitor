from fastapi.testclient import TestClient
from intelligence_os.main import app

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "intelligence-os"

def test_plan_does_not_execute():
    response = client.post("/research/plan", json={"question": "Compare unemployment trends"})
    assert response.status_code == 200
    body = response.json()
    assert body["execution"] == "not_started"
    assert len(body["stages"]) == 4
    assert body["safety"]["brokerage_execution"] == "disabled"

def test_invalid_question():
    assert client.post("/research/plan", json={"question": "a"}).status_code == 422
