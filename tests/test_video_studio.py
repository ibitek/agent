"""Storyboards use private Redis drafts and never call a rendering service."""
import base64

from test_cloud_api import client, login

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nframe").decode()


def storyboard():
    return {
        "title": "Northern light",
        "ratio": "9:16",
        "characters": [{"id": "a" * 32, "name": "Ada", "appearance": "Short dark hair",
                        "wardrobe": "Navy coat", "reference": PNG}],
        "scenes": [{"id": "b" * 32, "title": "Arrival", "direction": "Track Ada through the door.",
                    "duration": 5, "frame": PNG, "cast": ["a" * 32]}],
    }


def test_video_storyboard_survives_reload_and_other_chats_are_separate(client):
    url = "/api/video-studio/" + "1" * 32
    assert client.get(url).status_code == 401
    login(client)
    revision = client.get(url).json()["revision"]
    draft = storyboard()
    response = client.put(url, json={"revision": revision, "storyboard": draft})
    assert response.status_code == 200, response.text
    assert response.json() == client.get(url).json()
    assert response.json()["storyboard"] == draft
    assert client.get("/api/video-studio/standalone").json()["storyboard"]["scenes"] == []


def test_video_storyboard_accepts_identical_replay_and_refuses_stale_writer(client):
    login(client)
    url = "/api/video-studio/standalone"
    revision = client.get(url).json()["revision"]
    body = {"revision": revision, "storyboard": storyboard()}
    first = client.put(url, json=body)
    assert first.status_code == 200, first.text
    assert client.put(url, json=body).json() == first.json()
    modified = {**body["storyboard"], "title": "Changed in another tab"}
    assert client.put(url, json={"revision": revision, "storyboard": modified}).status_code == 409
    assert client.get(url).json() == first.json()


def test_storyboard_rejects_dangling_cast_and_remote_reference(client):
    login(client)
    url = "/api/video-studio/standalone"
    start = client.get(url).json()
    draft = storyboard()
    draft["scenes"][0]["cast"] = ["f" * 32]
    assert client.put(url, json={"revision": start["revision"], "storyboard": draft}).status_code == 422
    draft = storyboard()
    draft["characters"][0]["reference"] = "https://example.test/portrait.png"
    assert client.put(url, json={"revision": start["revision"], "storyboard": draft}).status_code == 422
    assert client.get(url).json() == start
