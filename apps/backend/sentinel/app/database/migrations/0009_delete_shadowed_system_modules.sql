-- Remove DB rows whose names are reserved for code-defined system modules.
-- These were seeded by the legacy AraiOS system and are now superseded by
-- ModuleDefinition in app/services/araios/system_modules/.
-- Also adds a DB-level constraint so the conflict can never happen again.

DELETE FROM modules
WHERE name IN (
    'runtime_exec',
    'python',
    'git_exec',
    'str_replace_editor',
    'http_request',
    'browser',
    'memory',
    'sub_agents',
    'telegram',
    'triggers',
    'module_manager',
    'tasks',
    'documents',
    'coordination'
);
