DO $$
BEGIN
    IF to_regclass('public.git_push_approvals') IS NULL THEN
        RETURN;
    END IF;

    INSERT INTO tool_approvals (
        id,
        provider,
        tool_name,
        session_id,
        action,
        description,
        match_key,
        status,
        requested_by,
        decision_by,
        decision_note,
        payload_json,
        result_json,
        expires_at,
        resolved_at,
        created_at,
        updated_at
    )
    SELECT
        g.id,
        'git',
        'git_exec',
        g.session_id,
        'git.write',
        ('Allow write operation: ' || g.command),
        lower(regexp_replace(trim(g.command), '\s+', ' ', 'g')),
        g.status,
        g.requested_by,
        g.decision_by,
        g.decision_note,
        jsonb_build_object(
            'account_id', g.account_id::text,
            'repo_url', g.repo_url,
            'remote_name', g.remote_name,
            'command', g.command
        ),
        g.result_json,
        g.expires_at,
        g.resolved_at,
        g.created_at,
        g.updated_at
    FROM git_push_approvals g
    ON CONFLICT (id) DO NOTHING;

    DROP TABLE IF EXISTS git_push_approvals;
END $$;
