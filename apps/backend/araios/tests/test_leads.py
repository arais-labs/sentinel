def test_create_and_list_leads(client, admin_headers):
    # Create
    resp = client.post(
        "/api/leads",
        json={"name": "Jane Doe", "role": "CTO", "company": "Acme", "status": "draft"},
        headers=admin_headers,
    )
    assert resp.status_code == 201
    lead = resp.json()
    assert lead["name"] == "Jane Doe"
    assert lead["company"] == "Acme"
    assert "id" in lead

    # List
    resp = client.get("/api/leads", headers=admin_headers)
    assert resp.status_code == 200
    leads = resp.json()["leads"]
    assert len(leads) == 1
    assert leads[0]["id"] == lead["id"]


def test_update_lead(client, admin_headers):
    lead = client.post(
        "/api/leads",
        json={"name": "Update Me", "company": "Old"},
        headers=admin_headers,
    ).json()

    resp = client.patch(
        f"/api/leads/{lead['id']}",
        json={"company": "New", "status": "approved"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["company"] == "New"
    assert resp.json()["status"] == "approved"


def test_delete_lead(client, admin_headers):
    lead = client.post(
        "/api/leads",
        json={"name": "Delete Me"},
        headers=admin_headers,
    ).json()

    resp = client.delete(f"/api/leads/{lead['id']}", headers=admin_headers)
    assert resp.status_code == 200

    resp = client.get("/api/leads", headers=admin_headers)
    assert len(resp.json()["leads"]) == 0


def test_update_nonexistent_lead(client, admin_headers):
    resp = client.patch(
        "/api/leads/nonexistent",
        json={"name": "Nope"},
        headers=admin_headers,
    )
    assert resp.status_code == 404


def test_camel_case_fields(client, admin_headers):
    """Frontend sends camelCase, API should accept and return it."""
    lead = client.post(
        "/api/leads",
        json={"name": "Camel", "linkedinUrl": "https://linkedin.com/in/test", "messageDraft": "Hi!"},
        headers=admin_headers,
    ).json()

    assert lead["linkedinUrl"] == "https://linkedin.com/in/test"
    assert lead["messageDraft"] == "Hi!"
