def test_crud_clients(client, admin_headers):
    resp = client.post(
        "/api/clients",
        json={"name": "Alice", "company": "ClientCo", "engagementType": "xcelerator", "phase": "build"},
        headers=admin_headers,
    )
    assert resp.status_code == 201
    cl = resp.json()
    assert cl["engagementType"] == "xcelerator"

    resp = client.patch(
        f"/api/clients/{cl['id']}",
        json={"phase": "deploy", "phaseProgress": 90},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["phase"] == "deploy"

    resp = client.delete(f"/api/clients/{cl['id']}", headers=admin_headers)
    assert resp.status_code == 200
