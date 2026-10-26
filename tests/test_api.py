from http import HTTPStatus

TEAM_PAYLOAD = {
    "sport": "football",
    "league": "NFL",
    "name": "New England Patriots",
    "short_name": "NE",
    "external_ref": "nfl-ne"
}


def test_root_returns_warmup(client):
    response = client.get("/")
    assert response.status_code == HTTPStatus.OK
    payload = response.json()
    assert payload["message"].startswith("Sportsbook Assistant")


def test_create_team(client):
    response = client.post("/teams/", json=TEAM_PAYLOAD)
    assert response.status_code == HTTPStatus.OK
    created = response.json()
    assert created["name"] == TEAM_PAYLOAD["name"]
    assert created["id"] > 0


def test_list_teams_includes_created_entries(client):
    client.post("/teams/", json=TEAM_PAYLOAD)
    response = client.get("/teams/")
    assert response.status_code == HTTPStatus.OK
    payload = response.json()
    assert isinstance(payload, list)
    assert any(team["name"] == TEAM_PAYLOAD["name"] for team in payload)
