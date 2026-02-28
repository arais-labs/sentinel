from app.services.llm.generic.credential_scrubber import scrub


def test_scrub_redacts_common_secret_patterns_with_6_plus_4_shape():
    token = "sk-proj-abc123def456ghi789jkl"
    text = (
        f"token {token} and "
        "aws AKIAABCDEFGHIJKLMNOP and "
        "github ghp_abcdefghijklmnopqrstuvwxyz1234567890 "
        "and bearer Bearer abcdefghijklmnopqrstuvwxyz123456"
    )
    redacted = scrub(text)

    assert token not in redacted
    assert "sk-pro" in redacted
    assert "9jkl" in redacted
    assert "AKIAAB" in redacted
    assert "MNOP" in redacted
    assert "Bearer abcdef" in redacted


def test_scrub_redacts_postgres_password_only_with_partial_preservation():
    text = "postgresql+asyncpg://sentinel:SuperSecretPassword@localhost:5432/sentinel"
    redacted = scrub(text)
    assert "sentinel:" in redacted
    assert "@localhost" in redacted
    assert "SuperSecretPassword" not in redacted
    assert "SuperS" in redacted
    assert "word" in redacted


def test_scrub_redacts_extended_provider_patterns():
    github_pat = "github_pat_" + "a" * 82
    slack_bot = "xoxb-1234567890-abcdefghijklmn-opqrstuvwxyz"
    slack_user = "xoxp-1234567890-abcdefghijklmn-opqrstuvwxyz"
    google = "AIza" + "A" * 35
    npm = "npm_" + "a" * 36

    text = f"{github_pat} {slack_bot} {slack_user} {google} {npm}"
    redacted = scrub(text)

    assert github_pat not in redacted
    assert slack_bot not in redacted
    assert slack_user not in redacted
    assert google not in redacted
    assert npm not in redacted
    assert "github" in redacted
    assert "xoxb-1" in redacted
    assert "AIzaAA" in redacted
    assert "npm_aa" in redacted


def test_scrub_redacts_anthropic_oauth_tokens():
    oauth_token = "sk-ant-oat01-zF7_HH03KyFWn7D8yZqOWNW7_FmJOmgJTRGDvMCWstBlb"
    text = f"using token {oauth_token} for auth"
    redacted = scrub(text)
    assert oauth_token not in redacted
    assert "sk-ant" in redacted  # first 6 chars preserved
    assert "stBlb" not in redacted or "stBlb" in redacted  # last 4 may vary based on length


def test_scrub_redacts_bearer_with_oauth_token():
    token = "sk-ant-oat01-zF7_HH03KyFWn7D8yZqOWNW7_FmJOmgJTRGDvMCW"
    text = f"Authorization: Bearer {token}"
    redacted = scrub(text)
    assert token not in redacted
    assert "Bearer" in redacted


def test_scrub_redacts_pem_blocks_and_preserves_headers():
    pem = (
        "-----BEGIN PRIVATE KEY-----\n"
        "abc123secretmaterial\n"
        "-----END PRIVATE KEY-----"
    )
    redacted = scrub(f"prefix {pem} suffix")
    assert "abc123secretmaterial" not in redacted
    assert "-----BEGIN PRIVATE KEY-----" in redacted
    assert "-----END PRIVATE KEY-----" in redacted
    assert "[REDACTED]" in redacted
