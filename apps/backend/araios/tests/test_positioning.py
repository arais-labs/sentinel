def test_get_empty_positioning(client, admin_headers):
    resp = client.get("/api/positioning", headers=admin_headers)
    assert resp.status_code == 200
    # All fields are None when no positioning row exists
    data = resp.json()
    assert data.get("tagline") is None


def test_update_and_get_positioning(client, admin_headers):
    resp = client.patch(
        "/api/positioning",
        json={"tagline": "Build fast", "valueProps": ["Speed", "Quality"]},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["tagline"] == "Build fast"
    assert resp.json()["valueProps"] == ["Speed", "Quality"]

    # Get it back
    resp = client.get("/api/positioning", headers=admin_headers)
    assert resp.json()["tagline"] == "Build fast"


def test_deep_merge_icp(client, admin_headers):
    client.patch(
        "/api/positioning",
        json={"icp": {"primary": "Engineers"}},
        headers=admin_headers,
    )
    resp = client.patch(
        "/api/positioning",
        json={"icp": {"secondary": "Architects"}},
        headers=admin_headers,
    )
    icp = resp.json()["icp"]
    assert icp["primary"] == "Engineers"
    assert icp["secondary"] == "Architects"
