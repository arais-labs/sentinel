def test_crud_competitors(client, admin_headers):
    # Create
    resp = client.post(
        "/api/competitors",
        json={"name": "Rival Inc", "website": "https://rival.com", "category": "direct"},
        headers=admin_headers,
    )
    assert resp.status_code == 201
    comp = resp.json()

    # List
    resp = client.get("/api/competitors", headers=admin_headers)
    assert len(resp.json()["competitors"]) == 1

    # Update with deep merge on pricing
    resp = client.patch(
        f"/api/competitors/{comp['id']}",
        json={"pricing": {"freeTier": True, "startingPrice": "$10/mo"}},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["pricing"]["freeTier"] is True

    # Delete
    resp = client.delete(f"/api/competitors/{comp['id']}", headers=admin_headers)
    assert resp.status_code == 200
