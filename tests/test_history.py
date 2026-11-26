def seed_history(services):
    with services.db.connect(write=True) as db:
        db.execute("INSERT INTO accounts(id,username,name) VALUES('67890','other','Other')")
        for index in range(25):
            uid = f"{index + 1:032x}"
            db.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)",
                (uid, "12345", uid, uid, "completed", 1000 + index // 3, 1, ""),
            )
            db.execute(
                "INSERT INTO run_items VALUES(?,?,?,?)",
                (uid, "1000", "deleted", "Deletion confirmed."),
            )
        db.execute(
            "INSERT INTO runs VALUES(?,?,?,?,?,?,?,?)",
            ("f" * 32, "67890", "other-key", "other", "completed", 9999, 1, ""),
        )


def test_activity_history_is_paged_stable_owner_scoped_and_local(ready, fake):
    client, _, services = ready
    seed_history(services)
    pages = [client.get(f"/v1/runs?page={page}&limit=10").json() for page in (1, 2, 3)]
    assert [len(page["runs"]) for page in pages] == [10, 10, 5]
    assert all(page["total"] == 25 and page["pages"] == 3 for page in pages)
    ids = [run["id"] for page in pages for run in page["runs"]]
    assert len(set(ids)) == 25
    assert ids == [f"{index:032x}" for index in range(25, 0, -1)]
    assert pages[0]["runs"][0]["counts"] == {"deleted": 1}
    assert client.get("/v1/runs?page=999&limit=10").json()["page"] == 3
    assert not fake.calls


def test_history_deep_link_finds_page_and_does_not_leak_other_owner(ready):
    client, _, services = ready
    seed_history(services)
    focused = client.get("/v1/runs?focus=" + f"{1:032x}").json()
    assert focused["page"] == 3
    assert f"{1:032x}" in {run["id"] for run in focused["runs"]}
    assert client.get("/v1/runs?focus=" + "f" * 32).status_code == 404
    for query in ("limit=100", "page=0", "focus=../invalid"):
        assert client.get("/v1/runs?" + query).status_code == 422


def test_app_version_is_v2_and_api_routes_remain_v1(client):
    from app import __version__

    assert __version__ == client.app.version == "2.0.2"
    assert client.get("/v1/session").status_code == 200
    assert client.get("/v2/session").status_code == 404
