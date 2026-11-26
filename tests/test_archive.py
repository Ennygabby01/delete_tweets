import json

import pytest


@pytest.mark.parametrize(
    ("source", "display"),
    [
        ("Music &amp; podcasts", "Music & podcasts"),
        ("It&#39;s &quot;ready&quot;", 'It\'s "ready"'),
        ("&#x1F600; &lt;tag&gt;", "😀 <tag>"),
        ("&lt;script&gt;alert(1)&lt;/script&gt;", "<script>alert(1)</script>"),
        ("Keep &amp;amp; literal", "Keep &amp; literal"),
    ],
)
def test_post_text_decodes_once_for_lists_and_run_details_without_rewriting_storage(
    ready, source, display
):
    client, _, services = ready
    services.archive.import_bytes(
        json.dumps([{"tweet": {"id_str": "2000", "full_text": source}}]).encode()
    )
    for _ in range(2):
        posts = client.get("/v1/posts").json()["posts"]
        assert next(p for p in posts if p["id"] == "2000")["text"] == display
    detail = services.runs.preview("12345", ["2000"], False, "", "entity-test-key-123456")
    assert detail["items"][0]["text"] == display
    with services.db.connect() as db:
        assert db.execute("SELECT text FROM posts WHERE id='2000'").fetchone()[0] == source
